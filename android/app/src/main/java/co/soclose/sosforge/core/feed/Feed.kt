package co.soclose.sosforge.core.feed

import co.soclose.sosforge.core.live.ServerMessage
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.core.net.SourceHealth
import co.soclose.sosforge.core.net.Stats

/**
 * What the app knows about the world right now. Immutable; [FeedReducer] makes the next one.
 *
 * Every age on screen is computed on the SERVER clock: [serverNow] = device time + [clockSkewMs],
 * re-synced on every snapshot and tick (frontend/src/store.ts `clockSkew`).
 */
data class FeedState(
    /** Sorted by EVENT time, newest first (lesson 5: never by arrival). */
    val events: List<SosEvent> = emptyList(),
    val stats: Stats? = null,
    val sources: List<SourceHealth> = emptyList(),
    val clients: Int? = null,
    val clockSkewMs: Long = 0L,
    /** Device time of the last message of any kind (0 = none yet). */
    val lastMessageAt: Long = 0L,
    /** Server time the current data describes (the last snapshot, tick or poll). */
    val dataServerTime: Long? = null,
    /** True while what is shown came from the disk cache: never LIVE. */
    val fromCache: Boolean = false,
    /** Device time of the disk copy being shown (when [fromCache]). */
    val cacheSavedAt: Long? = null,
    /** cluster_id -> the sources that published a solution for it (the "also reported by" line). */
    val clusterSources: Map<String, Set<String>> = emptyMap(),
    /** What a zoned snapshot could not place by distance, null when the server did not say. */
    val unplaced: co.soclose.sosforge.core.net.Unplaced? = null,
    /** Chunks of a split snapshot still to come: while > 0 the picture is NOT complete ("nothing near you" is not true yet). */
    val snapshotRemaining: Int = 0,
) {
    fun serverNow(deviceNow: Long): Long = deviceNow + clockSkewMs
    fun byId(id: String): SosEvent? = events.firstOrNull { it.id == id }
}

/** One change the alert engine must hear about. */
sealed interface FeedChange {
    /**
     * An event that is (still) in the feed. [fromSnapshot]: re-sent by a (re)connection snapshot or a
     * full poll. [breaking] / [escalated]: the server's own "this is news" (pipeline.py, lane F).
     */
    data class Upserted(
        val event: SosEvent,
        val previous: SosEvent?,
        val fromSnapshot: Boolean,
        val breaking: Boolean,
        val escalated: Boolean = false,
    ) : FeedChange

    /** An event the server withdrew (stale, cancelled, lifted, out_of_scope), or a CAP all-clear. */
    data class Removed(val id: String, val reason: String) : FeedChange
}

data class Reduced(val state: FeedState, val changes: List<FeedChange>)

/** Is this event a RUNNING alert right now (ongoing and not past its own `expires`)? */
fun SosEvent.isCurrentAlert(serverNow: Long): Boolean = ongoing && !isEnded(serverNow)

/** Past its own `expires`: not current, never notified, shown as ended. */
fun SosEvent.isEnded(serverNow: Long): Boolean = expiresMs?.let { it <= serverNow } == true

/**
 * The store's logic (frontend/src/store.ts `ingest`), pure: one message in, the next state and the
 * changes out. Unit-tested against the verbatim websocket capture.
 */
object FeedReducer {
    const val MAX_EVENTS = 1000

    /** Newest event time first; an unreadable time sinks to the bottom rather than posing as "now". */
    val byEventTime: Comparator<SosEvent> = compareByDescending<SosEvent> { it.timeMs ?: Long.MIN_VALUE }.thenBy { it.id }

    /**
     * The count cut. A running alert carries the time it was ISSUED, often days old: a cut by count or
     * by age on event time would drop it first (the web lost 264 of 346 running alerts that way). So
     * every current alert is kept, and only point-in-time (or ended) events are cut, oldest first.
     */
    fun trim(sorted: List<SosEvent>, serverNow: Long, max: Int = MAX_EVENTS): List<SosEvent> {
        if (sorted.size <= max) return sorted
        val running = sorted.filter { it.isCurrentAlert(serverNow) }
        val room = (max - running.size).coerceAtLeast(0)
        val rest = sorted.filter { !it.isCurrentAlert(serverNow) }.take(room)
        return (running + rest).sortedWith(byEventTime)
    }

