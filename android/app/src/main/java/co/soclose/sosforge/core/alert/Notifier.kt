package co.soclose.sosforge.core.alert

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import co.soclose.sosforge.MainActivity
import co.soclose.sosforge.R
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.ui.theme.SeverityFill

/**
 * The Android half of alerting: four channels, and posting / updating / cancelling what the [Ledger]
 * decided. A missing POST_NOTIFICATIONS on Android 13+ is a SILENT no-op of the platform (AdsForge
 * lesson): every post checks it first and reports a refusal, so Settings can say how many alerts the
 * owner did not see.
 */
class Notifier(private val app: Context) {
    private val nm = NotificationManagerCompat.from(app)

    fun createChannels() {
        val c = Words.localized(app)
        val alarm = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM) ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val extreme = NotificationChannel(CH_EXTREME, c.getString(R.string.ch_extreme_name), NotificationManager.IMPORTANCE_HIGH).apply {
            description = c.getString(R.string.ch_extreme_desc)
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 600, 250, 600, 250, 600)
            setSound(alarm, AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
            enableLights(true)
            lightColor = Color.RED
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        val severe = NotificationChannel(CH_SEVERE, c.getString(R.string.ch_severe_name), NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = c.getString(R.string.ch_severe_desc)
            enableVibration(true)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        val digest = NotificationChannel(CH_DIGEST, c.getString(R.string.ch_digest_name), NotificationManager.IMPORTANCE_LOW).apply {
            description = c.getString(R.string.ch_digest_desc)
        }
        val watch = NotificationChannel(CH_WATCH, c.getString(R.string.ch_watch_name), NotificationManager.IMPORTANCE_LOW).apply {
            description = c.getString(R.string.ch_watch_desc)
            setShowBadge(false)
        }
        nm.createNotificationChannels(listOf(extreme, severe, digest, watch))
    }

    /** Can a notification actually reach the screen right now? */
    fun canPost(): Boolean {
        if (!nm.areNotificationsEnabled()) return false
        return Build.VERSION.SDK_INT < 33 ||
            ActivityCompat.checkSelfPermission(app, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
    }

    /** Is this channel muted by the owner in the system settings? */
    fun channelImportance(id: String): Int? = nm.getNotificationChannel(id)?.importance

    fun channelFor(level: Level): String = when (level) {
        Level.EXTREME -> CH_EXTREME
        Level.SEVERE -> CH_SEVERE
        Level.DIGEST -> CH_DIGEST
    }

    /** Posts (or updates in place) one alert. False when the platform would have dropped it silently. */
    fun show(fx: Effect.Show, serverNow: Long): Boolean {
        val c = Words.localized(app)
        val e = fx.event
        val sev = c.getString(Words.severity(if (co.soclose.sosforge.core.geo.Geo.isTsunami(e)) co.soclose.sosforge.core.net.Severity.EXTREME else e.severityOf))
        val kind = c.getString(Words.kind(e.kindOf))
        val title = c.getString(R.string.notif_title, sev, kind)
        val parts = buildList {
            e.magnitude?.let { add("M" + Fmt.magnitude(it)) }
            if (fx.match.byCountry) {
                val country = Fmt.country(e.countryCode, c.resources.configuration.locales[0]) ?: e.countryCode.orEmpty()
                add(c.getString(R.string.notif_in_country, country, fx.match.place.name))
            } else {
                add(c.getString(R.string.notif_distance, Fmt.km(c, fx.match.distanceKm), fx.match.place.name))
            }
        }
        val line = parts.joinToString(" · ")
        val long = buildString {
            append(line).append('\n').append(e.displayPlace)
            Words.response(e.responseType)?.let { append('\n').append(c.getString(it)) }
            e.instruction?.takeIf { it.isNotBlank() }?.let { append('\n').append(it.take(400)) }
        }
        val open = PendingIntent.getActivity(
            app, fx.entry.notifId,
            Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(MainActivity.EXTRA_EVENT, e.id),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val b = NotificationCompat.Builder(app, channelFor(fx.match.level))
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(line)
            .setStyle(NotificationCompat.BigTextStyle().bigText(long))
            .setColor(android.graphics.Color.parseColor(SeverityFill.hex(e.severityOf)))
            .setCategory(NotificationCompat.CATEGORY_EVENT)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setPriority(if (fx.match.level == Level.EXTREME) NotificationCompat.PRIORITY_MAX else if (fx.match.level == Level.SEVERE) NotificationCompat.PRIORITY_DEFAULT else NotificationCompat.PRIORITY_LOW)
            .setContentIntent(open)
            .setAutoCancel(true)
            // an update in place never rings twice; an escalation (alert = true) does
            .setOnlyAlertOnce(!fx.alert)
            .setSilent(!fx.alert && fx.match.level == Level.DIGEST)
        e.timeMs?.let { b.setWhen(it).setShowWhen(true) }
        if (fx.match.level == Level.DIGEST) b.setGroup(GROUP_DIGEST)
        // a warning leaves the lock screen when it expires; a point event after half a day
        val ttl = e.expiresMs?.let { it - serverNow } ?: if (e.ongoing) null else 12 * 3_600_000L
        if (ttl != null && ttl > 0) b.setTimeoutAfter(ttl)
        return post(fx.entry.notifId, b.build())
    }

    /** The digest group's summary line ("3 lower alerts near your places"), or its removal at 0. */
    fun digestSummary(count: Int) {
        if (count < 2) {
            nm.cancel(DIGEST_SUMMARY_ID)
            return
        }
        val c = Words.localized(app)
        val n = NotificationCompat.Builder(app, CH_DIGEST)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(c.resources.getQuantityString(R.plurals.notif_digest_summary, count, count))
            .setGroup(GROUP_DIGEST)
            .setGroupSummary(true)
            .setSilent(true)
            .build()
        post(DIGEST_SUMMARY_ID, n)
    }

    fun cancel(notifId: Int) = nm.cancel(notifId)

    private fun post(id: Int, n: Notification): Boolean {
        if (!canPost()) return false
        if (Build.VERSION.SDK_INT >= 33 &&
            ActivityCompat.checkSelfPermission(app, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return false
        return runCatching { nm.notify(id, n) }.isSuccess
    }

    companion object {
        const val CH_EXTREME = "sos_extreme_near"
        const val CH_SEVERE = "sos_severe_near"
        const val CH_DIGEST = "sos_digest"
        const val CH_WATCH = "sos_watch_status"
        val CHANNELS = listOf(CH_EXTREME, CH_SEVERE, CH_DIGEST, CH_WATCH)
        const val GROUP_DIGEST = "sos.digest"
        const val WATCH_ID = 1
        const val DIGEST_SUMMARY_ID = 2
    }
}
