package co.soclose.sosforge.screens

// Shape of GSTATSFORGE/android app/src/test/.../screens/Screens.kt (0.2.4, GStats commit cdb56f0): the
// window's root view drawn into a Bitmap ourselves (Robolectric NATIVE graphics), inside a Box painted
// with the theme background, at real panel sizes. No captureToImage: it waits for idle frames.

import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.core.app.ApplicationProvider
import co.soclose.sosforge.Fixtures
import co.soclose.sosforge.SosApp
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.live.Link
import co.soclose.sosforge.core.net.ApiVersion
import co.soclose.sosforge.core.net.GuidanceReader
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.feature.detail.EventDetail
import co.soclose.sosforge.feature.funnel.FunnelScreen
import co.soclose.sosforge.feature.funnel.Step
import co.soclose.sosforge.ui.kit.ScreensMode
import co.soclose.sosforge.ui.nav.Routes
import co.soclose.sosforge.ui.nav.Shell
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.SosTheme
import co.soclose.sosforge.ui.theme.ThemeMode
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.text.TextLayoutResult
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/**
 * Every funnel step and every screen rendered to PNG on the JVM, from the VERBATIM production capture
 * (fixtures/ws.ndjson snapshot + sources) and lane B's guidance body. Run on demand:
 *   ./gradlew --no-daemon :app:testDebugUnitTest -Psos.screens=true --tests '*Screens*'
 * Output: app/build/screens/<variant>/<screen>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
abstract class ScreensBase(private val variant: String, private val scale: Float, private val mode: ThemeMode) {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()
    private lateinit var app: SosApp

    @Before fun seed() {
        assumeTrue("screenshots run with -Psos.screens=true", System.getProperty("sos.screens") == "true")
        ScreensMode.on = true
        app = ApplicationProvider.getApplicationContext()
        val snap = Fixtures.snapshot
        app.seedForScreens(snap.events, snap.sources, snap.serverTimeMs!!, fromCache = false)
        app.pinnedLink = Link.LIVE
        app.prefs.setPlaces(
            listOf(
                Place("la", "Los Angeles", 34.05, -118.24, 300, "search"),
                Place("ath", "Athens", 37.98, 23.73, 300, "search"),
            ),
        )
        app.prefs.setOnboarded(true)
        val g = GuidanceReader.read(SosJson.parseToJsonElement(Fixtures.text("guidance-en.json")), "en")
        app.seedVersionForScreens(ApiVersion(null, listOf(ApiVersion.GUIDANCE, ApiVersion.WS_ZONES)), g)
    }

    private fun shoot(name: String, content: @Composable () -> Unit) {
        rule.mainClock.autoAdvance = false
        rule.setContent {
            SosTheme(mode = mode, textScale = scale) {
                Box(Modifier.fillMaxSize().background(Sos.colors.bg0)) { content() }
            }
        }
        repeat(6) {
            Thread.sleep(40)
            rule.mainClock.advanceTimeBy(300)
            shadowOf(Looper.getMainLooper()).idle()
        }
        val root = rule.activity.window.decorView.rootView
        val bmp = Bitmap.createBitmap(root.width, root.height, Bitmap.Config.ARGB_8888)
        root.draw(Canvas(bmp))
        val dir = File(System.getProperty("sos.screens.out") ?: "build/screens", variant).apply { mkdirs() }
        File(dir, "$name.png").outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
        val (hard, soft) = Overflow.check(rule.onRoot(useUnmergedTree = true).fetchSemanticsNode(), root.width)
        File(dir, "overflow.txt").appendText(
            "== $name: ${hard.size} overflow, ${soft.size} ellipsized\n" + (hard.map { "  OVERFLOW $it" } + soft.map { "  ellipsized $it" }).joinToString("\n") + "\n",
        )
        assertTrue("$variant/$name overflows:\n" + hard.joinToString("\n"), hard.isEmpty())
    }

    /** A real event with the most to show: a CAP instruction and a position, else the first placed one. */
    private fun richEvent(): String = Fixtures.snapshot.events.firstOrNull { it.hasPosition && !it.instruction.isNullOrBlank() }?.id
        ?: Fixtures.snapshot.events.first { it.hasPosition }.id

    /** The swarm the replay rang for near Los Angeles (AlertRulesTest), shown with its distances. */
    private fun nearEvent(): String = Fixtures.snapshot.events.firstOrNull { it.id.startsWith("swarm:33.5") }?.id ?: richEvent()

    @Test fun f1_welcome() = shoot("f1_welcome") { FunnelScreen(onDone = {}, start = Step.WELCOME) }
    @Test fun f2_places() = shoot("f2_places") { FunnelScreen(onDone = {}, start = Step.PLACES) }
    @Test fun f3_kinds() = shoot("f3_kinds") { FunnelScreen(onDone = {}, start = Step.KINDS) }
    @Test fun f4_notify() = shoot("f4_notify") { FunnelScreen(onDone = {}, start = Step.NOTIFY) }
    @Test fun f5_background() = shoot("f5_background") { FunnelScreen(onDone = {}, start = Step.BACKGROUND) }
    @Test fun f6_done() = shoot("f6_done") { FunnelScreen(onDone = {}, start = Step.DONE) }

    /** No place yet (the Fold, 0.1.1): the step must lead back to adding one, not just "Continue". */
    @Test fun f5b_background_no_place() {
        app.prefs.setPlaces(emptyList())
        shoot("f5b_background_no_place") { FunnelScreen(onDone = {}, start = Step.BACKGROUND) }
    }
    @Test fun s1_live() = shoot("s1_live") { Shell(start = Routes.LIVE, onSetup = {}) }
    @Test fun s2_map() = shoot("s2_map") { Shell(start = Routes.MAP, onSetup = {}) }
    @Test fun s3_places() = shoot("s3_places") { Shell(start = Routes.PLACES, onSetup = {}) }
    @Test fun s4_sources() = shoot("s4_sources") { Shell(start = Routes.SOURCES, onSetup = {}) }
    @Test fun s5_settings() = shoot("s5_settings") { Shell(start = Routes.SETTINGS, onSetup = {}) }
    @Test fun s6_detail_warning() = shoot("s6_detail_warning") { EventDetail(richEvent(), onBack = {}, onMap = {}) }
    @Test fun s7_detail_near() = shoot("s7_detail_near") { EventDetail(nearEvent(), onBack = {}, onMap = {}) }

    @Test fun s8_live_cached_unreachable() {
        val snap = Fixtures.snapshot
        app.seedForScreens(snap.events, snap.sources, snap.serverTimeMs!!, fromCache = true)
        app.pinnedLink = Link.RECONNECTING
        shoot("s8_live_cached_unreachable") { Shell(start = Routes.LIVE, onSetup = {}) }
    }

    @Test fun s9_live_busy() {
        app.pinnedLink = Link.BUSY
        shoot("s9_live_busy") { Shell(start = Routes.LIVE, onSetup = {}) }
    }
}

