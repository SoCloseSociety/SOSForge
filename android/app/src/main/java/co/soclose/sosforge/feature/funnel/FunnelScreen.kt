package co.soclose.sosforge.feature.funnel

import android.Manifest
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.sys.Diagnostics
import co.soclose.sosforge.core.watch.WatchMode
import co.soclose.sosforge.core.watch.WatchRules
import co.soclose.sosforge.feature.places.PlaceAdder
import co.soclose.sosforge.feature.places.PlaceCard
import co.soclose.sosforge.feature.settings.SeverityFloorChips
import co.soclose.sosforge.ui.kit.Banner
import co.soclose.sosforge.ui.kit.Breakpoints
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.Chip
import co.soclose.sosforge.ui.kit.ChipFlow
import co.soclose.sosforge.ui.kit.KeyValue
import co.soclose.sosforge.ui.kit.PageHero
import co.soclose.sosforge.ui.kit.PrimaryButton
import co.soclose.sosforge.ui.kit.SecondaryButton
import co.soclose.sosforge.ui.kit.StateBlock
import co.soclose.sosforge.ui.kit.StatusPill
import co.soclose.sosforge.ui.kit.Tone
import co.soclose.sosforge.ui.kit.twoColumns
import co.soclose.sosforge.ui.kit.windowDp
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space

/** The funnel's steps, in order. Pure navigation (FunnelTest): back and skip never strand the owner. */
enum class Step { WELCOME, PLACES, KINDS, NOTIFY, BACKGROUND, DONE;

    fun next(): Step = entries[(ordinal + 1).coerceAtMost(entries.size - 1)]
    fun back(): Step? = if (this == WELCOME) null else entries[ordinal - 1]

    companion object {
        /** The steps that ask something. */
        val QUESTIONS = listOf(PLACES, KINDS, NOTIFY, BACKGROUND)
    }
}

/**
 * Lane K's illustrations (kie.ai nano-banana-pro, 27 Oct 2026; _apk-drops/sosforge/onboarding-art/
 * manifest.json): one per step and theme, 1080 x 806 WebP, their flat background equal to the theme's
 * bg0 (#06080a Lunaire, #f3ecdf Doux), so they sit on the page with no frame. Illustrations, never
 * data: no real map, no marker, no text, no severity colour (the manifest's rule).
 */
internal object OnboardArt {
    @DrawableRes
    fun of(step: Step, dark: Boolean): Int = when (step) {
        Step.WELCOME -> if (dark) R.drawable.onboard_art_welcome_dark else R.drawable.onboard_art_welcome_light
        Step.PLACES -> if (dark) R.drawable.onboard_art_places_dark else R.drawable.onboard_art_places_light
        Step.KINDS -> if (dark) R.drawable.onboard_art_kinds_dark else R.drawable.onboard_art_kinds_light
        Step.NOTIFY -> if (dark) R.drawable.onboard_art_notify_dark else R.drawable.onboard_art_notify_light
        Step.BACKGROUND -> if (dark) R.drawable.onboard_art_background_dark else R.drawable.onboard_art_background_light
        Step.DONE -> if (dark) R.drawable.onboard_art_done_dark else R.drawable.onboard_art_done_light
    }
}

/** The bottom of a step: ONE primary action, then back / skip as quiet actions. */
private class Acts(val primary: String, val onPrimary: () -> Unit, val skip: String? = null, val onSkip: () -> Unit = {}, val onBack: (() -> Unit)? = null)

/**
 * First run (re-enterable from Settings). An overview first, then one question per step, each with
 * what it really produces: a capture of the Live screen ("Preview"), of a real SOSForge
 * notification and of the watch notification ("Example"), and what the watch measurably costs.
 * One primary action per step, back and skip everywhere, a rationale BEFORE every system prompt,
 * and a refusal is a state the funnel goes on from, never a dead end. [start] lets the screenshot
 * tests open any step.
 *
 * Layout: the actions stay pinned under the scrolling step when the window is tall enough
 * ([Breakpoints.SHORT_DP]); on a landscape phone they follow the content. At two-column widths the
 * capture goes beside the words.
 */
