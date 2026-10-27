package co.soclose.sosforge.ui.theme

// Kit: GStatsForge android 0.2.4 (GStats commit cdb56f0), ui/theme/Bars.kt, unchanged but for the names.

import android.content.res.Configuration
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect

/**
 * Transparent bars, edge to edge, whose icons follow the APP's theme (dark on Doux, light on Lunaire),
 * not the phone's: Doux inside a dark phone once left white status icons on the cream (GStats Fold QA T4).
 */
fun ComponentActivity.barsFor(appDark: Boolean) {
    val t = android.graphics.Color.TRANSPARENT
    val style = if (appDark) SystemBarStyle.dark(t) else SystemBarStyle.light(t, t)
    enableEdgeToEdge(statusBarStyle = style, navigationBarStyle = style)
}

fun ComponentActivity.phoneIsNight(): Boolean =
    (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

@Composable
fun ComponentActivity.BarsFollow(mode: ThemeMode) {
    val dark = mode.isDark(isSystemInDarkTheme())
    LaunchedEffect(dark) { barsFor(dark) }
}
