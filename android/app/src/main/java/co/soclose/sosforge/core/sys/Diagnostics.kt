package co.soclose.sosforge.core.sys

import android.Manifest
import android.app.AppOpsManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Process
import android.provider.Settings
import androidx.core.content.ContextCompat

/**
 * What the phone REALLY lets this app do, read from the system rather than assumed. AdsForge lesson: a
 * missing POST_NOTIFICATIONS is a silent no-op, and `cmd appops get <pkg> POST_NOTIFICATION` is how the
 * truth was found; this is the same reading, from inside the app.
 */
object Diagnostics {
    fun notificationPermission(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** The appops mode of POST_NOTIFICATION ("allow", "ignore", ...), or why it cannot be read. */
    fun notificationAppOp(ctx: Context): String {
        if (Build.VERSION.SDK_INT < 33) return "n/a (Android ${Build.VERSION.RELEASE})"
        val aom = ctx.getSystemService(AppOpsManager::class.java) ?: return "unknown"
        val op = AppOpsManager.permissionToOp(Manifest.permission.POST_NOTIFICATIONS) ?: return "unknown"
        val mode = runCatching { aom.unsafeCheckOpNoThrow(op, Process.myUid(), ctx.packageName) }.getOrNull() ?: return "unreadable"
        return when (mode) {
            AppOpsManager.MODE_ALLOWED -> "allow"
            AppOpsManager.MODE_IGNORED -> "ignore"
            AppOpsManager.MODE_ERRORED -> "deny"
            AppOpsManager.MODE_DEFAULT -> "default"
            AppOpsManager.MODE_FOREGROUND -> "foreground"
            else -> "mode $mode"
        }
    }

    fun importanceWord(i: Int?): String = when (i) {
        null -> "missing"
        NotificationManager.IMPORTANCE_NONE -> "off"
        NotificationManager.IMPORTANCE_MIN -> "min"
        NotificationManager.IMPORTANCE_LOW -> "low"
        NotificationManager.IMPORTANCE_DEFAULT -> "default"
        NotificationManager.IMPORTANCE_HIGH -> "high"
        else -> "importance $i"
    }

    /** The app's notification settings page (where a refusal can be undone). */
    fun notificationSettings(ctx: Context): Intent =
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    /** The system dialog that exempts this app from battery optimisation (the Watch survives Doze). */
    fun batteryExemption(ctx: Context): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + ctx.packageName)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    fun appSettings(ctx: Context): Intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + ctx.packageName)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
}
