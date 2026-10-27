package co.soclose.sosforge.feature.detail

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Map
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.feed.isCurrentAlert
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.net.GuidanceLine
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.KeyValue
import co.soclose.sosforge.ui.kit.LinkText
import co.soclose.sosforge.ui.kit.MetaRow
import co.soclose.sosforge.ui.kit.PrimaryButton
import co.soclose.sosforge.ui.kit.SecondaryButton
import co.soclose.sosforge.ui.kit.Section
import co.soclose.sosforge.ui.kit.SeverityBadge
import co.soclose.sosforge.ui.kit.ShowMore
import co.soclose.sosforge.ui.kit.StateBlock
import co.soclose.sosforge.ui.kit.StatusPill
import co.soclose.sosforge.ui.kit.TextAction
import co.soclose.sosforge.ui.kit.Tone
import co.soclose.sosforge.ui.kit.rememberNow
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space
import java.net.URLEncoder

/** The web's deep link (frontend/src/deeplink.ts: `#e/<encoded id>`), so a shared link opens the same event. */
fun eventLink(origin: String, id: String): String = origin.trimEnd('/') + "/#e/" + URLEncoder.encode(id, "UTF-8").replace("+", "%20")

fun openUrl(ctx: Context, url: String) {
    if (!url.startsWith("https://") && !url.startsWith("http://")) return
    try {
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
        // no browser: nothing to open, nothing to break
    }
}

/**
 * One event: what, where, when (server clock), how far from each place, what the agency says to do,
 * what to do before / during / after (official sources), and who reported it. ONE primary action:
 * show it on the map. A withdrawn event says so instead of showing stale data.
 */
