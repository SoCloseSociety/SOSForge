package co.soclose.sosforge

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.toArgb
import co.soclose.sosforge.core.net.Severity
import co.soclose.sosforge.core.net.ServerAddress
import co.soclose.sosforge.ui.kit.Breakpoints
import co.soclose.sosforge.ui.kit.NavPlace
import co.soclose.sosforge.ui.kit.SeverityShape
import co.soclose.sosforge.ui.kit.navFor
import co.soclose.sosforge.ui.kit.twoColumns
import co.soclose.sosforge.ui.theme.Doux
import co.soclose.sosforge.ui.theme.Lunaire
import co.soclose.sosforge.ui.theme.SosColors
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/** The product rules that are checkable as numbers: measured, not eyeballed. */
class RulesTest {
    // ------------------------------------------------------------------ WCAG (SentinelHouse tools/verifier-contraste.js)

    private fun lin(c: Float): Double = if (c <= 0.04045) c / 12.92 else ((c + 0.055) / 1.055).pow(2.4)
    private fun lum(c: Color): Double = 0.2126 * lin(c.red) + 0.7152 * lin(c.green) + 0.0722 * lin(c.blue)
    private fun ratio(a: Color, b: Color): Double {
        val la = lum(a)
        val lb = lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    private fun surfaces(t: SosColors) = listOf(t.bg0, t.bg1, t.bg2, t.bg3, t.bg4)

    @Test fun every_severity_word_passes_AA_on_every_surface_of_both_themes_and_on_its_own_pill() {
        val report = StringBuilder()
        for ((name, t) in listOf("Lunaire" to Lunaire, "Doux" to Doux)) {
            for (s in Severity.entries) {
                val fg = t.sev.of(s)
                var worst = Double.MAX_VALUE
                for (bg in surfaces(t)) {
                    worst = min(worst, ratio(fg, bg))
                    worst = min(worst, ratio(fg, fg.copy(alpha = 0.14f).compositeOver(bg)))
                }
                report.append("$name ${s.key}: ${"%.2f".format(worst)}:1\n")
                assertTrue("$name ${s.key} ${"%.2f".format(worst)}:1", worst >= 4.5)
            }
        }
        println(report)
    }

    @Test fun the_text_tokens_pass_AA_where_text_sits() {
        for (t in listOf(Lunaire, Doux)) {
            for (bg in listOf(t.bg0, t.bg1, t.bg2, t.bg3)) {
                assertTrue(ratio(t.t1, bg) >= 4.5)
                assertTrue(ratio(t.t2, bg) >= 4.5)
            }
            // t3 (meta) on the page and on cards
            assertTrue(ratio(t.t3, t.bg0) >= 4.5)
            assertTrue(ratio(t.t3, if (t.dark) t.bg2 else t.bg3) >= 4.5)
            assertTrue(ratio(t.onPrimary, t.t1) >= 4.5)
        }
    }

    private fun hue(c: Color): Float {
        val r = c.red; val g = c.green; val b = c.blue
        val mx = maxOf(r, g, b); val mn = minOf(r, g, b); val d = mx - mn
        if (d == 0f) return 0f
        val h = when (mx) {
            r -> ((g - b) / d) % 6
            g -> (b - r) / d + 2
            else -> (r - g) / d + 4
        } * 60f
        return (h + 360f) % 360f
    }

    @Test fun a_severity_keeps_its_hue_in_both_themes() {
        for (s in Severity.entries) {
            if (s == Severity.INFO) continue // a grey: its hue is not perceptible
            val d = abs(hue(Lunaire.sev.of(s)) - hue(Doux.sev.of(s)))
            assertTrue("${s.key}: ${hue(Lunaire.sev.of(s))} vs ${hue(Doux.sev.of(s))}", min(d, 360 - d) <= 3f)
        }
    }

    @Test fun mint_never_encodes_a_severity() {
        for (t in listOf(Lunaire, Doux)) {
            Severity.entries.forEach { s ->
                assertFalse(t.sev.of(s).toArgb() == t.mint.toArgb())
                val d = abs(hue(t.sev.of(s)) - hue(t.mint))
                assertTrue("${s.key} is too close to mint", min(d, 360 - d) >= 25f)
            }
        }
    }

    @Test fun every_severity_has_a_glyph_of_a_different_shape() {
        val shapes = Severity.entries.map { SeverityShape.of(it) }
        assertEquals(shapes.size, shapes.toSet().size)
    }

    @Test fun the_tokens_are_the_charte_values() {
        // SentinelHouse/08-sentinel-home/sentinel_home/ui/charte.css, :root and :root[data-theme="doux"]
        assertEquals(Color(0xFF06080A), Lunaire.bg0)
        assertEquals(Color(0xFFE9EEF2), Lunaire.t1)
        assertEquals(Color(0xFF34E5A0), Lunaire.mint)
        assertEquals(Color(0xFFF3ECDF), Doux.bg0)
        assertEquals(Color(0xFF0B7556), Doux.mint)
        assertFalse("#56c8a7 is drift", listOf(Lunaire.mint, Doux.mint).any { it == Color(0xFF56C8A7) })
    }

    // ------------------------------------------------------------------ layout frontiers

    @Test fun only_two_width_frontiers_exist_600_and_780() {
        assertEquals(listOf(600, 780), Breakpoints.WIDTHS)
        assertEquals(NavPlace.BOTTOM_BAR, navFor(599, 800).place)
        assertEquals(NavPlace.LEFT_RAIL, navFor(600, 800).place)
        assertFalse(twoColumns(779))
        assertTrue(twoColumns(780))
        // the real fleet widths: Fold cover 360, Fold inner 752 / 835
        assertEquals(NavPlace.BOTTOM_BAR, navFor(360, 780).place)
        assertEquals(NavPlace.LEFT_RAIL, navFor(752, 835).place)
        assertFalse(twoColumns(752))
        assertTrue(twoColumns(835))
    }

    @Test fun no_other_dp_breakpoint_hides_in_the_screens() {
        val src = File("src/main/java").walkTopDown().filter { it.extension == "kt" }
        val pattern = Regex("""(widthDp|screenWidthDp|maxWidth\.value|\bw\b)\s*[<>]=?\s*(\d{3})""")
        val found = src.flatMap { f -> pattern.findAll(f.readText()).map { "${f.name}: ${it.value}" } }.toList()
        assertTrue("breakpoints outside Panes.kt: $found", found.isEmpty())
    }

    // ------------------------------------------------------------------ network

    @Test fun cleartext_hosts_are_exactly_the_network_security_config_of_this_build_type() {
        // debug replaces the file (src/debug/res/xml); release ships src/main's, which allows no cleartext
        val f = if (co.soclose.sosforge.core.net.DevHosts.OVERRIDE) "src/debug/res/xml/network_security_config.xml" else "src/main/res/xml/network_security_config.xml"
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(File(f))
        val nodes = doc.getElementsByTagName("domain")
        val hosts = (0 until nodes.length).map { nodes.item(it).textContent.trim() }.toSet()
        assertEquals(ServerAddress.CLEARTEXT_HOSTS, hosts)
        assertFalse("the release config permits cleartext", File("src/main/res/xml/network_security_config.xml").readText().contains("cleartextTrafficPermitted=\"true\""))
    }

    @Test fun a_release_talks_to_production_only() {
        if (co.soclose.sosforge.core.net.DevHosts.OVERRIDE) return // the debug build is the drills' build
        assertTrue(ServerAddress.CLEARTEXT_HOSTS.isEmpty())
        assertEquals(null, ServerAddress.normalize("http://127.0.0.1:8334"))
        assertEquals(null, ServerAddress.normalize("http://10.0.2.2:8300"))
    }

    // ------------------------------------------------------------------ words

    private fun names(f: File): List<String> {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(f)
        val out = mutableListOf<String>()
        for (tag in listOf("string", "plurals")) {
            val n = doc.getElementsByTagName(tag)
            for (i in 0 until n.length) {
                val e = n.item(i) as Element
                if (e.getAttribute("translatable") != "false") out += "$tag:" + e.getAttribute("name")
            }
        }
        return out.sorted()
    }

    @Test fun the_five_languages_say_everything() {
        val base = names(File("src/main/res/values/strings.xml"))
        for (lang in listOf("fr", "es", "ja", "in")) {
            assertEquals("values-$lang", base, names(File("src/main/res/values-$lang/strings.xml")))
        }
    }

    @Test fun no_em_or_en_dash_anywhere() {
        val files = File("src/main").walkTopDown().filter { it.isFile && it.extension in setOf("kt", "xml") } + File("../README.md").takeIf { it.exists() }.let { listOfNotNull(it) }
        val bad = files.filter { it.readText().contains('—') || it.readText().contains('–') }.map { it.path }.toList()
        assertTrue("em/en dashes in $bad", bad.isEmpty())
    }

    @Test fun a_figure_and_its_unit_never_break_apart_in_english() {
        val text = File("src/main/res/values/strings.xml").readText()
        // "%1$d min", "%1$s km": the space after a placeholder followed by a unit must be a no-break space
        val loose = Regex("""%\d\$[ds] (s|min|h|d|km|places?|alerts?)\b""").findAll(text).map { it.value }.toList()
        assertTrue("breakable: $loose", loose.isEmpty())
    }
}