@Composable
fun FunnelScreen(onDone: () -> Unit, start: Step = Step.WELCOME) {
    val ctx = LocalContext.current
    val app = ctx.app
    var step by rememberSaveable { mutableStateOf(start) }
    val places by app.prefs.places.collectAsStateWithLifecycle()
    val alerts by app.prefs.alerts.collectAsStateWithLifecycle()
    val watchLive by app.prefs.watchLive.collectAsStateWithLifecycle()
    val running by app.watchRunning.collectAsStateWithLifecycle()
    var notifAsked by rememberSaveable { mutableStateOf(false) }
    // re-read the system's answers whenever the app comes back (from the system settings)
    var resumed by remember0()
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) resumed++ }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }
    val notifOk = resumed >= 0 && Diagnostics.notificationPermission(ctx) && app.notifier.canPost()
    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        notifAsked = true
        resumed++
    }
    val finish = {
        app.prefs.setOnboarded(true)
        app.watch.reconcile()
        onDone()
    }
    BackHandler(enabled = step != Step.WELCOME) { step.back()?.let { step = it } }

    val (w, h) = windowDp()
    val wide = twoColumns(w)
    val pinned = h >= Breakpoints.SHORT_DP
    val showArt = pinned && androidx.compose.ui.platform.LocalDensity.current.fontScale < 1.6f
    val dark = Sos.colors.dark
    val overline = stringResource(R.string.funnel_step, step.ordinal + 1, Step.entries.size)
    val back = { step.back()?.let { step = it }; Unit }
    val askNotif = !notifOk && !notifAsked && Build.VERSION.SDK_INT >= 33

    val acts = when (step) {
        Step.WELCOME -> Acts(stringResource(R.string.welcome_go), { step = step.next() }, stringResource(R.string.funnel_skip_all), finish)
        Step.PLACES -> Acts(stringResource(if (places.isEmpty()) R.string.fplaces_continue_none else R.string.funnel_continue), { step = step.next() }, onBack = back)
        Step.KINDS -> Acts(stringResource(R.string.funnel_continue), { step = step.next() }, onBack = back)
        Step.NOTIFY -> Acts(
            stringResource(if (askNotif) R.string.fnotify_allow else R.string.funnel_continue),
            { if (askNotif) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS) else step = step.next() },
            if (askNotif) stringResource(R.string.not_now) else null, { step = step.next() }, back,
        )
        Step.BACKGROUND -> when {
            // no place: the watch has nothing to watch. The way back to add one comes first (0.1.1 on the
            // owner's Fold: the position failed, no place was added, and this step ended on "Continue"
            // with the watch never started)
            places.isEmpty() -> Acts(stringResource(R.string.fbg_add_place), { step = Step.PLACES }, stringResource(R.string.fbg_continue_without), { step = step.next() }, back)
            !watchLive -> Acts(stringResource(R.string.fbg_turn_on), { app.watch.setLive(true); resumed++ }, stringResource(R.string.fbg_skip), { step = step.next() }, back)
            else -> Acts(stringResource(R.string.funnel_continue), { step = step.next() }, onBack = back)
        }
        Step.DONE -> Acts(stringResource(R.string.fdone_go), finish, onBack = back)
    }

    // the capture beside (wide) or under (narrow) the step's words; null when the step has none
    val side: (@Composable ColumnScope.() -> Unit)? = when (step) {
        Step.WELCOME -> {
            {
                Shot(stringResource(R.string.funnel_preview), R.drawable.onboard_preview_live, stringResource(R.string.welcome_preview_caption), stringResource(R.string.welcome_preview_desc), if (wide) 360.dp else 300.dp)
            }
        }
        Step.NOTIFY -> {
            {
                Shot(stringResource(R.string.funnel_example), R.drawable.onboard_example_alert, stringResource(R.string.fnotify_example_caption), stringResource(R.string.fnotify_example_desc), 440.dp)
            }
        }
        Step.BACKGROUND -> {
            {
                Shot(stringResource(R.string.funnel_example), R.drawable.onboard_example_watch, stringResource(R.string.fbg_example_caption), stringResource(R.string.fbg_example_desc), 440.dp)
            }
        }
        else -> null
    }

    val body: @Composable ColumnScope.() -> Unit = {
        // a landscape phone, and very large text, keep their height for the words and the actions
        if (showArt) Art(OnboardArt.of(step, dark), maxHeight = (h * 0.22f).dp)
        when (step) {
            Step.WELCOME -> {
                PageHero(title = stringResource(R.string.welcome_title), overline = overline, sentence = stringResource(R.string.welcome_sentence))
                Point(Icons.Outlined.Public, stringResource(R.string.welcome_b1))
                Point(Icons.Outlined.NotificationsActive, stringResource(R.string.welcome_b2))
                Point(Icons.Outlined.Lock, stringResource(R.string.welcome_b3))
            }
            Step.PLACES -> {
                PageHero(title = stringResource(R.string.fplaces_title), overline = overline, sentence = stringResource(R.string.fplaces_sentence))
                // the way to add one comes first: at Extra large two place cards fill the cover screen
                if (places.size < co.soclose.sosforge.core.geo.Place.MAX_PLACES) PlaceAdder(onAdd = { app.prefs.setPlaces(places + it) }, modifier = Modifier.padding(bottom = Space.m))
                places.forEach { p ->
                    PlaceCard(
                        p,
                        onChange = { np -> app.prefs.setPlaces(places.map { if (it.id == np.id) np else it }) },
                        onRemove = { app.prefs.setPlaces(places.filter { it.id != p.id }) },
                        modifier = Modifier.padding(bottom = Space.s),
                    )
                }
            }
            Step.KINDS -> {
                PageHero(title = stringResource(R.string.fkinds_title), overline = overline, sentence = stringResource(R.string.fkinds_sentence))
                Text(stringResource(R.string.fkinds_what), style = Sos.type.small, color = Sos.colors.t2)
                ChipFlow {
                    Kind.entries.forEach { k ->
                        val on = k.key in alerts.kinds
                        Chip(stringResource(Words.kinds(k)), on, {
                            val next = if (on) alerts.kinds - k.key else alerts.kinds + k.key
                            // never an empty set: nothing at all would be a silent switch-off
                            if (next.isNotEmpty()) app.prefs.setAlerts(alerts.copy(kinds = next))
                        })
                    }
                }
                Spacer(Modifier.height(Space.m))
                Text(stringResource(R.string.fkinds_floor), style = Sos.type.small, color = Sos.colors.t2)
                SeverityFloorChips(alerts.floor) { app.prefs.setAlerts(alerts.copy(minSeverity = it.key)) }
                Spacer(Modifier.height(Space.s))
                Text(stringResource(R.string.fkinds_note), style = Sos.type.meta, color = Sos.colors.t3)
            }
            Step.NOTIFY -> {
                PageHero(title = stringResource(R.string.fnotify_title), overline = overline, sentence = stringResource(R.string.fnotify_sentence))
                when {
                    notifOk -> Banner(stringResource(R.string.fnotify_on), Tone.OK)
                    notifAsked || !app.notifier.canPost() && Build.VERSION.SDK_INT < 33 -> Card {
                        StateBlock(stringResource(R.string.fnotify_off_title), body = stringResource(R.string.fnotify_off_body), icon = Icons.Outlined.NotificationsOff)
                        SecondaryButton(stringResource(R.string.open_settings), { runCatching { ctx.startActivity(Diagnostics.notificationSettings(ctx)) } })
                    }
                    else -> Text(stringResource(R.string.fnotify_rationale), style = Sos.type.body, color = Sos.colors.t2)
                }
            }
            Step.BACKGROUND -> {
                PageHero(title = stringResource(R.string.fbg_title), overline = overline, sentence = stringResource(R.string.fbg_sentence))
                Card {
                    Text(stringResource(R.string.fbg_live), style = Sos.type.bodyStrong, color = Sos.colors.t1)
                    Text(stringResource(R.string.fbg_live_body), style = Sos.type.small, color = Sos.colors.t2)
                    Spacer(Modifier.height(Space.s))
                    Text(stringResource(R.string.fbg_periodic), style = Sos.type.bodyStrong, color = Sos.colors.t1)
                    Text(stringResource(R.string.fbg_periodic_body), style = Sos.type.small, color = Sos.colors.t2)
                }
                Spacer(Modifier.height(Space.s))
                // what it costs, measured (never a promise): the live socket's bytes on the wire, 27 Oct 2026
                Card {
                    Text(stringResource(R.string.fbg_cost_title), style = Sos.type.bodyStrong, color = Sos.colors.t1)
                    Spacer(Modifier.height(Space.xs))
                    KeyValue(stringResource(R.string.fbg_cost_data), stringResource(R.string.fbg_cost_data_value))
                    Text(stringResource(R.string.fbg_cost_data_note), style = Sos.type.meta, color = Sos.colors.t3)
                    Spacer(Modifier.height(Space.s))
                    KeyValue(stringResource(R.string.fbg_cost_battery), stringResource(R.string.fbg_cost_battery_value))
                    Spacer(Modifier.height(Space.s))
                    KeyValue(stringResource(R.string.fbg_periodic), stringResource(R.string.fbg_cost_check_value))
                }
                Spacer(Modifier.height(Space.s))
                if (places.isEmpty()) {
                    Banner(stringResource(R.string.fbg_no_places), Tone.INFO)
                } else if (watchLive) {
                    val exempt = resumed >= 0 && app.watch.batteryExempt()
                    Banner(stringResource(if (running) R.string.fbg_running else R.string.fbg_not_running), if (running) Tone.OK else Tone.WATCH)
                    Spacer(Modifier.height(Space.s))
                    if (!exempt) {
                        Text(stringResource(R.string.fbg_battery), style = Sos.type.small, color = Sos.colors.t2)
                        Spacer(Modifier.height(Space.s))
                        SecondaryButton(stringResource(R.string.fbg_battery_allow), { runCatching { ctx.startActivity(Diagnostics.batteryExemption(ctx)) } })
                    } else {
                        Banner(stringResource(R.string.fbg_battery_done), Tone.OK)
                    }
                }
            }
            Step.DONE -> {
                PageHero(title = stringResource(R.string.fdone_title), overline = overline, sentence = stringResource(R.string.fdone_sentence))
                Card {
                    KeyValue(stringResource(R.string.fdone_places), pluralStringResource(R.plurals.places_count, places.size, places.size))
                    KeyValue(stringResource(R.string.fdone_alerts), stringResource(R.string.fdone_alerts_value, stringResource(Words.severity(alerts.floor)), alerts.kinds.size))
                    KeyValue(stringResource(R.string.fdone_notify), stringResource(if (notifOk) R.string.diag_granted else R.string.diag_refused))
                    val mode = WatchRules.mode(places.isNotEmpty(), watchLive, running)
                    KeyValue(
                        stringResource(R.string.fdone_watch),
                        stringResource(
                            when (mode) {
                                WatchMode.LIVE -> R.string.watch_short_live
                                WatchMode.PERIODIC -> R.string.watch_short_periodic
                                WatchMode.LIVE_NOT_RUNNING -> R.string.watch_short_not_running
                                WatchMode.OFF -> R.string.watch_short_off
                            },
                        ),
                    )
                }
            }
        }
    }

    val maxWidth = if (wide && side != null) 960.dp else 560.dp
    Column(Modifier.fillMaxSize().background(Sos.colors.bg0).windowInsetsPadding(WindowInsets.safeDrawing).imePadding()) {
        Column(
            Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = Space.xl, vertical = Space.l),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Column(Modifier.widthIn(max = maxWidth).fillMaxWidth()) {
                Progress(step)
                if (wide && side != null) {
                    Row(horizontalArrangement = Arrangement.spacedBy(Space.xl)) {
                        Column(Modifier.weight(1f)) {
                            body()
                            if (!pinned) ActionBlock(acts)
                        }
                        Column(Modifier.weight(0.85f).padding(top = Space.xl)) { side() }
                    }
                } else {
                    body()
                    side?.invoke(this)
                    if (!pinned) ActionBlock(acts)
                }
            }
        }
        if (pinned) {
            Box(Modifier.fillMaxWidth().background(Sos.colors.line).height(1.dp))
            Column(
                Modifier.fillMaxWidth().background(Sos.colors.bg0).padding(horizontal = Space.xl, vertical = Space.s),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Column(Modifier.widthIn(max = 560.dp).fillMaxWidth()) { ActionBlock(acts, top = 0.dp) }
            }
        }
    }
}

