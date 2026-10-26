package co.soclose.sosforge

import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.getValue
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.ui.nav.AppRoot
import co.soclose.sosforge.ui.theme.BarsFollow
import co.soclose.sosforge.ui.theme.SosTheme
import co.soclose.sosforge.ui.theme.barsFor
import co.soclose.sosforge.ui.theme.phoneIsNight
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * No configChanges on purpose: fold / unfold recreates the activity, and every piece of screen state
 * that must survive it (the open event, the tab, the funnel step) is saveable state.
 * AppCompatActivity for the per-app language before Android 13 (AppCompatDelegate).
 */
class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        barsFor(app.prefs.theme.value.isDark(phoneIsNight()))
        super.onCreate(savedInstanceState)
        takeEvent(intent)
        setContent {
            val textSize by app.prefs.textSize.collectAsStateWithLifecycle()
            val theme by app.prefs.theme.collectAsStateWithLifecycle()
            BarsFollow(theme)
            SosTheme(mode = theme, textScale = textSize.scale) { AppRoot() }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        takeEvent(intent)
    }

    private fun takeEvent(i: Intent?) {
        i?.getStringExtra(EXTRA_EVENT)?.let { OPEN_EVENT.value = it }
        i?.removeExtra(EXTRA_EVENT)
    }

    /** The socket runs while the app is on screen (and while the Watch runs, see SosApp). */
    override fun onStart() {
        super.onStart()
        app.onForeground(true)
    }

    override fun onStop() {
        super.onStop()
        // a fold / unfold recreates the activity: the socket stays open across it (no reconnect churn)
        if (!isChangingConfigurations) app.onForeground(false)
    }

    companion object {
        const val EXTRA_EVENT = "co.soclose.sosforge.EVENT"

        /** An event a notification asked to open; consumed by the shell. */
        val OPEN_EVENT = MutableStateFlow<String?>(null)
    }
}
