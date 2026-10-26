package co.soclose.sosforge.core.watch

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Prefs
import co.soclose.sosforge.core.live.Link
import co.soclose.sosforge.core.net.ApiError
import co.soclose.sosforge.core.net.ApiException
import co.soclose.sosforge.core.net.ApiVersion
import co.soclose.sosforge.core.net.Iso
import co.soclose.sosforge.core.net.Scope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The floor of background alerting: every 15 min (Android's minimum, with flex), whatever the Watch
 * service is doing. When the socket is live it does nothing (the socket already heard everything).
 * Otherwise it reads `/api/events` once and folds it in through the SAME alert engine:
 *  - lane B server (`events_cursor` + `events_zone`): the owner's zones, a cursor, an ETag -> a 304
 *    costs nothing when nothing changed, and withdrawn ids come back in `removed`;
 *  - legacy server: the latest 300 events, filtered on the phone.
 * The freshness rule ([co.soclose.sosforge.core.alert.AlertRules.isNews]) keeps a late poll from
 * announcing old events as new.
 */
class WatchWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext.app
        val prefs = app.prefs
        val now = System.currentTimeMillis()
        // the live Watch was wanted but is gone: try to bring it back (allowed when battery-exempt)
        if (prefs.watchLive.value && !app.watchRunning.value) app.watch.startService()
        if (prefs.places.value.isEmpty()) return done(prefs, "no places")
        if (app.link(now) == Link.LIVE) {
            app.alerts.releaseIfDue(app.feed.value, now, force = true)
            app.alerts.prune(now)
            return done(prefs, "socket live")
        }
        return try {
            val version = app.versionOnce() ?: return done(prefs, "unreachable (version)")
            val api = app.api()
            val scope = Scope.of(prefs.places.value, prefs.alerts.value)
            if (version.incremental && scope != null) {
                val cursor = prefs.getString(Prefs.K_POLL_CURSOR) ?: ""
                val etag = prefs.getString(Prefs.K_POLL_ETAG)
                val read = withContext(Dispatchers.IO) { api.events(version, scope, cursor, etag) }
                if (read.notModified) {
                    app.alerts.releaseIfDue(app.feed.value, now, force = true)
                    return done(prefs, "304 not modified")
                }
                val r = read.reply!!
                val full = cursor.isEmpty() || r.reset
                // a zoned read never REPLACES the world feed the screens show: it is merged
                app.applyPoll(r.events, r.removed, Iso.ms(r.serverTime), replace = false, fullRead = full)
                // an empty delta returns our own cursor; a delta too big comes back as a full read (reset)
                prefs.putString(Prefs.K_POLL_CURSOR, r.cursor ?: "")
                prefs.putString(Prefs.K_POLL_ETAG, read.etag)
                done(prefs, "zoned ${r.events.size} / removed ${r.removed.size}" + if (r.reset) " (reset: ${r.resetReason ?: "?"})" else "")
            } else {
                val read = withContext(Dispatchers.IO) { api.events(ApiVersion.NONE, null, null, null) }
                val r = read.reply!!
                app.applyPoll(r.events, emptyList(), Iso.ms(r.serverTime), replace = true, fullRead = true)
                done(prefs, "legacy ${r.events.size}")
            }
        } catch (e: ApiException) {
            // 400 bad_since: a cursor this server cannot read any more; the next run does a full read
            if ((e.error as? ApiError.Refused)?.code == 400) {
                prefs.putString(Prefs.K_POLL_CURSOR, "")
                prefs.putString(Prefs.K_POLL_ETAG, null)
            }
            done(prefs, when (val err = e.error) {
                ApiError.Unreachable -> "unreachable"
                is ApiError.Refused -> "refused ${err.code}"
                ApiError.Unreadable -> "unreadable"
            })
        }.also { app.alerts.prune(now) }
    }

    private fun done(prefs: Prefs, what: String): Result {
        prefs.putLong(Prefs.K_POLL_AT, System.currentTimeMillis())
        prefs.putString(Prefs.K_POLL_RESULT, what)
        return Result.success()
    }
}
