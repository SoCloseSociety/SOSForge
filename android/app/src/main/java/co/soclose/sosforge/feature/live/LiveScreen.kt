package co.soclose.sosforge.feature.live

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.FilterAltOff
import androidx.compose.material.icons.outlined.TouchApp
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.Prefs
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.alert.AlertRules
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.update.UpdateRules
import co.soclose.sosforge.core.update.UpdateState
import co.soclose.sosforge.feature.common.DataNote
import co.soclose.sosforge.feature.common.EventRow
import co.soclose.sosforge.feature.common.FeedBanner
import co.soclose.sosforge.feature.common.FeedStatusRules
import co.soclose.sosforge.feature.common.LiveFilter
import co.soclose.sosforge.feature.common.NearMe
import co.soclose.sosforge.feature.common.rememberLink
import co.soclose.sosforge.feature.detail.EventDetail
import co.soclose.sosforge.feature.detail.openUrl
import co.soclose.sosforge.ui.kit.Banner
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.Chip
import co.soclose.sosforge.ui.kit.ChipRow
import co.soclose.sosforge.ui.kit.LinkPill
import co.soclose.sosforge.ui.kit.LoadingState
import co.soclose.sosforge.ui.kit.MetaRow
import co.soclose.sosforge.ui.kit.Overline
import co.soclose.sosforge.ui.kit.PageHero
import co.soclose.sosforge.ui.kit.ScreensMode
import co.soclose.sosforge.ui.kit.StateBlock
import co.soclose.sosforge.ui.kit.StatusPill
import co.soclose.sosforge.ui.kit.TextAction
import co.soclose.sosforge.ui.kit.Tone
import co.soclose.sosforge.ui.kit.rememberNow
import co.soclose.sosforge.ui.kit.twoColumns
import co.soclose.sosforge.ui.kit.windowDp
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space
import kotlinx.coroutines.delay

/**
 * Live. Near me by default (the person's position and places), the world's latest otherwise or on
 * demand, newest EVENT first (lesson 5), every age on the server clock, the LIVE pill from the tick
 * watchdog only. In the near view a card keeps counting the severe events ELSEWHERE, so a far
 * tsunami never vanishes. From 780 dp the open event sits beside the list.
 */
