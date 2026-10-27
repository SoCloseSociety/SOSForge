package co.soclose.sosforge.core.watch

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import co.soclose.sosforge.SosApp
import co.soclose.sosforge.core.Prefs
import java.util.concurrent.TimeUnit

/** What the app can say, plainly, about background watching. */
enum class WatchMode {
    /** The foreground service holds the socket: alerts within seconds. */
    LIVE,
    /** No service: a check every 15 min (alerts may be up to ~15 min late, Android decides exactly when). */
    PERIODIC,
    /** The owner asked for the live watch but it is NOT running (refused by Android, killed, stopped). */
    LIVE_NOT_RUNNING,
    /** No places: nothing to watch. */
    OFF,
}

/** Pure: which mode the screen must announce. */
object WatchRules {
    fun mode(hasPlaces: Boolean, wantsLive: Boolean, serviceRunning: Boolean): WatchMode = when {
        !hasPlaces -> WatchMode.OFF
        wantsLive && serviceRunning -> WatchMode.LIVE
        wantsLive -> WatchMode.LIVE_NOT_RUNNING
        else -> WatchMode.PERIODIC
    }
}

/**
 * The background watch, two layers:
 *  1. the opt-in Watch, a foreground service of type `specialUse` holding the socket (README:
 *     "Foreground service type" for why not `dataSync`);
 *  2. a WorkManager periodic check every 15 min whenever at least one place exists: the floor that
 *     keeps alerting when the service is off, refused, or killed by the system.
 */
class Watch(private val app: SosApp) {

    fun reconcile() {
        val places = app.prefs.places.value
        // WorkManager initialises itself through androidx.startup; if that ever failed, the live watch
        // must still be reconciled rather than the whole call throwing
        val wm = runCatching { WorkManager.getInstance(app) }.getOrNull() ?: run {
            if (places.isNotEmpty() && app.prefs.watchLive.value && !app.watchRunning.value) startService()
            return
        }
        if (places.isEmpty()) {
            wm.cancelUniqueWork(WORK)
            stopService()
            return
        }
        val req = PeriodicWorkRequestBuilder<WatchWorker>(15, TimeUnit.MINUTES, 5, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, req)
        if (app.prefs.watchLive.value && !app.watchRunning.value) startService()
    }

    fun setLive(on: Boolean): Boolean {
        app.prefs.setWatchLive(on)
        return if (on) startService() else {
            stopService()
            true
        }
    }

    /**
     * Starts the service. Android refuses a foreground start from the background on 12+ unless an
     * exemption applies (boot, the battery-optimisation allowlist, a visible activity): the refusal is
     * caught, recorded, and Settings says "Live watch is not running".
     */
    fun startService(): Boolean = try {
        ContextCompat.startForegroundService(app, Intent(app, WatchService::class.java))
        app.prefs.putString(Prefs.K_WATCH_ERROR, null)
        true
    } catch (e: Exception) {
        // ForegroundServiceStartNotAllowedException (API 31+) or an IllegalStateException before it
        app.prefs.putString(Prefs.K_WATCH_ERROR, e.javaClass.simpleName)
        false
    }

    fun stopService() {
        app.stopService(Intent(app, WatchService::class.java))
    }

    fun batteryExempt(): Boolean {
        val pm = app.getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isIgnoringBatteryOptimizations(app.packageName)
    }

    companion object {
        const val WORK = "sos-watch-poll"
        val CAN_ASK_BATTERY = Build.VERSION.SDK_INT >= 23
    }
}