    fun reduce(state: FeedState, msg: ServerMessage, deviceNow: Long): Reduced = when (msg) {
        is ServerMessage.Snapshot -> {
            val skew = msg.serverTimeMs?.let { it - deviceNow } ?: state.clockSkewMs
            val events = trim(msg.events.filter { !it.isAllClear }.distinctBy { it.id }.sortedWith(byEventTime), deviceNow + skew)
            val before = state.events.associateBy { it.id }
            val changes = events.map { FeedChange.Upserted(it, before[it.id], fromSnapshot = true, breaking = false) }
            Reduced(
                state.copy(
                    events = events,
                    stats = msg.stats ?: state.stats,
                    sources = msg.sources.ifEmpty { state.sources },
                    clockSkewMs = skew,
                    lastMessageAt = deviceNow,
                    dataServerTime = msg.serverTimeMs ?: state.dataServerTime,
                    fromCache = false,
                    cacheSavedAt = null,
                    unplaced = msg.unplaced,
                    snapshotRemaining = msg.more.coerceAtLeast(0),
                ),
                changes,
            )
        }
        is ServerMessage.SnapshotMore -> {
            // a chunk of the same snapshot: merged, never replacing what the first part brought
            val skew = msg.serverTimeMs?.let { it - deviceNow } ?: state.clockSkewMs
            val incoming = msg.events.filter { !it.isAllClear }.distinctBy { it.id }
            val ids = incoming.map { it.id }.toSet()
            val before = state.events.associateBy { it.id }
            val events = trim((state.events.filter { it.id !in ids } + incoming).sortedWith(byEventTime), deviceNow + skew)
            Reduced(
                state.copy(
                    events = events,
                    clockSkewMs = skew,
                    lastMessageAt = deviceNow,
                    dataServerTime = msg.serverTimeMs ?: state.dataServerTime,
                    snapshotRemaining = msg.remaining.coerceAtLeast(0),
                ),
                incoming.map { FeedChange.Upserted(it, before[it.id], fromSnapshot = true, breaking = false) },
            )
        }
        is ServerMessage.Tick -> Reduced(
            state.copy(
                stats = msg.stats ?: state.stats,
                sources = msg.sources.ifEmpty { state.sources },
                clients = msg.clients ?: state.clients,
                clockSkewMs = msg.serverTimeMs?.let { it - deviceNow } ?: state.clockSkewMs,
                lastMessageAt = deviceNow,
                dataServerTime = msg.serverTimeMs ?: state.dataServerTime,
            ),
            emptyList(),
        )
        is ServerMessage.Purge -> {
            val gone = msg.ids.toSet()
            Reduced(
                state.copy(events = state.events.filter { it.id !in gone }, lastMessageAt = deviceNow),
                // every id is reported, held in the list or not: a notification may outlive the list's copy
                msg.ids.distinct().map { FeedChange.Removed(it, msg.reason) },
            )
        }
        is ServerMessage.Upsert -> upsert(state, msg, deviceNow)
        is ServerMessage.Refused, is ServerMessage.Unknown -> Reduced(state.copy(lastMessageAt = deviceNow), emptyList())
    }

    private fun upsert(state: FeedState, msg: ServerMessage.Upsert, deviceNow: Long): Reduced {
        val e = msg.event
        val clusters = e.clusterId?.takeIf { it.isNotBlank() }?.let { cid ->
            state.clusterSources + (cid to (state.clusterSources[cid].orEmpty() + e.source))
        } ?: state.clusterSources
        val previous = state.events.firstOrNull { it.id == e.id }
        if (!msg.primary) {
            // A secondary solution of an event already shown (USGS for an EMSC quake): it is credited on
            // the cluster, never listed twice. If WE held it as primary, it has just been superseded:
            // the new representative arrives as its own message, so the old copy leaves the list.
            val events = if (previous != null) state.events.filter { it.id != e.id } else state.events
            return Reduced(state.copy(events = events, clusterSources = clusters, lastMessageAt = deviceNow), emptyList())
        }
        if (e.isAllClear) {
            return Reduced(
                state.copy(events = state.events.filter { it.id != e.id }, clusterSources = clusters, lastMessageAt = deviceNow),
                listOf(FeedChange.Removed(e.id, "lifted")),
            )
        }
        // A revision never travels back in time: an older revision arriving late is ignored.
        if (previous != null && e.revision < previous.revision) {
            return Reduced(state.copy(lastMessageAt = deviceNow, clusterSources = clusters), emptyList())
        }
        val events = trim((state.events.filter { it.id != e.id } + e).sortedWith(byEventTime), state.serverNow(deviceNow))
        return Reduced(
            state.copy(events = events, clusterSources = clusters, lastMessageAt = deviceNow),
            listOf(FeedChange.Upserted(e, previous, fromSnapshot = false, breaking = msg.breaking, escalated = msg.escalated)),
        )
    }

    /**
     * A REST read (the poll fallback) folded in. [full] REPLACES the list (a legacy world read, like a
     * snapshot); otherwise each event is merged and [removed] ids are withdrawn (a zoned or delta read
     * must never shrink the world feed the screens show to the owner's zones).
     */
    fun fromPoll(state: FeedState, events: List<SosEvent>, removed: List<String>, serverTimeMs: Long?, full: Boolean, deviceNow: Long): Reduced {
        if (full) {
            val r = reduce(state, ServerMessage.Snapshot(serverTimeMs, events, null, emptyList(), 0, null), deviceNow)
            // a poll is not a live message: it must not make the socket look alive
            return r.copy(state = r.state.copy(lastMessageAt = state.lastMessageAt))
        }
        var s = state.copy(clockSkewMs = serverTimeMs?.let { it - deviceNow } ?: state.clockSkewMs)
        val changes = mutableListOf<FeedChange>()
        if (removed.isNotEmpty()) {
            val r = reduce(s, ServerMessage.Purge(removed, "stale"), deviceNow)
            s = r.state
            changes += r.changes
        }
        for (e in events) {
            val r = reduce(s, ServerMessage.Upsert(e, isNew = s.byId(e.id) == null, primary = true, breaking = false), deviceNow)
            s = r.state
            // a polled event is re-sent state, like a snapshot's: the ledger decides with the age rule
            changes += r.changes.map { if (it is FeedChange.Upserted) it.copy(fromSnapshot = true) else it }
        }
        s = s.copy(lastMessageAt = state.lastMessageAt, dataServerTime = serverTimeMs ?: s.dataServerTime, fromCache = false)
        return Reduced(s, changes)
    }
}
