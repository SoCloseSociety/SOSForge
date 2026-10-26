package co.soclose.sosforge.core.watch

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import co.soclose.sosforge.app

/**
 * After a reboot (or an app update), the live Watch comes back only if the owner had switched it on.
 * `specialUse` may start from BOOT_COMPLETED on Android 15, unlike `dataSync` (README). The periodic
 * check needs nothing here: WorkManager re-arms itself.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val app = context.app
        if (app.prefs.watchLive.value && app.prefs.places.value.isNotEmpty()) app.watch.startService()
    }
}
