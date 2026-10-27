package co.soclose.sosforge.feature.places

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.MyLocation
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.ApiError
import co.soclose.sosforge.core.net.ApiException
import co.soclose.sosforge.core.net.GeoResult
import co.soclose.sosforge.core.sys.Locate
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.Chip
import co.soclose.sosforge.ui.kit.ChipFlow
import co.soclose.sosforge.ui.kit.SecondaryButton
import co.soclose.sosforge.ui.kit.TextAction
import co.soclose.sosforge.core.alert.AlertRules
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.feature.settings.SwitchRow
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

/** The steps of "use my location": the rationale BEFORE the system prompt, then honest outcomes. */
enum class LocateStep { IDLE, RATIONALE, LOCATING, DENIED, FAILED }

/**
 * Adds a place, two ways: the phone's position (a rationale screen first, then the system prompt;
 * a refusal is a state, never a dead end: search still works), or a name searched through the
 * server's `/api/geocode` (which holds Nominatim's 1 request per second).
 */
@Composable
fun PlaceAdder(onAdd: (Place) -> Unit, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val app = ctx.app
    val scope = rememberCoroutineScope()
    val kb = LocalSoftwareKeyboardController.current
    var step by rememberSaveable { mutableStateOf(LocateStep.IDLE) }
    var query by rememberSaveable { mutableStateOf("") }
    var results by remember { mutableStateOf<List<GeoResult>>(emptyList()) }
    var searchNote by remember { mutableStateOf<String?>(null) }
    var searching by remember { mutableStateOf(false) }
    val here = stringResource(R.string.place_here)
    val noneText = stringResource(R.string.search_none)
    val unreachableText = stringResource(R.string.search_unreachable)
    val failedText = stringResource(R.string.search_failed)

    fun locate() {
        step = LocateStep.LOCATING
        scope.launch {
            val loc = Locate.once(ctx)
            if (loc == null) step = LocateStep.FAILED
            else {
                step = LocateStep.IDLE
                // the country, for country-wide warnings (null when this phone has no geocoder: then none)
                val cc = Locate.countryOf(ctx, loc.latitude, loc.longitude)
                onAdd(Place(UUID.randomUUID().toString(), here, loc.latitude, loc.longitude, Place.DEFAULT_RADIUS_KM, "here", countryCode = cc))
            }
        }
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) locate() else step = LocateStep.DENIED
    }

    suspend fun search(q: String) {
        if (q.trim().length < 2) {
            results = emptyList()
            searchNote = null
            return
        }
        searching = true
        try {
            val r = withContext(Dispatchers.IO) { app.api().geocode(q.trim()) }
            results = r.results.filter { it.lat in -90.0..90.0 && it.lon in -180.0..180.0 }.take(6)
            searchNote = if (results.isEmpty()) noneText else null
        } catch (e: ApiException) {
            results = emptyList()
            searchNote = if (e.error == ApiError.Unreachable) unreachableText else failedText
        } finally {
            searching = false
        }
    }

    // typing pauses 700 ms before a search: the server proxies Nominatim at 1 request per second
    LaunchedEffect(query) {
        delay(700)
        search(query)
    }

    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Space.s)) {
        when (step) {
            LocateStep.RATIONALE -> Card {
                Text(stringResource(R.string.locate_rationale_title), style = Sos.type.heading, color = Sos.colors.t1)
                Spacer(Modifier.height(Space.xs))
                Text(stringResource(R.string.locate_rationale_body), style = Sos.type.small, color = Sos.colors.t2)
                Spacer(Modifier.height(Space.m))
                Row(horizontalArrangement = Arrangement.spacedBy(Space.s), verticalAlignment = Alignment.CenterVertically) {
                    SecondaryButton(stringResource(R.string.locate_allow), {
                        if (Locate.granted(ctx)) locate() else permission.launch(Manifest.permission.ACCESS_COARSE_LOCATION)
                    }, icon = Icons.Outlined.MyLocation)
                    TextAction(stringResource(R.string.not_now), { step = LocateStep.IDLE })
                }
            }
            LocateStep.LOCATING -> Text(stringResource(R.string.locate_working), style = Sos.type.small, color = Sos.colors.t2)
            LocateStep.DENIED -> Note(stringResource(R.string.locate_denied)) { step = LocateStep.IDLE }
            LocateStep.FAILED -> Note(stringResource(R.string.locate_failed)) { step = LocateStep.IDLE }
            LocateStep.IDLE -> SecondaryButton(
                stringResource(R.string.locate_use),
                { if (Locate.granted(ctx)) locate() else step = LocateStep.RATIONALE },
                icon = Icons.Outlined.MyLocation,
            )
        }
        OutlinedTextField(
            value = query,
            onValueChange = { query = it.take(120) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text(stringResource(R.string.search_label)) },
            leadingIcon = { Icon(Icons.Outlined.Search, null) },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = {
                kb?.hide()
                scope.launch { search(query) }
            }),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Sos.colors.t1, unfocusedBorderColor = Sos.colors.lineStrong,
                focusedLabelColor = Sos.colors.t2, unfocusedLabelColor = Sos.colors.t3,
                cursorColor = Sos.colors.t1,
            ),
            textStyle = Sos.type.body.copy(color = Sos.colors.t1),
        )
        if (searching) Text(stringResource(R.string.search_working), style = Sos.type.meta, color = Sos.colors.t3)
        searchNote?.let { Text(it, style = Sos.type.small, color = Sos.colors.t3) }
        results.forEach { r ->
            Card(onClick = {
                onAdd(
                    Place(
                        UUID.randomUUID().toString(), r.name.substringBefore(',').ifBlank { r.name }.take(60), r.lat, r.lon,
                        Place.DEFAULT_RADIUS_KM, "search", countryCode = r.countryCode?.uppercase()?.takeIf { it.length == 2 },
                    ),
                )
                query = ""
                results = emptyList()
                kb?.hide()
            }) {
                Text(r.name, style = Sos.type.bodyStrong, color = Sos.colors.t1, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(Fmt.latLon(r.lat, r.lon), style = Sos.type.meta, color = Sos.colors.t3)
            }
        }
    }
}

