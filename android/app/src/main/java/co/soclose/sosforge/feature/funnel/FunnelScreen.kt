package co.soclose.sosforge.feature.funnel

import android.Manifest
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.NotificationsOff
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
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
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.Chip
import co.soclose.sosforge.ui.kit.ChipFlow
import co.soclose.sosforge.ui.kit.KeyValue
import co.soclose.sosforge.ui.kit.PageHero
import co.soclose.sosforge.ui.kit.PrimaryButton
import co.soclose.sosforge.ui.kit.SecondaryButton
import co.soclose.sosforge.ui.kit.StateBlock
import co.soclose.sosforge.ui.kit.TextAction
import co.soclose.sosforge.ui.kit.Tone
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space

/** The funnel's steps, in order. Pure navigation (FunnelTest): back and skip never strand the owner. */
enum class Step { WELCOME, PLACES, KINDS, NOTIFY, BACKGROUND, DONE;

    fun next(): Step = entries[(ordinal + 1).coerceAtMost(entries.size - 1)]
    fun back(): Step? = if (this == WELCOME) null else entries[ordinal - 1]

    companion object {
        /** The steps that ask something (shown as "step n of 4"). */
        val QUESTIONS = listOf(PLACES, KINDS, NOTIFY, BACKGROUND)
    }
}

/**
 * First run (re-enterable from Settings): one primary action per step, back and skip everywhere, a
 * rationale BEFORE every system prompt, and a refusal is a state the funnel goes on from, never a dead
 * end. [start] lets the screenshot tests open any step.
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

    Box(Modifier.fillMaxSize().background(Sos.colors.bg0).windowInsetsPadding(WindowInsets.safeDrawing).imePadding()) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Space.xl, vertical = Space.l),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Column(Modifier.widthIn(max = 560.dp).fillMaxWidth()) {
                val overline = if (step in Step.QUESTIONS) stringResource(R.string.funnel_step, Step.QUESTIONS.indexOf(step) + 1, Step.QUESTIONS.size) else stringResource(R.string.app_name)
                when (step) {
                    Step.WELCOME -> {
                        PageHero(title = stringResource(R.string.welcome_title), overline = overline, sentence = stringResource(R.string.welcome_sentence))
                        Bullet(stringResource(R.string.welcome_b1))
                        Bullet(stringResource(R.string.welcome_b2))
                        Bullet(stringResource(R.string.welcome_b3))
                        Actions(primary = stringResource(R.string.welcome_go), onPrimary = { step = step.next() }, skip = stringResource(R.string.funnel_skip_all), onSkip = finish)
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
                        Actions(
                            primary = stringResource(if (places.isEmpty()) R.string.fplaces_continue_none else R.string.funnel_continue),
                            onPrimary = { step = step.next() },
                            onBack = { step = Step.WELCOME },
                        )
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
                        Actions(primary = stringResource(R.string.funnel_continue), onPrimary = { step = step.next() }, onBack = { step = step.back()!! })
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
                        val ask = !notifOk && !notifAsked && Build.VERSION.SDK_INT >= 33
                        Actions(
                            primary = stringResource(if (ask) R.string.fnotify_allow else R.string.funnel_continue),
                            onPrimary = { if (ask) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS) else step = step.next() },
                            skip = if (ask) stringResource(R.string.not_now) else null,
                            onSkip = { step = step.next() },
                            onBack = { step = step.back()!! },
                        )
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
                        if (places.isEmpty()) {
                            Banner(stringResource(R.string.fbg_no_places), Tone.INFO)
                            Actions(primary = stringResource(R.string.funnel_continue), onPrimary = { step = step.next() }, onBack = { step = step.back()!! })
                        } else if (!watchLive) {
                            Actions(
                                primary = stringResource(R.string.fbg_turn_on),
                                onPrimary = { app.watch.setLive(true); resumed++ },
                                skip = stringResource(R.string.fbg_skip),
                                onSkip = { step = step.next() },
                                onBack = { step = step.back()!! },
                            )
                        } else {
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
                            Actions(primary = stringResource(R.string.funnel_continue), onPrimary = { step = step.next() }, onBack = { step = step.back()!! })
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
                        Actions(primary = stringResource(R.string.fdone_go), onPrimary = finish, onBack = { step = step.back()!! })
                    }
                }
            }
        }
    }
}

@Composable
private fun remember0() = rememberSaveable { mutableIntStateOf(0) }

@Composable
private fun Bullet(text: String) {
    Row(Modifier.padding(vertical = 4.dp)) {
        Text("•", style = Sos.type.body, color = Sos.colors.mint)
        Spacer(Modifier.width(Space.s))
        Text(text, style = Sos.type.body, color = Sos.colors.t1)
    }
}

/** The bottom of a step: ONE primary action, then back / skip as quiet text actions. */
@Composable
private fun Actions(primary: String, onPrimary: () -> Unit, skip: String? = null, onSkip: () -> Unit = {}, onBack: (() -> Unit)? = null) {
    Spacer(Modifier.height(Space.xl))
    PrimaryButton(primary, onPrimary, Modifier.fillMaxWidth())
    Row(Modifier.fillMaxWidth().padding(top = Space.s), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        if (onBack != null) TextAction(stringResource(R.string.back), onBack, color = Sos.colors.t2) else Spacer(Modifier.width(1.dp))
        if (skip != null) TextAction(skip, onSkip, color = Sos.colors.t2)
    }
}