@Composable
fun LiveScreen(onOpen: (String) -> Unit, onPlaces: () -> Unit, onMap: (String) -> Unit) {
    val ctx = LocalContext.current
    val app = ctx.app
    val feed by app.feed.collectAsStateWithLifecycle()
    val places by app.prefs.places.collectAsStateWithLifecycle()
    val here by app.prefs.here.collectAsStateWithLifecycle()
    val prefs by app.prefs.alerts.collectAsStateWithLifecycle()
    val version by app.version.collectAsStateWithLifecycle()
    val world by app.world.collectAsStateWithLifecycle()
    val severeWorld by app.severeWorld.collectAsStateWithLifecycle()
    val update by app.update.collectAsStateWithLifecycle()
    val link = rememberLink()
    val deviceNow = rememberNow(1_000L)
    val now = feed.serverNow(deviceNow)
    var window by rememberSaveable { mutableIntStateOf(LiveFilter.DEFAULT_WINDOW) }
    var kinds by rememberSaveable { mutableStateOf(Kind.entries.map { it.key }.toSet()) }
    var selected by rememberSaveable { mutableStateOf<String?>(null) }
    val (w, _) = windowDp()
    val two = twoColumns(w)

    val points = remember(places, here) { NearMe.points(places, here) }
    var nearChoice by rememberSaveable { mutableStateOf<Boolean?>(null) }
    // near by default whenever there is something to be near to; the owner's own choice wins after
    val nearView = points.isNotEmpty() && (nearChoice ?: NearMe.defaultNear(points))
    // a zoned server's socket only carries the zones: the world, and the severe events elsewhere, are REST reads
    val zonedServer = version?.wsScope == true
    val worldFromRest = zonedServer && !nearView
    val severeFromRest = zonedServer && nearView
    val owner = LocalLifecycleOwner.current
    LaunchedEffect(worldFromRest, severeFromRest) {
        if (ScreensMode.on || (!worldFromRest && !severeFromRest)) return@LaunchedEffect
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                app.refreshWorld(severeOnly = severeFromRest)
                delay(60_000L)
            }
        }
    }
    val source = when {
        worldFromRest -> world?.events.orEmpty()
        nearView -> remember(feed.events, points) { NearMe.near(feed.events, points) }
        else -> feed.events
    }
    val elsewhereSource = if (severeFromRest) severeWorld?.events else feed.events
    val elsewhere = remember(elsewhereSource, points, now / 30_000L) { elsewhereSource?.let { NearMe.elsewhereSevere(it, points, now) } }

    // derivations on stable inputs only (lesson 1)
    val visible = remember(source, window, kinds, now / 30_000L) { LiveFilter.apply(source, window, kinds, now) }
    val near = remember(feed.events, places, prefs, now / 30_000L) {
        feed.events.filter { !it.isEnded(now) && AlertRules.isNews(it, now) && AlertRules.match(it, places, prefs) != null }
    }
    var dismissed by remember { mutableIntStateOf(app.prefs.getLong(Prefs.K_UPDATE_DISMISSED).toInt()) }

    val list: @Composable (Modifier) -> Unit = { mod ->
        LazyColumn(mod, contentPadding = PaddingValues(horizontal = Space.gutter, vertical = Space.s), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            item(key = "hero") {
                PageHero(
                    title = stringResource(R.string.live_title),
                    overline = stringResource(if (nearView) R.string.live_overline_near else R.string.live_overline_world),
                    // the world list on a zoned server is a REST read: it never wears the LIVE pill, it says its age
                    trailing = { if (worldFromRest) StatusPill(stringResource(R.string.live_world_pill), Tone.NEUTRAL) else LinkPill(link) },
                    meta = {
                        val n = feed.sources.count { it.connected }
                        Text(stringResource(R.string.live_meta, visible.size, n, feed.sources.size), style = Sos.type.meta, color = Sos.colors.t3)
                    },
                )
            }
            val u = update
            if (u is UpdateState.Available && UpdateRules.showCard(u, dismissed)) item(key = "update") {
                Banner(
                    stringResource(R.string.update_body, u.release.version),
                    Tone.INFO,
                    Modifier.padding(bottom = Space.s),
                    title = stringResource(R.string.update_title),
                    action = {
                        MetaRow {
                            TextAction(stringResource(R.string.update_download), { openUrl(ctx, u.downloadUrl) })
                            TextAction(stringResource(R.string.update_later), {
                                app.prefs.putLong(Prefs.K_UPDATE_DISMISSED, u.release.versionCode.toLong())
                                dismissed = u.release.versionCode
                            }, color = Sos.colors.t2)
                        }
                    },
                )
            }
            item(key = "banner") {
                if (worldFromRest) {
                    val at = world?.at?.takeIf { it > 0 }
                    val age = at?.let { Fmt.age(ctx, (deviceNow - it).coerceAtLeast(0) / 1000) }
                    Banner(
                        when {
                            world?.error != null && age != null -> stringResource(R.string.live_world_unreachable_age, age)
                            world?.error != null -> stringResource(R.string.live_world_unreachable)
                            age != null && points.isEmpty() -> stringResource(R.string.live_world_no_places, age)
                            age != null -> stringResource(R.string.live_world_read, age)
                            else -> stringResource(R.string.live_world_reading)
                        },
                        if (world?.error != null) Tone.WATCH else Tone.INFO,
                        Modifier.padding(bottom = Space.s),
                    )
                } else {
                    FeedBanner(link, feed, Modifier.padding(bottom = Space.s))
                }
            }
            item(key = "view") {
                Column(Modifier.padding(top = Space.xs)) {
                    if (points.isNotEmpty()) {
                        ChipRow {
                            Chip(stringResource(R.string.live_view_near), nearView, { nearChoice = true })
                            Chip(stringResource(R.string.live_view_world), !nearView, { nearChoice = false })
                        }
                    } else {
                        Text(stringResource(R.string.live_world_because), style = Sos.type.small, color = Sos.colors.t3)
                        TextAction(stringResource(R.string.places_add), onPlaces)
                    }
                }
            }
            if (nearView) item(key = "elsewhere") {
                Card(onClick = { nearChoice = false }, modifier = Modifier.padding(vertical = Space.xs)) {
                    Overline(stringResource(R.string.live_elsewhere_overline))
                    Text(
                        when {
                            elsewhere == null -> stringResource(R.string.live_elsewhere_reading)
                            elsewhere.isEmpty() -> stringResource(R.string.live_elsewhere_none)
                            else -> pluralStringResource(R.plurals.live_elsewhere_count, elsewhere.size, elsewhere.size)
                        },
                        style = Sos.type.bodyStrong,
                        color = if (elsewhere.isNullOrEmpty()) Sos.colors.t2 else Sos.colors.t1,
                    )
                    if (!elsewhere.isNullOrEmpty()) {
                        // the most severe one, by name: a far tsunami is SAID, not just counted
                        val top = elsewhere.maxWith(compareBy({ AlertRules.rankOf(it) }, { it.timeMs ?: 0L }))
                        Text(
                            stringResource(Words.severity(top.severityOf)) + " · " + stringResource(Words.kind(top.kindOf)) + " · " + top.displayPlace,
                            style = Sos.type.small, color = Sos.colors.t2, maxLines = 2,
                        )
                        TextAction(stringResource(R.string.live_elsewhere_show), { nearChoice = false })
                    }
                }
            }
            if (nearView) item(key = "near") {
                Card(onClick = onPlaces, modifier = Modifier.padding(vertical = Space.xs)) {
                    Overline(stringResource(R.string.live_near_overline))
                    Text(
                        when {
                            places.isEmpty() -> stringResource(R.string.live_near_none_places)
                            feed.snapshotRemaining > 0 -> stringResource(R.string.live_loading_rest)
                            near.isEmpty() -> stringResource(R.string.live_near_quiet)
                            else -> pluralStringResource(R.plurals.live_near_count, near.size, near.size)
                        },
                        style = Sos.type.bodyStrong, color = Sos.colors.t1,
                    )
                }
            }
            item(key = "filters") {
                Column(Modifier.padding(top = Space.s)) {
                    ChipRow {
                        LiveFilter.WINDOWS.forEach { m -> Chip(stringResource(windowLabel(m)), window == m, { window = m }) }
                    }
                    ChipRow {
                        val all = kinds.size == Kind.entries.size
                        Chip(stringResource(R.string.filter_all_kinds), all, { kinds = Kind.entries.map { it.key }.toSet() })
                        Kind.entries.forEach { k ->
                            Chip(stringResource(Words.kinds(k)), !all && k.key in kinds, {
                                kinds = if (all) setOf(k.key) else if (k.key in kinds) (kinds - k.key).ifEmpty { Kind.entries.map { it.key }.toSet() } else kinds + k.key
                            })
                        }
                    }
                }
            }
            when {
                !worldFromRest && FeedStatusRules.note(link, feed) == DataNote.WAITING -> item(key = "wait") { LoadingState() }
                worldFromRest && world == null -> item(key = "wait") { LoadingState() }
                !worldFromRest && visible.isEmpty() && feed.snapshotRemaining > 0 -> item(key = "wait") { LoadingState() }
                visible.isEmpty() -> item(key = "empty") {
                    StateBlock(
                        stringResource(
                            when {
                                nearView && source.isEmpty() -> R.string.live_empty_near
                                source.isEmpty() -> R.string.live_empty_title
                                else -> R.string.live_empty_filtered
                            },
                        ),
                        body = stringResource(
                            when {
                                nearView && source.isEmpty() -> R.string.live_empty_near_body
                                source.isEmpty() -> R.string.live_empty_body
                                window > 0 -> R.string.live_empty_window
                                else -> R.string.live_empty_kinds
                            },
                        ),
                        icon = Icons.Outlined.FilterAltOff,
                    )
                }
                else -> items(visible, key = { it.id }) { e ->
                    EventRow(e, now, places, selected = two && selected == e.id, onClick = {
                        if (two) selected = e.id else onOpen(e.id)
                    })
                }
            }
        }
    }

    if (!two) {
        list(Modifier.fillMaxSize())
    } else {
        Row(Modifier.fillMaxSize()) {
            list(Modifier.weight(1f).fillMaxHeight())
            Box(Modifier.padding(vertical = Space.l).width(1.dp).fillMaxHeight().background(Sos.colors.line))
            Box(Modifier.weight(1f).fillMaxHeight()) {
                val id = selected
                if (id != null && (feed.byId(id) != null || world?.events?.any { it.id == id } == true)) {
                    EventDetail(id, onBack = { selected = null }, onMap = { onMap(id) }, showBack = false)
                } else {
                    StateBlock(
                        stringResource(R.string.live_pick_title),
                        body = stringResource(R.string.live_pick_body),
                        icon = Icons.Outlined.TouchApp,
                        modifier = Modifier.fillMaxWidth().padding(top = 80.dp),
                    )
                }
            }
        }
    }
}

fun windowLabel(m: Int): Int = when (m) {
    15 -> R.string.window_15
    60 -> R.string.window_60
    360 -> R.string.window_360
    1440 -> R.string.window_1440
    else -> R.string.window_all
}