@Composable
private fun Note(text: String, onOk: () -> Unit) {
    Card {
        Text(text, style = Sos.type.small, color = Sos.colors.t2)
        TextAction(stringResource(R.string.ok), onOk)
    }
}

/**
 * One place: its name, its radius (five honest choices), country-wide warnings with their live count
 * (lesson 24: the owner decides on their own number), and a way to remove it.
 */
@Composable
fun PlaceCard(place: Place, onChange: (Place) -> Unit, onRemove: () -> Unit, modifier: Modifier = Modifier, below: @Composable () -> Unit = {}) {
    val ctx = LocalContext.current
    val feed by ctx.app.feed.collectAsStateWithLifecycle()
    Card(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(place.name, style = Sos.type.heading, color = Sos.colors.t1, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(Fmt.latLon(place.lat, place.lon), style = Sos.type.meta, color = Sos.colors.t3)
            }
            IconButton(onClick = onRemove) { Icon(Icons.Outlined.Delete, stringResource(R.string.place_remove, place.name), tint = Sos.colors.t2) }
        }
        Text(stringResource(R.string.place_radius), style = Sos.type.small, color = Sos.colors.t2, modifier = Modifier.padding(top = Space.s))
        ChipFlow {
            Place.RADII.forEach { r -> Chip(Fmt.km(ctx, r.toDouble()), place.radiusKm == r, { onChange(place.copy(radiusKm = r)) }) }
        }
        val cc = place.countryCode
        if (cc != null) {
            val country = Fmt.country(cc, LocalConfiguration.current.locales[0]) ?: cc
            val now = feed.serverNow(System.currentTimeMillis())
            val count = remember(feed.events, cc) {
                feed.events.count { !it.hasPosition && it.countryCode.equals(cc, true) && !it.isEnded(now) && AlertRules.rankOf(it) >= AlertRules.COUNTRY_FLOOR.rank }
            }
            SwitchRow(stringResource(R.string.place_country_wide, country), place.countryWide) { onChange(place.copy(countryWide = it)) }
            Text(pluralStringResource(R.plurals.place_country_count, count, count), style = Sos.type.meta, color = Sos.colors.t3)
        }
        below()
    }
}
