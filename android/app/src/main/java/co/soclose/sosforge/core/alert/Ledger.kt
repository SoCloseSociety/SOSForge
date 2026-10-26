package co.soclose.sosforge.core.alert

import co.soclose.sosforge.core.feed.FeedChange
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.SosEvent
import kotlinx.serialization.Serializable

/** The persisted memory of what was notified: survives a process death, so nothing rings twice. */
@Serializable
data class LedgerState(
    val entries: Map<String, Entry> = emptyMap(),
    /** Keys cancelled, dropped or aged out: never notified again (bounded ring, oldest first). */
    val retired: List<String> = emptyList(),
    /** Events seen near a place, notified or not (bounded, insertion order). */
    val seen: Map<String, Seen> = emptyMap(),
    /** The first full read after install has been taken as history. */
    val historyDone: Boolean = false,
    /** Alerts decided while notifications were blocked (the honest count Settings shows). */
    val blocked: Int = 0,
)

/** What the Android side must do. */
sealed interface Effect {
    data class Show(val entry: Entry, val event: SosEvent, val match: Match, val alert: Boolean) : Effect
    data class Remove(val notifId: Int, val key: String) : Effect
}

/** Everything the rules need about "now". */
data class AlertContext(
    val places: List<Place>,
    val prefs: AlertPrefs,
    val serverNow: Long,
    val deviceNow: Long,
    val quiet: Boolean,
)

/**
 * The ledger as a pure state machine: changes in, effects out (LedgerTest). [AlertRules] decides each
 * case; this class keeps the memory those decisions need, by cluster key.
 */
class Ledger(initial: LedgerState = LedgerState()) {
    var state: LedgerState = initial
        private set

    val heldCount: Int get() = state.entries.values.count { it.state == Entry.State.HELD }

    private fun retire(s: LedgerState, key: String): LedgerState {
        val ring = (s.retired.filter { it != key } + key).takeLast(RETIRED_MAX)
        return s.copy(entries = s.entries - key, retired = ring)
    }

    private fun remember(key: String, seen: Seen) {
        val m = LinkedHashMap(state.seen)
        m.remove(key)
        m[key] = seen
        while (m.size > SEEN_MAX) m.remove(m.keys.first())
        state = state.copy(seen = m)
    }

    /**
     * [fullRead]: this batch carries a snapshot (or one of its chunks) or a full poll. The first such
     * read after install is history; every one after it is judged id by id. [complete]: the read is
     * whole (a split snapshot's last chunk); history spans every chunk of the first snapshot.
     */
    fun onChanges(changes: List<FeedChange>, ctx: AlertContext, fullRead: Boolean, complete: Boolean = true): List<Effect> {
        val historyNow = fullRead && !state.historyDone
        val out = mutableListOf<Effect>()
        for (c in changes) when (c) {
            is FeedChange.Upserted -> out += onUpsert(c.event, ctx, Signal(c.breaking, c.escalated), history = historyNow && c.fromSnapshot)
            is FeedChange.Removed -> out += onRemoved(c.id)
        }
        if (fullRead && complete && !state.historyDone) state = state.copy(historyDone = true)
        return out
    }

    private fun onUpsert(e: SosEvent, ctx: AlertContext, signal: Signal, history: Boolean): List<Effect> {
        val key = e.clusterKey
        val entry = state.entries[key]
        val match = AlertRules.match(e, ctx.places, ctx.prefs)
        val d = AlertRules.decide(
            entry, e, match, ctx.serverNow, signal,
            quiet = ctx.quiet,
            retired = key in state.retired,
            seen = state.seen[key],
            history = history,
        )
        val fx = apply(d, key, entry, e, match, ctx)
        if (AlertRules.isNear(e, ctx.places)) remember(key, Seen(AlertRules.rankOf(e), matched = match != null))
        return fx
    }