@Composable
fun EventDetail(id: String, onBack: () -> Unit, onMap: () -> Unit, showBack: Boolean = true) {
    val ctx = LocalContext.current
    val app = ctx.app
    val feed by app.feed.collectAsStateWithLifecycle()
    val places by app.prefs.places.collectAsStateWithLifecycle()
    val guidance by app.guidance.collectAsStateWithLifecycle()
    val version by app.version.collectAsStateWithLifecycle()
    val now = feed.serverNow(rememberNow(1_000L))
    val world by app.world.collectAsStateWithLifecycle()
    // an event opened from the world list (a zoned server) is not in the zoned feed
    val e = feed.byId(id) ?: world?.events?.firstOrNull { it.id == id }
    LaunchedEffect(version) { if (guidance == null && version != null) app.refreshGuidance() }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Space.gutter, vertical = Space.s)) {
        if (showBack) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.back), tint = Sos.colors.t1) }
                Text(stringResource(R.string.back), style = Sos.type.label, color = Sos.colors.t2)
            }
        }
        if (e == null) {
            StateBlock(stringResource(R.string.detail_gone_title), body = stringResource(R.string.detail_gone_body), icon = Icons.Outlined.WarningAmber)
            return@Column
        }
        Header(e, now)
        Spacer(Modifier.height(Space.l))
        // wraps at Extra large on the 360 dp cover instead of pushing Share off the card
        MetaRow {
            PrimaryButton(stringResource(R.string.detail_show_map), onMap, enabled = e.hasPosition, icon = Icons.Outlined.Map)
            SecondaryButton(stringResource(R.string.detail_share), { share(ctx, e, app.prefs.origin.value) }, icon = Icons.Outlined.Share)
        }

        Section(stringResource(R.string.detail_where)) {
            Card {
                Text(e.displayPlace, style = Sos.type.bodyStrong, color = Sos.colors.t1)
                e.region?.takeIf { it.isNotBlank() && it != e.place }?.let { Text(it, style = Sos.type.small, color = Sos.colors.t2) }
                val country = Fmt.country(e.countryCode, LocalConfiguration.current.locales[0])
                if (country != null) KeyValue(stringResource(R.string.detail_country), listOfNotNull(Fmt.flag(e.countryCode), country).joinToString(" "))
                if (e.hasPosition) KeyValue(stringResource(R.string.detail_position), Fmt.latLon(e.lat!!, e.lon!!))
                else Text(stringResource(R.string.detail_no_position), style = Sos.type.small, color = Sos.colors.t3)
                e.depthKm?.let { KeyValue(stringResource(R.string.detail_depth), Fmt.km(ctx, it)) }
            }
        }

        if (places.isNotEmpty()) {
            Section(stringResource(R.string.detail_from_places)) {
                Card {
                    places.forEach { p ->
                        val d = Geo.distanceKm(p, e)
                        val inside = d != null && d <= Geo.reachKm(p, e)
                        KeyValue(
                            p.name,
                            when {
                                d == null && p.countryWide && p.countryCode != null && p.countryCode.equals(e.countryCode, ignoreCase = true) ->
                                    stringResource(R.string.detail_country_wide, Fmt.country(e.countryCode, LocalConfiguration.current.locales[0]) ?: p.countryCode)
                                d == null -> stringResource(R.string.detail_distance_unknown)
                                inside -> stringResource(R.string.detail_inside, Fmt.km(ctx, d))
                                else -> stringResource(R.string.detail_outside, Fmt.km(ctx, d), p.radiusKm)
                            },
                            valueColor = if (inside) Sos.colors.t1 else Sos.colors.t2,
                        )
                    }
                }
            }
        }

        Section(stringResource(R.string.detail_todo)) {
            Card {
                val resp = Words.response(e.responseType)
                if (resp != null) Text(stringResource(resp), style = Sos.type.heading, color = Sos.colors.t1)
                else if (!e.responseType.isNullOrBlank()) Text(e.responseType, style = Sos.type.heading, color = Sos.colors.t1)
                if (!e.instruction.isNullOrBlank()) {
                    Spacer(Modifier.height(Space.xs))
                    ShowMore(co.soclose.sosforge.core.net.Entities.decode(e.instruction), collapsedLines = 4, color = Sos.colors.t1)
                }
                if (!e.description.isNullOrBlank()) {
                    Spacer(Modifier.height(Space.s))
                    ShowMore(co.soclose.sosforge.core.net.Entities.decode(e.description), collapsedLines = 3)
                }
                if (resp == null && e.responseType.isNullOrBlank() && e.instruction.isNullOrBlank() && e.description.isNullOrBlank()) {
                    Text(stringResource(R.string.detail_no_instruction), style = Sos.type.small, color = Sos.colors.t3)
                }
                if (e.waveMaxM != null) KeyValue(stringResource(R.string.detail_wave_max), "%.1f m".format(e.waveMaxM) + (e.waveMaxSite?.let { " · $it" } ?: ""))
                if (e.waveEta != null) KeyValue(stringResource(R.string.detail_wave_eta), (e.waveEtaSite?.let { "$it · " } ?: "") + e.waveEta)
            }
        }

        Section(stringResource(R.string.detail_guidance)) {
            val g = guidance?.of(e.kind) ?: if (e.tsunami) guidance?.of(Kind.TSUNAMI.key) else null
            if (g == null) {
                Text(
                    stringResource(if (version?.has(co.soclose.sosforge.core.net.ApiVersion.GUIDANCE) == true) R.string.detail_guidance_none_kind else R.string.detail_guidance_absent),
                    style = Sos.type.small, color = Sos.colors.t3,
                )
            } else {
                Phase(stringResource(R.string.phase_before), g.before)
                Phase(stringResource(R.string.phase_during), g.during)
                Phase(stringResource(R.string.phase_after), g.after)
            }
        }

        Section(stringResource(R.string.detail_sources)) {
            Card {
                KeyValue(stringResource(R.string.detail_reported_by), Words.source(e.source))
                val also = e.clusterId?.let { feed.clusterSources[it] }.orEmpty().filter { it != e.source }.map { Words.source(it) }
                if (also.isNotEmpty()) KeyValue(stringResource(R.string.detail_also), also.joinToString(", "))
                e.timeMs?.let { KeyValue(stringResource(R.string.detail_time_utc), Fmt.utc(it)) }
                if (e.revision > 0) KeyValue(stringResource(R.string.detail_revision), e.revision.toString())
                e.url?.takeIf { it.startsWith("http") }?.let { u -> TextAction(stringResource(R.string.detail_official), { openUrl(ctx, u) }) }
            }
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun Header(e: SosEvent, now: Long) {
    val ctx = LocalContext.current
    MetaRow {
        SeverityBadge(e.severityOf, pill = true)
        Text(stringResource(Words.kind(e.kindOf)), style = Sos.type.label, color = Sos.colors.t2)
        if (e.tsunami && e.kindOf != Kind.TSUNAMI) StatusPill(stringResource(R.string.tag_tsunami), Tone.ALERT)
        when {
            e.isEnded(now) -> StatusPill(stringResource(R.string.tag_ended), Tone.NEUTRAL)
            e.isUpcoming(now) -> StatusPill(stringResource(R.string.tag_upcoming), Tone.INFO)
            e.isCurrentAlert(now) -> StatusPill(stringResource(R.string.tag_running), Tone.INFO)
        }
    }
    Spacer(Modifier.height(Space.s))
    Text(e.displayPlace, style = Sos.type.display, color = Sos.colors.t1, modifier = Modifier.semantics { heading() })
    if (e.magnitude != null) {
        Text(
            "M" + Fmt.magnitude(e.magnitude) + (e.magType?.let { " $it" } ?: ""),
            style = Sos.type.figure, color = Sos.colors.sev.of(e.severityOf),
        )
    }
    val t = e.timeMs
    if (t != null) {
        Text(
            Fmt.age(ctx, (now - t) / 1000) + " · " + Fmt.utc(t) + " · " + stringResource(R.string.detail_local, Fmt.localClock(t)),
            style = Sos.type.meta, color = Sos.colors.t3,
        )
    }
    if (e.isEnded(now)) {
        Spacer(Modifier.height(Space.xs))
        Text(stringResource(R.string.detail_ended), style = Sos.type.small, color = Sos.colors.t2)
    }
    if (e.preliminary) {
        Spacer(Modifier.height(Space.xs))
        Text(stringResource(R.string.detail_preliminary), style = Sos.type.small, color = Sos.colors.t2)
    }
}

@Composable
private fun Phase(title: String, lines: List<GuidanceLine>) {
    if (lines.isEmpty()) return
    val ctx = LocalContext.current
    var all by rememberSaveable(title) { mutableStateOf(false) }
    val shown = remember(lines, all) { if (all) lines else lines.take(3) }
    Card(Modifier.padding(bottom = Space.s)) {
        Text(title, style = Sos.type.heading, color = Sos.colors.t1)
        shown.forEach { l ->
            Spacer(Modifier.height(Space.s))
            Row {
                Text("•", style = Sos.type.body, color = Sos.colors.t3)
                Spacer(Modifier.width(Space.s))
                Column(Modifier.weight(1f)) {
                    Text(l.text, style = Sos.type.body, color = Sos.colors.t1)
                    if (l.url != null) {
                        LinkText(
                            stringResource(R.string.guidance_source, l.source ?: Uri.parse(l.url).host ?: l.url),
                            { openUrl(ctx, l.url) },
                        )
                    }
                }
            }
        }
        if (lines.size > 3) TextAction(stringResource(if (all) R.string.show_less else R.string.show_more), { all = !all })
    }
}

private fun share(ctx: Context, e: SosEvent, origin: String) {
    val sev = ctx.getString(Words.severity(e.severityOf))
    val kind = ctx.getString(Words.kind(e.kindOf))
    val time = e.timeMs?.let { Fmt.utc(it) } ?: ""
    val mag = e.magnitude?.let { " M" + Fmt.magnitude(it) } ?: ""
    val text = "$sev · $kind$mag · ${e.displayPlace} · $time\n${eventLink(origin, e.id)}"
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
    try {
        ctx.startActivity(Intent.createChooser(send, null).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
    }
}
