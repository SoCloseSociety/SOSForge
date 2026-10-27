package co.soclose.sosforge.feature.places

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AddLocationAlt
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.feed.isCurrentAlert
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.feature.common.EventRow
import co.soclose.sosforge.ui.kit.Overline
import co.soclose.sosforge.ui.kit.PageHero
import co.soclose.sosforge.ui.kit.PrimaryButton
import co.soclose.sosforge.ui.kit.StateBlock
import co.soclose.sosforge.ui.kit.TextAction
import co.soclose.sosforge.ui.kit.rememberNow
import co.soclose.sosforge.ui.kit.twoColumns
import co.soclose.sosforge.ui.kit.windowDp
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space

/** The events within a place's reach, nearest first: running alerts, and point events of the last 24 h. Pure. */
object Nearby {
    fun of(place: Place, events: List<SosEvent>, serverNow: Long, max: Int = 3): List<Pair<SosEvent, Double>> =
        events.asSequence()
            .filter { !it.isEnded(serverNow) && (it.isCurrentAlert(serverNow) || (it.timeMs ?: 0L) >= serverNow - 86_400_000L) }
            .mapNotNull { e -> Geo.distanceKm(place, e)?.takeIf { it <= Geo.reachKm(place, e) }?.let { e to it } }
            .sortedWith(compareByDescending<Pair<SosEvent, Double>> { it.first.severityOf.rank }.thenBy { it.second })
            .take(max)
            .toList()
}

/** Places: the zones the owner watches, each with what is happening inside it right now. */
@Composable
fun PlacesScreen(onOpen: (String) -> Unit) {
    val ctx = LocalContext.current
    val app = ctx.app
    val places by app.prefs.places.collectAsStateWithLifecycle()
    val feed by app.feed.collectAsStateWithLifecycle()
    val now = feed.serverNow(rememberNow(5_000L))
    var adding by rememberSaveable { mutableStateOf(false) }
    val (w, _) = windowDp()
    val two = twoColumns(w)

    fun save(next: List<Place>) {
        app.prefs.setPlaces(next)
        app.watch.reconcile()
    }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(horizontal = Space.gutter, vertical = Space.s), verticalArrangement = Arrangement.spacedBy(Space.m)) {
        item(key = "hero") {
            PageHero(
                title = stringResource(R.string.places_title),
                overline = stringResource(R.string.places_overline),
                sentence = stringResource(R.string.places_sentence),
                action = if (!adding && places.size < Place.MAX_PLACES) {
                    { PrimaryButton(stringResource(R.string.places_add), { adding = true }, icon = Icons.Outlined.AddLocationAlt) }
                } else null,
                meta = { Text(pluralStringResource(R.plurals.places_count, places.size, places.size), style = Sos.type.meta, color = Sos.colors.t3) },
            )
        }
        if (adding) item(key = "adder") {
            Column {
                Overline(stringResource(R.string.places_add))
                Spacer(Modifier.height(Space.s))
                PlaceAdder(onAdd = { p ->
                    save(places + p)
                    adding = false
                })
                TextAction(stringResource(R.string.cancel), { adding = false })
            }
        }
        if (places.isEmpty() && !adding) item(key = "empty") {
            StateBlock(stringResource(R.string.places_empty_title), body = stringResource(R.string.places_empty_body), icon = Icons.Outlined.Place)
        }
        val chunks = if (two) places.chunked(2) else places.map { listOf(it) }
        items(chunks, key = { c -> c.joinToString { it.id } }) { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(Space.m)) {
                row.forEach { p ->
                    val near = remember(feed.events, p, now / 30_000L) { Nearby.of(p, feed.events, now) }
                    PlaceCard(
                        p,
                        onChange = { np -> save(places.map { if (it.id == np.id) np else it }) },
                        onRemove = { save(places.filter { it.id != p.id }) },
                        modifier = Modifier.weight(1f),
                    ) {
                        Spacer(Modifier.height(Space.s))
                        Overline(stringResource(R.string.places_nearest))
                        if (near.isEmpty() && feed.snapshotRemaining > 0) {
                            Text(stringResource(R.string.live_loading_rest), style = Sos.type.small, color = Sos.colors.t3)
                        } else if (near.isEmpty()) {
                            Text(stringResource(R.string.places_quiet, p.radiusKm), style = Sos.type.small, color = Sos.colors.t3)
                        } else {
                            near.forEach { (e, _) -> EventRow(e, now, listOf(p), selected = false, onClick = { onOpen(e.id) }) }
                        }
                    }
                }
                if (row.size == 1 && two) Spacer(Modifier.weight(1f))
            }
        }
        item(key = "privacy") {
            Text(stringResource(R.string.places_privacy), style = Sos.type.meta, color = Sos.colors.t3, modifier = Modifier.fillMaxWidth())
        }
    }
}