    private fun apply(d: Decision, key: String, entry: Entry?, e: SosEvent, match: Match?, ctx: AlertContext): List<Effect> =
        when (d) {
            Decision.Nothing -> emptyList()
            Decision.Cancel -> {
                state = retire(state, key)
                listOfNotNull(entry?.let { Effect.Remove(it.notifId, key) })
            }
            Decision.DropHeld -> {
                state = retire(state, key)
                emptyList()
            }
            is Decision.Hold -> {
                val m = match!!
                val next = Entry(
                    key, e.id, e.revision, d.level, Entry.State.HELD, entry?.notifId ?: AlertRules.notifIdFor(key),
                    ctx.deviceNow, m.place.name, severity = maxOf(entry?.severity ?: 0, AlertRules.rankOf(e)),
                )
                state = state.copy(entries = state.entries + (key to next))
                emptyList()
            }
            is Decision.Post -> {
                val m = match!!
                if (AlertRules.dropsHeldOnPost(d.level, ctx.quiet)) {
                    state.entries.values.filter { it.state == Entry.State.HELD && it.key != key }.forEach { state = retire(state, it.key) }
                }
                // A silent post is a revision at or below the rank already rung, or an escalation that
                // quiet hours silenced (a SEVERE at 3 am): either way that rank now counts as rung.
                val next = Entry(
                    key, e.id, e.revision, d.level, Entry.State.POSTED, entry?.notifId ?: AlertRules.notifIdFor(key),
                    entry?.at ?: ctx.deviceNow, m.place.name, severity = maxOf(entry?.severity ?: 0, AlertRules.rankOf(e)),
                )
                state = state.copy(entries = state.entries + (key to next))
                listOf(Effect.Show(next, e, m, d.alert))
            }
        }

    private fun onRemoved(eventId: String): List<Effect> {
        // by the id the entry currently SHOWS: a purge of a secondary solution leaves the cluster alone
        val entry = state.entries.values.firstOrNull { it.eventId == eventId } ?: return emptyList()
        return when (AlertRules.decideRemoved(entry)) {
            Decision.Cancel -> {
                state = retire(state, entry.key)
                listOf(Effect.Remove(entry.notifId, entry.key))
            }
            Decision.DropHeld -> {
                state = retire(state, entry.key)
                emptyList()
            }
            else -> emptyList()
        }
    }

    /**
     * Quiet hours are over: every held entry whose event is still in the feed, still concerns a place,
     * and has not ended, is posted (with sound: the night is over). The rest is dropped.
     */
    fun release(ctx: AlertContext, lookup: (String) -> SosEvent?): List<Effect> {
        if (ctx.quiet) return emptyList()
        val out = mutableListOf<Effect>()
        for (h in state.entries.values.filter { it.state == Entry.State.HELD }) {
            val e = lookup(h.eventId)
            val m = e?.let { AlertRules.match(it, ctx.places, ctx.prefs) }
            if (e == null || m == null || e.isEnded(ctx.serverNow)) {
                state = retire(state, h.key)
                continue
            }
            val next = h.copy(eventId = e.id, revision = e.revision, level = m.level, state = Entry.State.POSTED, severity = maxOf(h.severity, AlertRules.rankOf(e)))
            state = state.copy(entries = state.entries + (h.key to next))
            out += Effect.Show(next, e, m, alert = true)
        }
        return out
    }

    /** The posted entries whose event is not in [present] (to verify against the server, never guessed). */
    fun postedMissing(present: Set<String>): List<Entry> =
        state.entries.values.filter { it.state == Entry.State.POSTED && it.eventId !in present }

    /** Forget entries older than [maxAgeMs], retiring their key so an old running alert never rings again. */
    fun prune(deviceNow: Long, maxAgeMs: Long = PRUNE_MS) {
        state.entries.values.filter { deviceNow - it.at > maxAgeMs }.forEach { state = retire(state, it.key) }
    }

    fun countBlocked(n: Int) {
        if (n > 0) state = state.copy(blocked = state.blocked + n)
    }

    companion object {
        const val RETIRED_MAX = 2000
        const val SEEN_MAX = 3000
        const val PRUNE_MS = 7L * 24 * 3_600_000L
    }
}