@Composable
private fun remember0() = rememberSaveable { mutableIntStateOf(0) }

/** Where the step stands: one segment per step, the done ones in the thread colour. */
@Composable
private fun Progress(step: Step) {
    val n = Step.entries.size
    Row(
        Modifier.fillMaxWidth().padding(bottom = Space.m).clearAndSetSemantics {
            progressBarRangeInfo = ProgressBarRangeInfo((step.ordinal + 1).toFloat(), 1f..n.toFloat(), steps = n - 2)
        },
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Step.entries.forEach { s ->
            Box(
                Modifier.weight(1f).height(4.dp).clip(RoundedCornerShape(2.dp))
                    .background(if (s.ordinal <= step.ordinal) Sos.colors.mint else Sos.colors.lineStrong),
            )
        }
    }
}

/** One overview point: an icon in its disc, then the words (they wrap, at any text size). */
@Composable
private fun Point(icon: ImageVector, text: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.Top) {
        Box(Modifier.size(36.dp).clip(CircleShape).background(Sos.colors.mint.copy(alpha = 0.14f)), contentAlignment = Alignment.Center) {
            Icon(icon, contentDescription = null, tint = Sos.colors.mint, modifier = Modifier.size(20.dp))
        }
        Spacer(Modifier.width(Space.m))
        Text(text, style = Sos.type.body, color = Sos.colors.t1, modifier = Modifier.weight(1f).padding(top = 7.dp))
    }
}

