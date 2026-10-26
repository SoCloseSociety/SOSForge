package co.soclose.sosforge.core.alert

import android.content.Context
import co.soclose.sosforge.core.Prefs
import co.soclose.sosforge.core.feed.FeedChange
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.net.SosJson
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File
import java.time.LocalTime

/** What Settings shows about alerting. */
data class AlertStatus(val held: Int = 0, val blocked: Int = 0, val posted: Int = 0, val lastPostAt: Long = 0L)

/**
 * Glue: feed changes -> [Ledger] (pure rules) -> [Notifier] (Android). The ledger is written to disk
 * at once whenever an entry changed (a post, a hold, a cancel), so a process killed in the night still
 * remembers what it showed; a change of the "seen" memory alone is written at most every 15 s.
 */
class AlertEngine(ctx: Context, private val prefs: Prefs, private val notifier: Notifier) {
    private val file = File(ctx.filesDir, "ledger.json")
    private val mutex = Mutex()
    private val ledger = Ledger(load())
    private var lastRelease = 0L
    private var lastSave = 0L
    private val _status = MutableStateFlow(status())
    val status: StateFlow<AlertStatus> = _status.asStateFlow()

    private fun load(): LedgerState =
        runCatching { SosJson.decodeFromString(LedgerState.serializer(), file.readText()) }.getOrDefault(LedgerState())

    private fun save(before: LedgerState, deviceNow: Long) {
        val now = ledger.state
        if (now == before) return
        val urgent = now.entries != before.entries || now.retired != before.retired || now.historyDone != before.historyDone
        if (!urgent && deviceNow - lastSave < 15_000L) return
        lastSave = deviceNow
        runCatching {
            val tmp = File(file.parentFile, file.name + ".tmp")
            tmp.writeText(SosJson.encodeToString(LedgerState.serializer(), now))
            tmp.renameTo(file)
        }
    }

    private fun status() = AlertStatus(
        held = ledger.heldCount,
        blocked = ledger.state.blocked,
        posted = ledger.state.entries.values.count { it.state == Entry.State.POSTED },
        lastPostAt = ledger.state.entries.values.filter { it.state == Entry.State.POSTED }.maxOfOrNull { it.at } ?: 0L,
    )

    fun context(feed: FeedState, deviceNow: Long): AlertContext {
        val now = LocalTime.now()
        return AlertContext(
            places = prefs.places.value,
            prefs = prefs.alerts.value,
            serverNow = feed.serverNow(deviceNow),
            deviceNow = deviceNow,
            quiet = prefs.quiet.value.active(now.hour * 60 + now.minute),
        )
    }

    /** [fullRead]: the batch carried a snapshot or a full poll (the first one ever is history). */
    suspend fun onChanges(changes: List<FeedChange>, feed: FeedState, deviceNow: Long, fullRead: Boolean, complete: Boolean = true) {
        if (changes.isEmpty() && !fullRead) return
        mutex.withLock {
            val before = ledger.state
            val ctx = context(feed, deviceNow)
            run(ledger.onChanges(changes, ctx, fullRead, complete), ctx)
            save(before, deviceNow)
        }
    }

    /** Release what quiet hours held, at most once a minute (called on every batch and by the worker). */
    suspend fun releaseIfDue(feed: FeedState, deviceNow: Long, force: Boolean = false) {
        if (!force && deviceNow - lastRelease < 60_000L) return
        lastRelease = deviceNow
        mutex.withLock {
            if (ledger.heldCount == 0) return
            val ctx = context(feed, deviceNow)
            val before = ledger.state
            run(ledger.release(ctx) { id -> feed.byId(id) }, ctx)
            save(before, deviceNow)
        }
    }

    /** Posted alerts whose event is not in [present]: candidates to verify with the server. */
    suspend fun postedMissing(present: Set<String>): List<Entry> = mutex.withLock { ledger.postedMissing(present) }

    suspend fun prune(deviceNow: Long) {
        mutex.withLock {
            val before = ledger.state
            ledger.prune(deviceNow)
            save(before, deviceNow)
            _status.value = status()
        }
    }

    private fun run(effects: List<Effect>, ctx: AlertContext) {
        var blocked = 0
        var digestTouched = false
        for (fx in effects) when (fx) {
            is Effect.Show -> {
                if (!notifier.show(fx, ctx.serverNow)) blocked++
                if (fx.match.level == Level.DIGEST) digestTouched = true
            }
            is Effect.Remove -> {
                notifier.cancel(fx.notifId)
                digestTouched = true
            }
        }
        ledger.countBlocked(blocked)
        if (digestTouched) {
            notifier.digestSummary(ledger.state.entries.values.count { it.state == Entry.State.POSTED && it.level == Level.DIGEST })
        }
        _status.value = status()
    }
}
