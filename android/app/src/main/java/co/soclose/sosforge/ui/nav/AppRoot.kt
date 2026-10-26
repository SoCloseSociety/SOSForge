package co.soclose.sosforge.ui.nav

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.app
import co.soclose.sosforge.feature.funnel.FunnelScreen

/** First run (or "Run setup again" from Settings): the funnel; afterwards, the shell. */
@Composable
fun AppRoot() {
    val app = LocalContext.current.app
    val onboarded by app.prefs.onboarded.collectAsStateWithLifecycle()
    if (!onboarded) FunnelScreen(onDone = {})
    else Shell(onSetup = { app.prefs.setOnboarded(false) })
}
