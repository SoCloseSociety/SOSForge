package co.soclose.sosforge.ui.theme

// Kit: Sentinel House Compose kit, from GStatsForge android 0.2.4 (GStats commit cdb56f0), ui/theme/Theme.kt.
// Changed here: token VALUES re-read from SentinelHouse/08-sentinel-home/sentinel_home/ui/charte.css
// (Lunaire on :root, Doux on :root[data-theme="doux"]) instead of the GStats copy, which had drifted
// (bg0 #0B0F14 for #06080a, red #FB7185 for #ff5e5e, a rose thread where the charte draws a mint one).
// Added: the SOSForge severity palette (fixed hues, lightness per theme, AA measured in RulesTest).

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.text.selection.LocalTextSelectionColors
import androidx.compose.foundation.text.selection.TextSelectionColors
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import co.soclose.sosforge.R
import co.soclose.sosforge.core.net.Severity

/**
 * Sentinel House tokens, charte.css values to the byte. Rules kept from the House: one primary action
 * per screen in the text colour, mint is the brand thread AND "ok / live" (never a severity), status =
 * glyph + word, every figure carries its age, Plex Sans for words and Plex Mono for figures, text never
 * under 12 sp.
 */
@Immutable
data class SosColors(
    val dark: Boolean,
    val bg0: Color,       // --bg-0: the night (or the cream) behind everything
    val bg1: Color,       // --bg-1: bars, sheets, the primary button's ink
    val bg2: Color,       // --bg-2: cards
    val bg3: Color,       // --bg-3: raised, pressed
    val bg4: Color,       // --bg-4: chips, inputs
    val line: Color,      // --line
    val lineStrong: Color, // --line-strong
    val t1: Color,        // text, and the primary action's fill
    val t2: Color,
    val t3: Color,        // overlines, meta, ages
    val onPrimary: Color, // --primaire-encre
    val mint: Color,      // the thread, ok, live. NEVER a severity.
    val cyan: Color,      // links, info
    val amber: Color,     // attention (a status, not a severity)
    val red: Color,       // a broken thing (a status, not a severity)
    val sev: SeverityColors,
)

/**
 * The reserved SOSForge status scale (frontend/src/styles.css --sev-*): the HUE of each rank is the web
 * fill's, in both themes; only the lightness moves, until WCAG AA (4.5:1) holds on every surface of the
 * theme AND on the rank's own 14 % tinted pill. Computed by the method of
 * SentinelHouse tools/verifier-contraste.js, re-measured by RulesTest on every build.
 */
@Immutable
data class SeverityColors(val info: Color, val minor: Color, val moderate: Color, val severe: Color, val extreme: Color) {
    fun of(s: Severity): Color = when (s) {
        Severity.INFO -> info
        Severity.MINOR -> minor
        Severity.MODERATE -> moderate
        Severity.SEVERE -> severe
        Severity.EXTREME -> extreme
    }
}

/** The web's fills, for map markers (the map is its own background). */
object SeverityFill {
    val info = Color(0xFF6F7379)
    val minor = Color(0xFF0CA30C)
    val moderate = Color(0xFFFAB219)
    val severe = Color(0xFFEC835A)
    val extreme = Color(0xFFD03B3B)
    fun hex(s: Severity): String = when (s) {
        Severity.INFO -> "#6f7379"
        Severity.MINOR -> "#0ca30c"
        Severity.MODERATE -> "#fab219"
        Severity.SEVERE -> "#ec835a"
        Severity.EXTREME -> "#d03b3b"
    }
}