// Fold cover 360 x 780 dp, Fold inner 752 x 835 dp: the real panels. Lunaire and Doux, normal and largest text.
@Config(qualifiers = "w360dp-h780dp-xxhdpi")
class Screens360Lunaire : ScreensBase("360_lunaire", 1f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w360dp-h780dp-xxhdpi")
class Screens360Doux : ScreensBase("360_doux", 1f, ThemeMode.DOUX)

@Config(qualifiers = "w360dp-h780dp-xxhdpi")
class Screens360LunaireXl : ScreensBase("360_lunaire_xl", 1.3f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w360dp-h780dp-xxhdpi")
class Screens360DouxXl : ScreensBase("360_doux_xl", 1.3f, ThemeMode.DOUX)

@Config(qualifiers = "w752dp-h835dp-xxhdpi")
class Screens752Lunaire : ScreensBase("752_lunaire", 1f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w752dp-h835dp-xxhdpi")
class Screens752Doux : ScreensBase("752_doux", 1f, ThemeMode.DOUX)

@Config(qualifiers = "w752dp-h835dp-xxhdpi")
class Screens752LunaireXl : ScreensBase("752_lunaire_xl", 1.3f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w752dp-h835dp-xxhdpi")
class Screens752DouxXl : ScreensBase("752_doux_xl", 1.3f, ThemeMode.DOUX)

// Tall: the whole of each long page at the largest text, to see every card end (owner rule).
@Config(qualifiers = "w360dp-h2600dp-xxhdpi")
class Screens360TallXl : ScreensBase("360_tall_lunaire_xl", 1.3f, ThemeMode.LUNAIRE)

// French at the cover's width: the longest of the five languages.
@Config(qualifiers = "fr-w360dp-h780dp-xxhdpi")
class Screens360French : ScreensBase("360_fr_doux", 1f, ThemeMode.DOUX)

// The two-column frontier from the other side (Fold inner in landscape).
@Config(qualifiers = "w835dp-h752dp-xxhdpi")
class Screens835Lunaire : ScreensBase("835_land_lunaire", 1f, ThemeMode.LUNAIRE)

/**
 * The owner's rule measured on the real layout (2026-10-10: nothing overflows its card, long detail
 * behind "Show more"), not eyeballed: the semantics tree of the rendered screen, walked.
 *  - OVERFLOW (fails the test): a node pushed past the window's left or right edge, or a text cut in
 *    WIDTH (a single line wider than its box: a word cut mid-way). Nodes inside a horizontal scroller
 *    (the chip rows) are exempt: going past the edge is what a scroller does.
 *  - ellipsized (listed in overflow.txt for review): a text cut at its maxLines. That is the "Show
 *    more" pattern, or a two-line title; each one is read, not assumed.
 */
object Overflow {
    fun check(root: SemanticsNode, widthPx: Int): Pair<List<String>, List<String>> {
        val hard = mutableListOf<String>()
        val soft = mutableListOf<String>()
        fun label(n: SemanticsNode): String =
            (n.config.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text }
                ?: n.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ") ?: "#${n.id}").take(60)
        fun walk(n: SemanticsNode, inScroller: Boolean) {
            val scroller = inScroller || n.config.getOrNull(SemanticsProperties.HorizontalScrollAxisRange) != null
            val b = n.boundsInRoot
            if (!scroller && b.width > 0 && (b.right > widthPx + 1 || b.left < -1)) hard += "${label(n)} [${b.left.toInt()}..${b.right.toInt()} of $widthPx px]"
            n.config.getOrNull(SemanticsActions.GetTextLayoutResult)?.action?.let { get ->
                val out = mutableListOf<TextLayoutResult>()
                if (runCatching { get(out) }.getOrDefault(false)) out.firstOrNull()?.let { t ->
                    // NOT t.didOverflowWidth: the semantics action re-lays the text out at its MAX width,
                    // so every wrap-content text shorter than that read as "cut" (288 false failures on the
                    // first run). A line wider than the box it is drawn in is the real cut, whatever the
                    // alignment.
                    val widest = (0 until t.lineCount).maxOfOrNull { t.getLineRight(it) - t.getLineLeft(it) } ?: 0f
                    if (!scroller && b.width > 0 && widest > b.width + 1) hard += "${label(n)} (cut in width: ${widest.toInt()} px in ${b.width.toInt()})"
                    else if (t.didOverflowHeight) soft += label(n)
                    brokenWord(t)?.let { hard += "${label(n)} (word broken across lines: $it)" }
                }
            }
            n.children.forEach { walk(it, scroller) }
        }
        walk(root, false)
        return hard to soft
    }

    /**
     * A short word split between two lines ("Liv / e"): a layout too narrow for its words, which no
     * width check sees (Compose breaks the word instead of overflowing). Long tokens (ids, URLs,
     * hashes, over 18 characters) may break anywhere (owner rule: overflow-wrap anywhere), and so may
     * CJK text, which has no spaces.
     */
    fun brokenWord(t: TextLayoutResult): String? {
        val txt = t.layoutInput.text.text
        fun latin(ch: Char) = ch.isLetterOrDigit() && ch.code < 0x2E80
        for (i in 0 until t.lineCount - 1) {
            val end = t.getLineEnd(i, visibleEnd = false)
            if (end <= 0 || end >= txt.length || !latin(txt[end - 1]) || !latin(txt[end])) continue
            var a = end
            while (a > 0 && !txt[a - 1].isWhitespace()) a--
            var b = end
            while (b < txt.length && !txt[b].isWhitespace()) b++
            if (b - a <= 18) return txt.substring(a, end) + " / " + txt.substring(end, b)
        }
        return null
    }
}

// Round 2 (2026-10-27): every format. The narrowest phone, a landscape phone (rail, no labels), the
// fold34 AVD inner screen, a tablet, and the system font scale 2.0 on top of the owner's text size.
@Config(qualifiers = "w320dp-h640dp-xxhdpi")
class Screens320Lunaire : ScreensBase("320_lunaire", 1f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w320dp-h640dp-xxhdpi")
class Screens320DouxFont2 : ScreensBase("320_doux_font2", 2f, ThemeMode.DOUX)

@Config(qualifiers = "w360dp-h780dp-xxhdpi")
class Screens360LunaireFont2 : ScreensBase("360_lunaire_font2", 2f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w800dp-h360dp-xxhdpi")
class Screens800x360Lunaire : ScreensBase("800x360_land_phone_lunaire", 1f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w673dp-h841dp-xxhdpi")
class Screens673Doux : ScreensBase("673_fold34_inner_doux", 1f, ThemeMode.DOUX)

@Config(qualifiers = "w1280dp-h800dp-xhdpi")
class Screens1280Lunaire : ScreensBase("1280_tablet_lunaire", 1f, ThemeMode.LUNAIRE)

@Config(qualifiers = "w1280dp-h800dp-xhdpi")
class Screens1280DouxFont2 : ScreensBase("1280_tablet_doux_font2", 2f, ThemeMode.DOUX)
