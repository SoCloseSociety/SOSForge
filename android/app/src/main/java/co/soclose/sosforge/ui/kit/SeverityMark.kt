package co.soclose.sosforge.ui.kit

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.net.Severity
import co.soclose.sosforge.ui.theme.Sos
import kotlin.math.cos
import kotlin.math.sin

/**
 * Each severity has a glyph of a DIFFERENT SHAPE, so the rank survives without colour (colour-blind
 * readers, a sunlit screen, a grey-scale print). The web's glyphs (format.ts SEVERITY_META) put two
 * triangles side by side (moderate "▲", severe "⚠"); here every shape is unique (RulesTest checks):
 *
 *   info: an open ring   minor: a square   moderate: a triangle   severe: a diamond   extreme: an octagon
 *
 * Drawn on a Canvas: no font fallback can turn one into a tofu box.
 */
enum class SeverityShape { RING, SQUARE, TRIANGLE, DIAMOND, OCTAGON;

    companion object {
        fun of(s: Severity): SeverityShape = when (s) {
            Severity.INFO -> RING
            Severity.MINOR -> SQUARE
            Severity.MODERATE -> TRIANGLE
            Severity.SEVERE -> DIAMOND
            Severity.EXTREME -> OCTAGON
        }
    }
}

@Composable
fun SeverityGlyph(severity: Severity, modifier: Modifier = Modifier, size: Dp = 12.dp, color: Color? = null) {
    val col = color ?: Sos.colors.sev.of(severity)
    Canvas(modifier.size(size)) { drawShape(SeverityShape.of(severity), col) }
}

fun DrawScope.drawShape(shape: SeverityShape, col: Color) {
    val w = size.width
    val h = size.height
    when (shape) {
        SeverityShape.RING -> drawCircle(col, radius = w / 2 - w * 0.12f, style = Stroke(width = w * 0.2f))
        SeverityShape.SQUARE -> drawRect(col, topLeft = Offset(w * 0.1f, h * 0.1f), size = androidx.compose.ui.geometry.Size(w * 0.8f, h * 0.8f))
        SeverityShape.TRIANGLE -> drawPath(Path().apply { moveTo(w / 2, 0f); lineTo(w, h * 0.92f); lineTo(0f, h * 0.92f); close() }, col)
        SeverityShape.DIAMOND -> drawPath(Path().apply { moveTo(w / 2, 0f); lineTo(w, h / 2); lineTo(w / 2, h); lineTo(0f, h / 2); close() }, col)
        SeverityShape.OCTAGON -> drawPath(
            Path().apply {
                for (i in 0 until 8) {
                    val a = Math.toRadians(22.5 + 45.0 * i)
                    val x = w / 2 + (w / 2) * cos(a).toFloat()
                    val y = h / 2 + (h / 2) * sin(a).toFloat()
                    if (i == 0) moveTo(x, y) else lineTo(x, y)
                }
                close()
            },
            col,
        )
    }
}

/** The glyph + the word, in the severity's colour: what every list row and detail carries. */
@Composable
fun SeverityBadge(severity: Severity, modifier: Modifier = Modifier, pill: Boolean = false) {
    val col = Sos.colors.sev.of(severity)
    val word = stringResource(Words.severity(severity))
    val shape = RoundedCornerShape(999.dp)
    Row(
        modifier
            .then(if (pill) Modifier.background(col.copy(alpha = 0.14f), shape).border(1.dp, col.copy(alpha = 0.45f), shape).padding(horizontal = 10.dp, vertical = 4.dp) else Modifier)
            .clearAndSetSemantics { contentDescription = word },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SeverityGlyph(severity, size = 11.dp)
        Spacer(Modifier.width(6.dp))
        Text(if (pill) word.uppercase() else word, style = if (pill) Sos.type.pill else Sos.type.label, color = col, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