val Lunaire = SosColors(
    dark = true,
    bg0 = Color(0xFF06080A),
    bg1 = Color(0xFF0A0D10),
    bg2 = Color(0xFF0E1318),
    bg3 = Color(0xFF121922),
    bg4 = Color(0xFF182230),
    line = Color(0x21A0B9CD),       // rgba(160,185,205,.13)
    lineStrong = Color(0x42AAC3D7), // rgba(170,195,215,.26)
    t1 = Color(0xFFE9EEF2),
    t2 = Color(0xFF97A6B2),
    t3 = Color(0xFF7C8A96),
    onPrimary = Color(0xFF0A0D10),
    mint = Color(0xFF34E5A0),
    cyan = Color(0xFF45C9E6),
    amber = Color(0xFFE8B13A),
    red = Color(0xFFFF5E5E),
    sev = SeverityColors(
        info = Color(0xFF999CA1),     // hue 217
        minor = Color(0xFF0DB20D),    // hue 120
        moderate = Color(0xFFFAB219), // hue 41
        severe = Color(0xFFEC835A),   // hue 17
        extreme = Color(0xFFE07E7E),  // hue 0
    ),
)

val Doux = SosColors(
    dark = false,
    bg0 = Color(0xFFF3ECDF),
    bg1 = Color(0xFFF8F3E9),
    bg2 = Color(0xFFFCF9F2),
    bg3 = Color(0xFFFFFFFF),
    bg4 = Color(0xFFEFE6D6),
    line = Color(0x1F5A4632),       // rgba(90,70,50,.12)
    lineStrong = Color(0x385A4632), // rgba(90,70,50,.22)
    t1 = Color(0xFF27221D),
    t2 = Color(0xFF5A524A),
    t3 = Color(0xFF6B635A),
    onPrimary = Color(0xFFFCF9F2),
    mint = Color(0xFF0B7556),
    cyan = Color(0xFF0B6F86),
    amber = Color(0xFF8A5A00),
    red = Color(0xFFC03A2B),
    sev = SeverityColors(
        info = Color(0xFF575A5F),
        minor = Color(0xFF086708),
        moderate = Color(0xFF785203),
        severe = Color(0xFF9B3912),
        extreme = Color(0xFFA62828),
    ),
)

@OptIn(ExperimentalTextApi::class)
private fun plexSans(weight: Int) = Font(
    R.font.plex_sans,
    weight = FontWeight(weight),
    variationSettings = FontVariation.Settings(FontVariation.weight(weight)),
)

@OptIn(ExperimentalTextApi::class)
val PlexSans = FontFamily(plexSans(400), plexSans(500), plexSans(600), plexSans(700))

val PlexMono = FontFamily(
    Font(R.font.plex_mono_regular, FontWeight.Normal),
    Font(R.font.plex_mono_medium, FontWeight.Medium),
    Font(R.font.plex_mono_semibold, FontWeight.SemiBold),
)

/** charte.css type scale (--ts-*), in sp; nothing under 12. */
@Immutable
data class SosType(
    val display: TextStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, fontSize = 26.sp, lineHeight = 31.sp, letterSpacing = (-0.3).sp),
    val title: TextStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, fontSize = 19.sp, lineHeight = 25.sp, letterSpacing = (-0.2).sp),
    val heading: TextStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, lineHeight = 23.sp),
    val body: TextStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 21.sp),
    val bodyStrong: TextStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Medium, fontSize = 15.sp, lineHeight = 21.sp),
    val small: TextStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Normal, fontSize = 14.sp, lineHeight = 19.sp),
    val label: TextStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Medium, fontSize = 14.sp, lineHeight = 18.sp),
    /** Overlines and pills: mono capitals, 12 sp (the floor), tracked. The ONLY capitals in the app. */
    val overline: TextStyle = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp, letterSpacing = 1.6.sp),
    val pill: TextStyle = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp, letterSpacing = 1.2.sp),
    val figure: TextStyle = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.SemiBold, fontSize = 22.sp, lineHeight = 26.sp),
    val figureSmall: TextStyle = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.Medium, fontSize = 17.sp, lineHeight = 22.sp),
    val mono: TextStyle = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.Normal, fontSize = 13.sp, lineHeight = 18.sp),
    val meta: TextStyle = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.Normal, fontSize = 12.sp, lineHeight = 16.sp),
)

