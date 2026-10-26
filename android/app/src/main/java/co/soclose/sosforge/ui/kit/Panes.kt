package co.soclose.sosforge.ui.kit

// Kit: GStatsForge android 0.2.4 (GStats commit cdb56f0), ui/kit/Panes.kt (the fleet's Fold navigation
// rule). Changed for SOSForge: exactly TWO width frontiers, the charte's (599/600 and 779/780), and the
// second one is measured on the WINDOW like a CSS media query. Dropped: the 960 dp "roomy" switch and
// the adaptive list-detail scaffold (two columns are a plain Row here).

import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalConfiguration

/**
 *   width <  600                 bottom bar           (Fold cover 360, phones)
 *   width >= 600                 LEFT rail, labels    (Fold inner 752 and 835, tablets)
 *   width >= 600, height < 480   LEFT rail, icons only (a phone in landscape)
 *   width <  780                 ONE content column
 *   width >= 780                 two columns (list + detail, or two stacks of cards)
 */
object Breakpoints {
    const val RAIL_DP = 600
    const val TWO_COLUMNS_DP = 780
    const val SHORT_DP = 480

    /** Every width at which ANY layout switches. RulesTest refuses any other. */
    val WIDTHS: List<Int> = listOf(RAIL_DP, TWO_COLUMNS_DP)
}

enum class NavPlace { BOTTOM_BAR, LEFT_RAIL }

data class NavSpec(val place: NavPlace, val labels: Boolean)

/** The navigation of a window. Pure, unit-tested on the real fleet widths. */
fun navFor(widthDp: Int, heightDp: Int): NavSpec = when {
    widthDp < Breakpoints.RAIL_DP -> NavSpec(NavPlace.BOTTOM_BAR, labels = true)
    heightDp < Breakpoints.SHORT_DP -> NavSpec(NavPlace.LEFT_RAIL, labels = false)
    else -> NavSpec(NavPlace.LEFT_RAIL, labels = true)
}

/** Two content columns? Pure. */
fun twoColumns(widthDp: Int): Boolean = widthDp >= Breakpoints.TWO_COLUMNS_DP

/** The window the APP measures, in dp (what Settings > Diagnostics prints). */
@Composable
fun windowDp(): Pair<Int, Int> {
    val cfg = LocalConfiguration.current
    return cfg.screenWidthDp to cfg.screenHeightDp
}
