package co.soclose.sosforge.feature.common

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import co.soclose.sosforge.R
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.feed.isCurrentAlert
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.ui.kit.MetaRow
import co.soclose.sosforge.ui.kit.SeverityBadge
import co.soclose.sosforge.ui.kit.SeverityGlyph
import co.soclose.sosforge.ui.kit.StatusPill
import co.soclose.sosforge.ui.kit.Tone
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space

/** The Live list's filter, pure (LiveFilterTest). */
object LiveFilter {
    /** frontend/src/store.ts WINDOWS, in minutes; 0 = all the server keeps. */
    val WINDOWS = listOf(15, 60, 360, 1440, 0)
    const val DEFAULT_WINDOW = 1440

    /**
     * The time window cuts POINT events only. A running alert carries the time it was issued, often
     * days ago, and stays relevant while it runs: a window on event time dropped 264 of 346 running
     * alerts on the web (audit 2026-10-26). An ended one is cut like a point event.
     */
    fun apply(events: List<SosEvent>, windowMin: Int, kinds: Set<String>, serverNow: Long): List<SosEvent> {
        val cutoff = if (windowMin > 0) serverNow - windowMin * 60_000L else null
        return events.filter { e ->
            val kindOk = e.kind in kinds || (e.tsunami && Kind.TSUNAMI.key in kinds)
            val inWindow = cutoff == null || e.isCurrentAlert(serverNow) || (e.timeMs ?: Long.MIN_VALUE) >= cutoff
            kindOk && inWindow
        }
    }

    /** The nearest of the owner's places to an event, with its distance. */
    fun nearest(e: SosEvent, places: List<Place>): Pair<Place, Double>? =
        places.mapNotNull { p -> Geo.distanceKm(p, e)?.let { p to it } }.minByOrNull { it.second }
}

/** One event in a list: severity glyph + word, the figure, the place, the age on the SERVER clock. */
@Composable
fun EventRow(e: SosEvent, serverNow: Long, places: List<Place>, selected: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val c = Sos.colors
    val ctx = LocalContext.current
    val ended = e.isEnded(serverNow)
    val age = e.timeMs?.let { Fmt.age(ctx, (serverNow - it) / 1000) }
    val near = LiveFilter.nearest(e, places)
    Row(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(Space.radiusSmall))
            .then(if (selected) Modifier.background(c.bg3) else Modifier)
            .clickable(role = Role.Button, onClick = onClick)
            .defaultMinSize(minHeight = 64.dp)
            .padding(horizontal = Space.s, vertical = 10.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Column(Modifier.widthIn(min = 52.dp, max = 64.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            if (e.magnitude != null) {
                Text(Fmt.magnitude(e.magnitude), style = Sos.type.figureSmall, color = c.sev.of(e.severityOf), maxLines = 1)
                Text(e.magType?.uppercase() ?: "MAG", style = Sos.type.meta, color = c.t3, maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
            } else {
                // no figure to show: the severity's own shape, larger (the kind is already said on the right)
                SeverityGlyph(e.severityOf, Modifier.padding(top = 4.dp), size = 18.dp)
            }
        }
        Spacer(Modifier.width(Space.m))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            MetaRow {
                SeverityBadge(e.severityOf)
                Text(stringResource(Words.kind(e.kindOf)), style = Sos.type.small, color = c.t2, maxLines = 1)
                if (e.tsunami && e.kindOf != Kind.TSUNAMI) StatusPill(stringResource(R.string.tag_tsunami), Tone.ALERT)
                when {
                    ended -> StatusPill(stringResource(R.string.tag_ended), Tone.NEUTRAL)
                    e.isUpcoming(serverNow) -> StatusPill(stringResource(R.string.tag_upcoming), Tone.INFO)
                    e.isCurrentAlert(serverNow) -> StatusPill(stringResource(R.string.tag_running), Tone.INFO)
                }
                if (e.preliminary) StatusPill(stringResource(R.string.tag_preliminary), Tone.NEUTRAL)
            }
            Text(
                listOfNotNull(Fmt.flag(e.countryCode), e.displayPlace).joinToString(" "),
                style = Sos.type.bodyStrong, color = if (ended) c.t2 else c.t1, maxLines = 2, overflow = TextOverflow.Ellipsis,
            )
            Text(
                listOfNotNull(
                    age,
                    Words.source(e.source),
                    near?.let { stringResource(R.string.distance_from, Fmt.km(ctx, it.second), it.first.name) },
                ).joinToString(" · "),
                style = Sos.type.meta, color = c.t3, maxLines = 3, overflow = TextOverflow.Ellipsis,
            )
        }
    }
}