/** Spacing 4 / 8 / 12 / 16 / 24 / 32; charte --rayon 14 for cards, 10 for inner tiles. */
object Space {
    val xs = 4.dp
    val s = 8.dp
    val m = 12.dp
    val l = 16.dp
    val xl = 24.dp
    val xxl = 32.dp
    val radius = 14.dp
    val radiusSmall = 10.dp
    val gutter = 16.dp
}

val LocalSosColors = staticCompositionLocalOf { Lunaire }
val LocalSosType = staticCompositionLocalOf { SosType() }

object Sos {
    val colors: SosColors @Composable get() = LocalSosColors.current
    val type: SosType @Composable get() = LocalSosType.current
}

/** The owner's theme: follow the system (default), Lunaire (dark) or Doux (light). */
enum class ThemeMode(val key: String) {
    SYSTEM("system"), LUNAIRE("lunaire"), DOUX("doux");

    /** Is the app drawn dark? The ONE answer the app colours AND the system bars follow. */
    fun isDark(systemDark: Boolean): Boolean = when (this) {
        SYSTEM -> systemDark
        LUNAIRE -> true
        DOUX -> false
    }

    companion object {
        fun fromKey(key: String?): ThemeMode = entries.firstOrNull { it.key == key } ?: SYSTEM
    }
}

@Composable
fun SosTheme(mode: ThemeMode = ThemeMode.SYSTEM, textScale: Float = 1f, content: @Composable () -> Unit) {
    val dark = mode.isDark(isSystemInDarkTheme())
    // One root scale for every sp in the app: the owner's text size multiplies the system font scale.
    val base = LocalDensity.current
    val density = remember(base, textScale) { Density(base.density, base.fontScale * textScale) }
    val c = if (dark) Lunaire else Doux
    val t = SosType()
    val scheme = if (dark) darkColorScheme(
        primary = c.t1, onPrimary = c.onPrimary,
        secondary = c.mint, onSecondary = c.onPrimary,
        tertiary = c.cyan, onTertiary = c.onPrimary,
        background = c.bg0, onBackground = c.t1,
        surface = c.bg2, onSurface = c.t1,
        surfaceVariant = c.bg3, onSurfaceVariant = c.t2,
        surfaceContainer = c.bg2, surfaceContainerHigh = c.bg3, surfaceContainerHighest = c.bg4,
        surfaceContainerLow = c.bg1, surfaceContainerLowest = c.bg0,
        outline = c.lineStrong, outlineVariant = c.line,
        error = c.red, onError = c.onPrimary,
    ) else lightColorScheme(
        primary = c.t1, onPrimary = c.onPrimary,
        secondary = c.mint, onSecondary = c.bg3,
        tertiary = c.cyan, onTertiary = c.bg3,
        background = c.bg0, onBackground = c.t1,
        surface = c.bg2, onSurface = c.t1,
        surfaceVariant = c.bg4, onSurfaceVariant = c.t2,
        surfaceContainer = c.bg2, surfaceContainerHigh = c.bg3, surfaceContainerHighest = c.bg4,
        surfaceContainerLow = c.bg1, surfaceContainerLowest = c.bg0,
        outline = c.lineStrong, outlineVariant = c.line,
        error = c.red, onError = c.bg3,
    )
    CompositionLocalProvider(
        LocalSosColors provides c,
        LocalSosType provides t,
        LocalDensity provides density,
        LocalTextSelectionColors provides TextSelectionColors(handleColor = c.mint, backgroundColor = c.mint.copy(alpha = 0.3f)),
    ) {
        MaterialTheme(
            colorScheme = scheme,
            typography = MaterialTheme.typography.copy(
                bodyLarge = t.body, bodyMedium = t.small, bodySmall = t.small,
                labelLarge = t.label, labelMedium = t.label, labelSmall = t.meta,
                titleLarge = t.title, titleMedium = t.heading, titleSmall = t.label,
                headlineSmall = t.title, headlineMedium = t.display,
            ),
            content = content,
        )
    }
}
