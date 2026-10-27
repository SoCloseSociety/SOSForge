package co.soclose.sosforge.ui.kit

// Kit: GStatsForge android 0.2.4 (GStats commit cdb56f0), ui/kit/Surfaces.kt. Kept: Card, Overline,
// PageHero, KeyValue (+ KeyValueFit), MetaRow, ChipFlow, ChipRow, the filter chip, Section, Divider.
// Changed: the PageHero thread is MINT (charte.css --fil: var(--mint)), not rose; radius 14 (--rayon).
// Dropped: TileRow / StatTile (no tile grid in this app).

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.FlowRowScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space

/** A card: one card = one thing, one action (a tap opens its detail). */
@Composable
fun Card(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    accent: Color? = null,
    padding: Boolean = true,
    content: @Composable ColumnScope.() -> Unit,
) {
    val c = Sos.colors
    val shape = RoundedCornerShape(Space.radius)
    Column(
        modifier
            .fillMaxWidth()
            .clip(shape)
            .background(if (c.dark) c.bg2 else c.bg3)
            .border(1.dp, accent?.copy(alpha = 0.55f) ?: c.line, shape)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .then(if (padding) Modifier.padding(Space.l) else Modifier),
        content = content,
    )
}

/** Mono capitals above a block: overlines and pills are the ONLY capitals of the app. */
@Composable
fun Overline(text: String, modifier: Modifier = Modifier, color: Color? = null, maxLines: Int = 2) {
    Text(text.uppercase(), modifier.semantics { heading() }, style = Sos.type.overline, color = color ?: Sos.colors.t3, maxLines = maxLines, overflow = TextOverflow.Ellipsis)
}

/**
 * The top of a screen answers its question in two seconds: overline, a short title, one sentence,
 * the mint thread (charte: 160 x 2), at most ONE action, then a mono meta line.
 */
@Composable
fun PageHero(
    title: String,
    modifier: Modifier = Modifier,
    overline: String? = null,
    sentence: String? = null,
    trailing: (@Composable () -> Unit)? = null,
    action: (@Composable () -> Unit)? = null,
    meta: (@Composable () -> Unit)? = null,
) {
    Column(modifier.fillMaxWidth().padding(top = Space.s, bottom = Space.m)) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                if (overline != null) {
                    Overline(overline)
                    Spacer(Modifier.height(6.dp))
                }
                Text(title, style = Sos.type.display, color = Sos.colors.t1, modifier = Modifier.semantics { heading() })
            }
            if (trailing != null) {
                Spacer(Modifier.width(Space.s))
                trailing()
            }
        }
        if (sentence != null) {
            Spacer(Modifier.height(6.dp))
            Text(sentence, style = Sos.type.body, color = Sos.colors.t2)
        }
        Spacer(Modifier.height(Space.m))
        Box(Modifier.width(160.dp).height(2.dp).background(Sos.colors.mint))
        if (action != null) {
            Spacer(Modifier.height(Space.l))
            action()
        }
        if (meta != null) {
            Spacer(Modifier.height(Space.s))
            meta()
        }
    }
}

/**
 * label ............ value (mono). Side by side when both fit on one line; otherwise the value goes
 * UNDER its label at full width and wraps (GStats Fold QA T2: an id cut mid-word at Extra large).
 */
@Composable
fun KeyValue(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color? = null) {
    val gap = Space.s
    Layout(
        content = {
            Text(label, style = Sos.type.small, color = Sos.colors.t2)
            Text(value, style = Sos.type.mono, color = valueColor ?: Sos.colors.t1)
        },
        modifier = modifier.fillMaxWidth().padding(vertical = 6.dp),
    ) { ms, cs ->
        val gapPx = gap.roundToPx()
        val (l, v) = ms
        val lw = l.maxIntrinsicWidth(cs.maxHeight)
        val vw = v.maxIntrinsicWidth(cs.maxHeight)
        if (KeyValueFit.sideBySide(lw, vw, gapPx, cs.maxWidth)) {
            val lp = l.measure(Constraints(maxWidth = cs.maxWidth - gapPx - vw))
            val vp = v.measure(Constraints(maxWidth = vw))
            val h = maxOf(lp.height, vp.height)
            layout(cs.maxWidth, h) {
                lp.place(0, (h - lp.height) / 2)
                vp.place(cs.maxWidth - vp.width, (h - vp.height) / 2)
            }
        } else {
            val lp = l.measure(Constraints(maxWidth = cs.maxWidth))
            val vp = v.measure(Constraints(maxWidth = cs.maxWidth))
            val top = 2.dp.roundToPx()
            layout(cs.maxWidth, lp.height + top + vp.height) {
                lp.place(0, 0)
                vp.place(0, lp.height + top)
            }
        }
    }
}

/** When a label and its value share one line (pure, unit-tested). */
object KeyValueFit {
    fun sideBySide(labelPx: Int, valuePx: Int, gapPx: Int, widthPx: Int): Boolean = labelPx + gapPx + valuePx <= widthPx
}

/** A row of short items that WRAPS instead of squeezing the last one into a column one letter wide. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MetaRow(modifier: Modifier = Modifier, content: @Composable FlowRowScope.() -> Unit) {
    FlowRow(
        modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(Space.s),
        verticalArrangement = Arrangement.spacedBy(Space.xs),
        itemVerticalAlignment = Alignment.CenterVertically,
    ) { content() }
}

@Composable
fun Divider(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(1.dp).background(Sos.colors.line))
}

/** A row title above its content (not a frame around it), with an optional meta on the right. */
@Composable
fun Section(title: String, modifier: Modifier = Modifier, meta: String? = null, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Space.s)) {
        Row(Modifier.fillMaxWidth().padding(top = Space.l, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Overline(title, Modifier.weight(1f))
            if (meta != null) {
                Spacer(Modifier.width(Space.s))
                Text(meta, style = Sos.type.meta, color = Sos.colors.t3)
            }
        }
        content()
    }
}

/** A selectable chip, filled with the text colour when on (one selection colour, like the tabs). */
@Composable
fun Chip(text: String, selected: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier, leading: (@Composable () -> Unit)? = null) {
    val c = Sos.colors
    val shape = RoundedCornerShape(999.dp)
    Row(
        modifier
            .minimumInteractiveComponentSize()
            .clip(shape)
            .background(if (selected) c.t1 else if (c.dark) c.bg2 else c.bg3)
            .border(1.dp, if (selected) c.t1 else c.lineStrong, shape)
            .clickable(role = Role.Checkbox, onClick = onClick)
            .semantics { this.selected = selected }
            .defaultMinSize(minHeight = 40.dp)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (leading != null) {
            leading()
            Spacer(Modifier.width(6.dp))
        }
        Text(text, style = Sos.type.label, color = if (selected) c.onPrimary else c.t2, maxLines = 1)
    }
}

/** A few chips that WRAP (a dial whose selected value would otherwise be cut at the edge). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChipFlow(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    FlowRow(
        modifier.fillMaxWidth().padding(vertical = Space.xs),
        horizontalArrangement = Arrangement.spacedBy(Space.s),
        verticalArrangement = Arrangement.spacedBy(Space.s),
    ) { content() }
}

/** A horizontally scrolling chip row (one line on the 360 dp cover, never three). */
@Composable
fun ChipRow(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Row(
        modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(vertical = Space.xs),
        horizontalArrangement = Arrangement.spacedBy(Space.s),
        verticalAlignment = Alignment.CenterVertically,
    ) { content() }
}