/**
 * A REAL capture, said to be one: "Preview" or "Example" above it, where and when it was taken
 * under it. Never a mock-up (owner, 2026-10-27).
 */
@Composable
private fun Shot(label: String, @DrawableRes res: Int, caption: String, description: String, maxWidth: Dp) {
    val p = painterResource(res)
    val ratio = p.intrinsicSize.let { if (it.height > 0f) it.width / it.height else 1f }
    val shape = RoundedCornerShape(Space.radiusSmall)
    Column(Modifier.fillMaxWidth().padding(top = Space.l), horizontalAlignment = Alignment.CenterHorizontally) {
        Column(Modifier.widthIn(max = maxWidth).fillMaxWidth()) {
            StatusPill(label, Tone.INFO)
            Spacer(Modifier.height(Space.s))
            Image(
                p, contentDescription = description, contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxWidth().aspectRatio(ratio).clip(shape).border(1.dp, Sos.colors.lineStrong, shape),
            )
            Spacer(Modifier.height(Space.s))
            Text(caption, style = Sos.type.meta, color = Sos.colors.t3)
        }
    }
}

/**
 * A step's illustration, whole (the art is 4:3 with wide margins and its background is the page's,
 * so fitting it loses nothing), at most 180 dp and a fifth of the window tall, its corners rounded (step d's table runs off its
 * bottom edge: lane K's note). Decorative: no description.
 */
