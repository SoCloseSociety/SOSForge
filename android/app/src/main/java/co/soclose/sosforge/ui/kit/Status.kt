package co.soclose.sosforge.ui.kit

// Kit: GStatsForge android 0.2.4 (GStats commit cdb56f0), ui/kit/Status.kt. Kept: Tone, StatusPill,
// Banner, StateBlock, LoadingState, rememberNow. Added: ShowMore (owner rule 2026-10-10: long secondary
// detail hidden behind "Show more"), LinkPill (the LIVE pill, driven only by the tick watchdog).

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import co.soclose.sosforge.R
import co.soclose.sosforge.core.live.Link
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space
import kotlinx.coroutines.delay

/** App states (never a severity: those have their own palette and glyphs). */
enum class Tone { OK, WATCH, ALERT, INFO, NEUTRAL }

@Composable
fun toneColor(tone: Tone): Color {
    val c = Sos.colors
    return when (tone) {
        Tone.OK -> c.mint
        Tone.WATCH -> c.amber
        Tone.ALERT -> c.red
        Tone.INFO -> c.cyan
        Tone.NEUTRAL -> c.t2
    }
}

private fun glyph(tone: Tone) = when (tone) {
    Tone.OK -> "✓"
    Tone.WATCH -> "○"
    Tone.ALERT -> "×"
    Tone.INFO -> "i"
    Tone.NEUTRAL -> "·"
}

/** A status pill: glyph + word in mono capitals, never colour alone. */
@Composable
fun StatusPill(text: String, tone: Tone, modifier: Modifier = Modifier) {
    val col = toneColor(tone)
    val shape = RoundedCornerShape(999.dp)
    Row(
        modifier
            .background(col.copy(alpha = 0.14f), shape)
            .border(1.dp, col.copy(alpha = 0.4f), shape)
            .padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(glyph(tone), style = Sos.type.meta.copy(fontFamily = FontFamily.Default), color = col)
        Spacer(Modifier.width(6.dp))
        Text(text.uppercase(), style = Sos.type.pill, color = col, maxLines = 1, softWrap = false, overflow = TextOverflow.Ellipsis)
    }
}

/**
 * THE live pill. It says LIVE only when [link] is [Link.LIVE], which the tick watchdog alone decides
 * (core/live/Watchdog.kt). Every other state is said in words: connecting, reconnecting, server busy,
 * offline. A point (not a pulse): no infinite animation, so screenshots never hang on it.
 */
