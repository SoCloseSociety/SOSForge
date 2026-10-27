package co.soclose.sosforge.ui.kit

// Kit: GStatsForge android 0.2.4 (GStats commit cdb56f0), ui/kit/Buttons.kt. Kept: PrimaryButton,
// SecondaryButton, TextAction. Dropped: HoldToConfirmButton (nothing here is destructive enough).
// Changed: a label may wrap onto two lines (no fixed height on text: GStats lesson), 48 dp targets.

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import co.soclose.sosforge.ui.theme.Sos

private val Pill = RoundedCornerShape(999.dp)

/** The ONE primary action of a screen: a pill filled with the text colour (House principle). */
@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
    icon: ImageVector? = null,
) {
    val c = Sos.colors
    Row(
        modifier
            .defaultMinSize(minHeight = 52.dp)
            .alpha(if (enabled) 1f else 0.4f)
            .clip(Pill)
            .background(c.t1)
            .clickable(enabled = enabled && !loading, role = Role.Button, onClick = onClick)
            .padding(horizontal = 22.dp, vertical = 14.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (loading) {
            CircularProgressIndicator(Modifier.size(18.dp), color = c.onPrimary, strokeWidth = 2.dp)
            Box(Modifier.size(10.dp))
        } else if (icon != null) {
            Icon(icon, null, Modifier.size(18.dp), tint = c.onPrimary)
            Box(Modifier.size(8.dp))
        }
        Text(text, style = Sos.type.label, color = c.onPrimary, maxLines = 3, textAlign = TextAlign.Center)
    }
}

/** Any other action: a quiet outlined pill, optionally toned. */
@Composable
fun SecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    icon: ImageVector? = null,
    tone: Color? = null,
) {
    val c = Sos.colors
    val fg = tone ?: c.t1
    Row(
        modifier
            .defaultMinSize(minHeight = 48.dp)
            .alpha(if (enabled) 1f else 0.4f)
            .clip(Pill)
            .border(1.dp, if (tone != null) tone.copy(alpha = 0.5f) else c.lineStrong, Pill)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) {
            Icon(icon, null, Modifier.size(18.dp), tint = fg)
            Box(Modifier.size(8.dp))
        }
        Text(text, style = Sos.type.label, color = fg, maxLines = 3, textAlign = TextAlign.Center)
    }
}

/**
 * A link that may be long ("Source: Ready.gov (FEMA) -- Floods"): it WRAPS, up to three lines, instead
 * of being cut mid-word like a one-line action would be. 48 dp tall at least.
 */
@Composable
fun LinkText(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, color: Color? = null) {
    Box(
        modifier
            .defaultMinSize(minHeight = 48.dp)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(vertical = 4.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(text, style = Sos.type.small, color = color ?: Sos.colors.cyan, maxLines = 3, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
    }
}

/** An inline text action, 48 dp tall for the touch target. One or two words: it never breaks inside a word. */
@Composable
fun TextAction(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, color: Color? = null) {
    Box(
        modifier
            .defaultMinSize(minHeight = 48.dp)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 4.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(text, style = Sos.type.label, color = color ?: Sos.colors.cyan, maxLines = 1, softWrap = false)
    }
}
