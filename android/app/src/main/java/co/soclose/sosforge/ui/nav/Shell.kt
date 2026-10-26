package co.soclose.sosforge.ui.nav

// Kit: GStatsForge android 0.2.4 (GStats commit cdb56f0), ui/nav/Shell.kt: ONE NavigationSuiteScaffold
// whose bar becomes a LEFT rail from 600 dp (the fleet's Fold rule), the NavHost under it, insets per
// layout. Changed: SOSForge's five tabs, the notification deep link, no auth.

import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.Map
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Sensors
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRailItemDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteDefaults
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffold
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteType
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import co.soclose.sosforge.MainActivity
import co.soclose.sosforge.R
import co.soclose.sosforge.feature.detail.EventDetail
import co.soclose.sosforge.feature.live.LiveScreen
import co.soclose.sosforge.feature.map.MapScreen
import co.soclose.sosforge.feature.places.PlacesScreen
import co.soclose.sosforge.feature.settings.SettingsScreen
import co.soclose.sosforge.feature.sources.SourcesScreen
import co.soclose.sosforge.ui.kit.NavPlace
import co.soclose.sosforge.ui.kit.navFor
import co.soclose.sosforge.ui.kit.windowDp
import co.soclose.sosforge.ui.theme.Sos

object Routes {
    const val LIVE = "live"
    const val MAP = "map?focus={focus}"
    const val PLACES = "places"
    const val SOURCES = "sources"
    const val SETTINGS = "settings"
    const val EVENT = "event/{id}"
    val TABS = listOf(LIVE, MAP, PLACES, SOURCES, SETTINGS)

    fun map(focus: String? = null) = if (focus == null) "map" else "map?focus=" + Uri.encode(focus)
    fun event(id: String) = "event/" + Uri.encode(id)
}

private data class Tab(val route: String, val nav: String, val label: Int, val icon: ImageVector)

private val TABS = listOf(
    Tab(Routes.LIVE, Routes.LIVE, R.string.tab_live, Icons.Outlined.Bolt),
    Tab(Routes.MAP, "map", R.string.tab_map, Icons.Outlined.Map),
    Tab(Routes.PLACES, Routes.PLACES, R.string.tab_places, Icons.Outlined.Place),
    Tab(Routes.SOURCES, Routes.SOURCES, R.string.tab_sources, Icons.Outlined.Sensors),
    Tab(Routes.SETTINGS, Routes.SETTINGS, R.string.tab_settings, Icons.Outlined.Settings),
)

class Nav(private val c: NavHostController) {
    fun tab(route: String) = c.navigate(route) {
        popUpTo(Routes.LIVE) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }

    fun event(id: String) = c.navigate(Routes.event(id)) { launchSingleTop = true }
    fun map(focus: String) = c.navigate(Routes.map(focus)) {
        popUpTo(Routes.LIVE) { saveState = true }
        launchSingleTop = true
    }
    fun back() = c.popBackStack()
}

/** The bar's labels grow with the text size only up to this (five tabs on the 360 dp cover). */
object NavLabels {
    const val MAX_SCALE = 1.15f
    fun scale(fontScale: Float, bar: Boolean): Float = if (bar) minOf(fontScale, MAX_SCALE) else fontScale
}

@Composable
private fun NavLabel(name: String, bar: Boolean) {
    val d = LocalDensity.current
    CompositionLocalProvider(LocalDensity provides Density(d.density, NavLabels.scale(d.fontScale, bar))) {
        Text(name, style = if (bar) Sos.type.small.copy(fontSize = 12.sp, lineHeight = 16.sp) else Sos.type.meta, maxLines = 1, overflow = TextOverflow.Ellipsis, softWrap = false)
    }
}