@Composable
fun LinkPill(link: Link, modifier: Modifier = Modifier) {
    val (tone, word) = when (link) {
        Link.LIVE -> Tone.OK to stringResource(R.string.link_live)
        Link.CONNECTING -> Tone.NEUTRAL to stringResource(R.string.link_connecting)
        Link.RECONNECTING -> Tone.WATCH to stringResource(R.string.link_reconnecting)
        Link.BUSY -> Tone.WATCH to stringResource(R.string.link_busy)
        Link.OFFLINE -> Tone.NEUTRAL to stringResource(R.string.link_offline)
    }
    val col = toneColor(tone)
    val shape = RoundedCornerShape(999.dp)
    Row(
        modifier
            .background(col.copy(alpha = 0.14f), shape)
            .border(1.dp, col.copy(alpha = 0.4f), shape)
            .padding(horizontal = 10.dp, vertical = 4.dp)
            .semantics { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).background(col, CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(word.uppercase(), style = Sos.type.pill, color = col, maxLines = 1, softWrap = false)
    }
}

/** Ticks so ages on screen stay true without a new read (frozen in previews and screenshots). */
@Composable
fun rememberNow(periodMs: Long = 1_000L): Long {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val still = LocalInspectionMode.current || ScreensMode.on
    LaunchedEffect(periodMs, still) {
        if (still) return@LaunchedEffect
        while (true) {
            delay(periodMs)
            now = System.currentTimeMillis()
        }
    }
    return now
}

/** Screenshot tests switch every ticking clock off (an endless clock never lets Compose go idle). */
object ScreensMode {
    @Volatile var on: Boolean = false
}

@Composable
fun Banner(text: String, tone: Tone, modifier: Modifier = Modifier, title: String? = null, action: (@Composable () -> Unit)? = null) {
    val col = toneColor(tone)
    val shape = RoundedCornerShape(Space.radiusSmall)
    val icon: ImageVector = when (tone) {
        Tone.ALERT -> Icons.Outlined.ErrorOutline
        Tone.WATCH -> Icons.Outlined.WarningAmber
        else -> Icons.Outlined.Info
    }
    Row(
        modifier
            .fillMaxWidth()
            .background(col.copy(alpha = 0.08f), shape)
            .border(1.dp, col.copy(alpha = 0.32f), shape)
            .padding(Space.m)
            .semantics { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.Top,
    ) {
        Icon(icon, null, Modifier.size(20.dp), tint = col)
        Spacer(Modifier.width(Space.s))
        Column(Modifier.weight(1f)) {
            if (title != null) Text(title, style = Sos.type.bodyStrong, color = Sos.colors.t1)
            Text(text, style = Sos.type.small, color = Sos.colors.t2)
            if (action != null) action()
        }
    }
}

/** Waiting, said honestly: a spinner at once, and past 3 s "Slow connection" with the time elapsed. */
@Composable
fun LoadingState(modifier: Modifier = Modifier) {
    val started = remember { System.currentTimeMillis() }
    val elapsed = (rememberNow(1_000L) - started) / 1000
    Column(
        modifier.fillMaxWidth().padding(vertical = Space.xl),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Space.s),
    ) {
        if (!ScreensMode.on) CircularProgressIndicator(Modifier.size(24.dp), color = Sos.colors.t2, strokeWidth = 2.5.dp)
        if (elapsed >= 3) Text(stringResource(R.string.slow, elapsed.toInt()), style = Sos.type.meta, color = Sos.colors.amber)
    }
}

/** Empty / error / offline: a glyph, a title, one sentence, one action. One per page. */
@Composable
fun StateBlock(
    title: String,
    modifier: Modifier = Modifier,
    body: String? = null,
    icon: ImageVector = Icons.Outlined.Inbox,
    tone: Tone = Tone.NEUTRAL,
    action: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier.fillMaxWidth().padding(vertical = 32.dp, horizontal = Space.l),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Space.s),
    ) {
        Icon(icon, null, Modifier.size(32.dp), tint = toneColor(tone))
        Text(title, style = Sos.type.heading, color = Sos.colors.t1, textAlign = TextAlign.Center, modifier = Modifier.widthIn(max = 420.dp))
        if (body != null) Text(body, style = Sos.type.small, color = Sos.colors.t2, textAlign = TextAlign.Center, modifier = Modifier.widthIn(max = 420.dp))
        if (action != null) {
            Spacer(Modifier.height(Space.s))
            action()
        }
    }
}

/**
 * Long secondary text (an agency's description, a CAP instruction, a source error): the first lines,
 * then "Show more". Nothing long ever stretches a card (owner rule, 2026-10-10).
 */
@Composable
fun ShowMore(text: String, modifier: Modifier = Modifier, collapsedLines: Int = 3, mono: Boolean = false, color: Color? = null) {
    var open by rememberSaveable(text) { mutableStateOf(false) }
    var overflows by remember(text) { mutableStateOf(false) }
    val more = stringResource(R.string.show_more)
    val less = stringResource(R.string.show_less)
    Column(modifier.fillMaxWidth().animateContentSize()) {
        Text(
            text,
            style = if (mono) Sos.type.mono else Sos.type.small,
            color = color ?: Sos.colors.t2,
            maxLines = if (open) Int.MAX_VALUE else collapsedLines,
            overflow = TextOverflow.Ellipsis,
            onTextLayout = { if (!open) overflows = it.hasVisualOverflow },
        )
        if (overflows || open) {
            TextAction(if (open) less else more, { open = !open }, Modifier.semantics { contentDescription = if (open) less else more })
        }
    }
}