@Composable
private fun Art(@DrawableRes res: Int, maxHeight: Dp) {
    BoxWithConstraints(Modifier.fillMaxWidth().padding(bottom = Space.m), contentAlignment = Alignment.Center) {
        val h = minOf(maxWidth * 0.5f, 180.dp, maxHeight)
        Image(
            painterResource(res), contentDescription = null, contentScale = ContentScale.Fit,
            modifier = Modifier.height(h).aspectRatio(1080f / 806f).clip(RoundedCornerShape(Space.radius)),
        )
    }
}

/** ONE primary action, then back and skip as quiet actions that wrap instead of overflowing at large text. */
@Composable
private fun ActionBlock(a: Acts, top: Dp = Space.xl) {
    Spacer(Modifier.height(top))
    PrimaryButton(a.primary, a.onPrimary, Modifier.fillMaxWidth())
    if (a.onBack != null || a.skip != null) {
        FlowRow(Modifier.fillMaxWidth().padding(top = Space.xs), horizontalArrangement = Arrangement.SpaceBetween, verticalArrangement = Arrangement.Center) {
            if (a.onBack != null) Quiet(stringResource(R.string.back), a.onBack) else Spacer(Modifier.width(1.dp))
            if (a.skip != null) Quiet(a.skip, a.onSkip, end = true)
        }
    }
}

/** A text action that WRAPS (TextAction is one line by design; a skip label at 200 % is not). */
@Composable
private fun Quiet(text: String, onClick: () -> Unit, end: Boolean = false) {
    Box(
        Modifier.defaultMinSize(minHeight = 48.dp).clickable(role = Role.Button, onClick = onClick).padding(horizontal = 4.dp),
        contentAlignment = if (end) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Text(text, style = Sos.type.label, color = Sos.colors.t2, textAlign = if (end) TextAlign.End else TextAlign.Start)
    }
}