@Composable
fun Shell(start: String = Routes.LIVE, onSetup: () -> Unit) {
    val controller = rememberNavController()
    val nav = androidx.compose.runtime.remember(controller) { Nav(controller) }
    val entry by controller.currentBackStackEntryAsState()
    val route = entry?.destination?.route
    var lastTab by rememberSaveable { mutableStateOf(Routes.LIVE) }
    LaunchedEffect(route) { if (route != null && route in Routes.TABS) lastTab = route }
    val lit = route?.takeIf { it in Routes.TABS } ?: lastTab

    // a tapped notification opens its event
    val open by MainActivity.OPEN_EVENT.collectAsStateWithLifecycle()
    LaunchedEffect(open) {
        open?.let {
            nav.event(it)
            MainActivity.OPEN_EVENT.value = null
        }
    }

    val (w, h) = windowDp()
    val spec = navFor(w, h)
    val layout = if (spec.place == NavPlace.LEFT_RAIL) NavigationSuiteType.NavigationRail else NavigationSuiteType.NavigationBar
    val labels = TABS.map { stringResource(it.label) }
    val c = Sos.colors
    val itemColors = NavigationSuiteDefaults.itemColors(
        navigationBarItemColors = NavigationBarItemDefaults.colors(
            selectedIconColor = c.onPrimary, selectedTextColor = c.t1, indicatorColor = c.t1,
            unselectedIconColor = c.t2, unselectedTextColor = c.t2,
        ),
        navigationRailItemColors = NavigationRailItemDefaults.colors(
            selectedIconColor = c.onPrimary, selectedTextColor = c.t1, indicatorColor = c.t1,
            unselectedIconColor = c.t2, unselectedTextColor = c.t2,
        ),
    )

    NavigationSuiteScaffold(
        navigationSuiteItems = {
            TABS.forEachIndexed { i, t ->
                val name = labels[i]
                item(
                    selected = lit == t.route,
                    onClick = { nav.tab(t.nav) },
                    icon = { Icon(t.icon, null) },
                    modifier = Modifier.semantics { contentDescription = name },
                    label = if (spec.labels) ({ NavLabel(name, bar = spec.place == NavPlace.BOTTOM_BAR) }) else null,
                    alwaysShowLabel = spec.labels,
                    colors = itemColors,
                )
            }
        },
        layoutType = layout,
        navigationSuiteColors = NavigationSuiteDefaults.colors(
            navigationBarContainerColor = c.bg1, navigationRailContainerColor = c.bg1,
            navigationBarContentColor = c.t2, navigationRailContentColor = c.t2,
        ),
        containerColor = c.bg0,
        contentColor = c.t1,
    ) {
        val insets = if (layout == NavigationSuiteType.NavigationBar) {
            WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal).union(WindowInsets.ime.only(WindowInsetsSides.Bottom))
        } else {
            WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.End + WindowInsetsSides.Bottom)
        }
        Box(Modifier.fillMaxSize().background(c.bg0).windowInsetsPadding(insets)) {
            NavHost(controller, startDestination = start, modifier = Modifier.fillMaxSize()) {
                composable(Routes.LIVE) { LiveScreen(onOpen = nav::event, onPlaces = { nav.tab(Routes.PLACES) }, onMap = { nav.map(it) }) }
                composable(Routes.MAP, arguments = listOf(navArgument("focus") { type = NavType.StringType; nullable = true; defaultValue = null })) {
                    MapScreen(focusId = it.arguments?.getString("focus"), onOpen = nav::event)
                }
                composable(Routes.PLACES) { PlacesScreen(onOpen = nav::event) }
                composable(Routes.SOURCES) { SourcesScreen() }
                composable(Routes.SETTINGS) { SettingsScreen(onSetup = onSetup) }
                composable(Routes.EVENT, arguments = listOf(navArgument("id") { type = NavType.StringType })) {
                    val id = it.arguments?.getString("id").orEmpty()
                    EventDetail(id, onBack = { nav.back() }, onMap = { nav.map(id) })
                }
            }
        }
    }
}
