package co.soclose.sosforge.core.watch

import android.app.Notification
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import co.soclose.sosforge.MainActivity
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Prefs
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.alert.Notifier
import co.soclose.sosforge.core.live.Link
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * The Watch: a foreground service whose only job is to keep this process, and so the ONE socket of
 * [co.soclose.sosforge.SosApp], alive while the app is off screen. Its notification (silent channel)
 * says the truth every few seconds: live, reconnecting, or offline -- never "watching" over a dead socket.
 */
class WatchService : LifecycleService() {
    private var shown: String? = null

    override fun onCreate() {
        super.onCreate()
        goForeground(text(Link.CONNECTING))
        app.watchRunning.value = true
        app.prefs.putLong(Prefs.K_WATCH_STARTED, System.currentTimeMillis())
        app.updateLiveDemand()
        lifecycleScope.launch {
            while (isActive) {
                val t = text(app.link(System.currentTimeMillis()))
                if (t != shown) goForeground(t)
                delay(3_000L)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            app.prefs.setWatchLive(false)
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    /** Android 15 calls this when a time-limited type runs out. `specialUse` has no limit; stop cleanly anyway. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        app.prefs.putString(Prefs.K_WATCH_ERROR, "timeout")
        stopSelf()
    }

    override fun onDestroy() {
        app.watchRunning.value = false
        app.updateLiveDemand()
        super.onDestroy()
    }

    private fun text(link: Link): String {
        val c = Words.localized(this)
        val n = app.prefs.places.value.size
        val places = c.resources.getQuantityString(R.plurals.watch_places, n, n)
        val state = c.getString(
            when (link) {
                Link.LIVE -> R.string.watch_state_live
                Link.CONNECTING -> R.string.watch_state_connecting
                Link.RECONNECTING -> R.string.watch_state_retrying
                Link.BUSY -> R.string.watch_state_busy
                Link.OFFLINE -> R.string.watch_state_offline
            },
        )
        return "$places · $state"
    }

    private fun goForeground(text: String) {
        shown = text
        val c = Words.localized(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, WatchService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        val n: Notification = NotificationCompat.Builder(this, Notifier.CH_WATCH)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(c.getString(R.string.watch_notif_title))
            .setContentText(text)
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setContentIntent(open)
            .addAction(0, c.getString(R.string.watch_stop), stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, Notifier.WATCH_ID, n, type)
    }

    companion object {
        const val ACTION_STOP = "co.soclose.sosforge.watch.STOP"
    }
}
