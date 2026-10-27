package co.soclose.sosforge.feature.map

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.LayersClear
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.net.Severity
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.feature.common.LiveFilter
import co.soclose.sosforge.feature.common.rememberLink
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.LinkPill
import co.soclose.sosforge.ui.kit.MetaRow
import co.soclose.sosforge.ui.kit.ScreensMode
import co.soclose.sosforge.ui.kit.SecondaryButton
import co.soclose.sosforge.ui.kit.SeverityBadge
import co.soclose.sosforge.ui.kit.SeverityGlyph
import co.soclose.sosforge.ui.kit.StateBlock
import co.soclose.sosforge.ui.kit.rememberNow
import co.soclose.sosforge.ui.theme.SeverityFill
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space
import org.maplibre.android.MapLibre
import org.maplibre.android.camera.CameraPosition
import org.maplibre.android.camera.CameraUpdateFactory
import org.maplibre.android.geometry.LatLng
import org.maplibre.android.maps.MapLibreMap
import org.maplibre.android.maps.MapView
import org.maplibre.android.maps.Style
import org.maplibre.android.style.expressions.Expression
import org.maplibre.android.style.layers.CircleLayer
import org.maplibre.android.style.layers.FillLayer
import org.maplibre.android.style.layers.LineLayer
import org.maplibre.android.style.layers.PropertyFactory
import org.maplibre.android.style.sources.GeoJsonSource
import org.maplibre.geojson.Feature
import org.maplibre.geojson.FeatureCollection
import org.maplibre.geojson.Point
import org.maplibre.geojson.Polygon

/**
 * The basemap is OpenFreeMap, as on the web (frontend/src/components/MapView.tsx): OpenStreetMap vector
 * tiles, no key, no quota. Lunaire uses the web's `dark` style, Doux the `positron` light one.
 * A basemap outage degrades the picture, never the alerts: the style falls back to a plain background
 * and the markers still draw; a map that cannot start at all (no GL) shows a fallback and the feed runs.
 */
object MapStyle {
    const val DARK = "https://tiles.openfreemap.org/styles/dark"
    const val LIGHT = "https://tiles.openfreemap.org/styles/positron"

    fun fallback(dark: Boolean): String =
        """{"version":8,"sources":{},"layers":[{"id":"base","type":"background","paint":{"background-color":"${if (dark) "#0d0d0d" else "#efe6d6"}"}}]}"""

    /** Marker size from magnitude (logarithmic energy, as the web), or from severity when there is none. */
    fun radius(e: SosEvent): Float {
        val w = e.magnitude ?: if (e.severityOf == Severity.EXTREME) 6.0 else 4.0
        return (3.0 + (w.coerceIn(0.0, 9.0) / 9.0) * (w.coerceIn(0.0, 9.0) / 9.0) * 19.0).toFloat()
    }

    fun events(events: List<SosEvent>): FeatureCollection = FeatureCollection.fromFeatures(
        events.filter { it.hasPosition }.map { e ->
            Feature.fromGeometry(Point.fromLngLat(e.lon!!, e.lat!!)).apply {
                addStringProperty("id", e.id)
                addStringProperty("color", SeverityFill.hex(e.severityOf))
                addNumberProperty("r", radius(e))
                addNumberProperty("rank", e.severityOf.rank)
            }
        },
    )

    fun places(places: List<Place>): FeatureCollection = FeatureCollection.fromFeatures(
        places.map { p ->
            val ring = Geo.circle(p.lat, p.lon, p.radiusKm.toDouble()).map { (la, lo) -> Point.fromLngLat(lo, la) }
            Feature.fromGeometry(Polygon.fromLngLats(listOf(ring))).apply { addStringProperty("name", p.name) }
        },
    )
}

@Composable
fun MapScreen(focusId: String?, onOpen: (String) -> Unit) {
    val ctx = LocalContext.current
    val app = ctx.app
    val feed by app.feed.collectAsStateWithLifecycle()
    val places by app.prefs.places.collectAsStateWithLifecycle()
    val link = rememberLink()
    val now = feed.serverNow(rememberNow(5_000L))
    val dark = Sos.colors.dark
    var picked by rememberSaveable { mutableStateOf(focusId) }
    var failed by remember { mutableStateOf<String?>(null) }
    val shown = remember(feed.events, now / 60_000L) {
        LiveFilter.apply(feed.events, LiveFilter.DEFAULT_WINDOW, Kind.entries.map { it.key }.toSet(), now).filter { !it.isEnded(now) }
    }
    val preview = LocalInspectionMode.current || ScreensMode.on

    Box(Modifier.fillMaxSize()) {
        if (preview || failed != null) {
            MapFallback(failed)
        } else {
            LiveMap(
                events = shown, places = places, dark = dark, focus = feed.byId(focusId ?: ""),
                onPick = { picked = it }, onFail = { failed = it },
            )
        }
        Row(Modifier.align(Alignment.TopStart).padding(Space.m)) { LinkPill(link) }
        Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().padding(Space.m), verticalArrangement = Arrangement.spacedBy(Space.s)) {
            val e = picked?.let { feed.byId(it) }
            if (e != null) {
                Card(Modifier.widthIn(max = 560.dp)) {
                    Row(verticalAlignment = Alignment.Top) {
                        Column(Modifier.weight(1f)) {
                            MetaRow {
                                SeverityBadge(e.severityOf)
                                Text(stringResource(Words.kind(e.kindOf)), style = Sos.type.small, color = Sos.colors.t2)
                            }
                            Text(e.displayPlace, style = Sos.type.bodyStrong, color = Sos.colors.t1, maxLines = 2)
                            e.timeMs?.let { Text(Fmt.age(ctx, (now - it) / 1000), style = Sos.type.meta, color = Sos.colors.t3) }
                        }
                        IconButton(onClick = { picked = null }) { Icon(Icons.Outlined.Close, stringResource(R.string.close), tint = Sos.colors.t2) }
                    }
                    SecondaryButton(stringResource(R.string.map_open_detail), { onOpen(e.id) })
                }
            }
            Legend()
        }
    }
}

@Composable
private fun Legend() {
    Card(Modifier.widthIn(max = 560.dp)) {
        MetaRow {
            Severity.entries.reversed().forEach { s ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SeverityGlyph(s, size = 10.dp)
                    Spacer(Modifier.width(4.dp))
                    Text(stringResource(Words.severity(s)), style = Sos.type.meta, color = Sos.colors.sev.of(s))
                }
            }
        }
    }
}

@Composable
private fun MapFallback(reason: String?) {
    Box(Modifier.fillMaxSize().padding(top = 64.dp), contentAlignment = Alignment.TopCenter) {
        StateBlock(
            stringResource(R.string.map_unavailable),
            body = stringResource(R.string.map_unavailable_body) + (reason?.let { "\n($it)" } ?: ""),
            icon = Icons.Outlined.LayersClear,
        )
    }
}

/**
 * The MapLibre view, inside a try/catch (lesson 3): a rendering library must never take the feed
 * down. Its lifecycle follows the screen's.
 */
@Composable
private fun LiveMap(events: List<SosEvent>, places: List<Place>, dark: Boolean, focus: SosEvent?, onPick: (String) -> Unit, onFail: (String) -> Unit) {
    val ctx = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val created = remember { runCatching { createMap(ctx) } }
    val view = created.getOrNull()
    if (view == null) {
        LaunchedEffect(Unit) { onFail(created.exceptionOrNull()?.javaClass?.simpleName ?: "MapView") }
        return
    }
    var map by remember { mutableStateOf<MapLibreMap?>(null) }
    var style by remember { mutableStateOf<Style?>(null) }

    DisposableEffect(owner, view) {
        val obs = LifecycleEventObserver { _, ev ->
            runCatching {
                when (ev) {
                    Lifecycle.Event.ON_START -> view.onStart()
                    Lifecycle.Event.ON_RESUME -> view.onResume()
                    Lifecycle.Event.ON_PAUSE -> view.onPause()
                    Lifecycle.Event.ON_STOP -> view.onStop()
                    else -> Unit
                }
            }
        }
        owner.lifecycle.addObserver(obs)
        onDispose {
            owner.lifecycle.removeObserver(obs)
            runCatching { view.onStop(); view.onDestroy() }
        }
    }

    LaunchedEffect(view, dark) {
        runCatching {
            view.getMapAsync { m ->
                map = m
                m.uiSettings.isRotateGesturesEnabled = false
                m.uiSettings.isTiltGesturesEnabled = false
                // the legend and the picked-event card live at the bottom: the OpenStreetMap
                // attribution (required by its licence) and the logo go to the free top-right corner
                m.uiSettings.attributionGravity = android.view.Gravity.TOP or android.view.Gravity.END
                m.uiSettings.logoGravity = android.view.Gravity.TOP or android.view.Gravity.END
                val px = (8 * view.resources.displayMetrics.density).toInt()
                m.uiSettings.setAttributionMargins(0, px, px, 0)
                m.uiSettings.setLogoMargins(0, px, 12 * px, 0)
                m.cameraPosition = CameraPosition.Builder().target(LatLng(20.0, 10.0)).zoom(1.2).build()
                view.addOnDidFailLoadingMapListener {
                    // the basemap host is down or blocked: a plain background, the markers still draw
                    m.setStyle(Style.Builder().fromJson(MapStyle.fallback(dark))) { s -> style = s; addLayers(s) }
                }
                m.setStyle(Style.Builder().fromUri(if (dark) MapStyle.DARK else MapStyle.LIGHT)) { s -> style = s; addLayers(s) }
                m.addOnMapClickListener { ll ->
                    val pt = m.projection.toScreenLocation(ll)
                    val f = m.queryRenderedFeatures(pt, "sos-events").firstOrNull()
                    f?.getStringProperty("id")?.let(onPick)
                    f != null
                }
            }
        }.onFailure { onFail(it.javaClass.simpleName) }
    }

    LaunchedEffect(style, events) {
        runCatching { style?.getSourceAs<GeoJsonSource>("sos-events")?.setGeoJson(MapStyle.events(events)) }
    }
    LaunchedEffect(style, places) {
        runCatching { style?.getSourceAs<GeoJsonSource>("sos-places")?.setGeoJson(MapStyle.places(places)) }
    }
    LaunchedEffect(map, focus?.id) {
        val m = map ?: return@LaunchedEffect
        val f = focus ?: return@LaunchedEffect
        if (f.hasPosition) {
            runCatching { m.animateCamera(CameraUpdateFactory.newLatLngZoom(LatLng(f.lat!!, f.lon!!), 5.0)) }
            onPick(f.id)
        }
    }

    AndroidView(factory = { view }, modifier = Modifier.fillMaxSize())
}

private fun createMap(ctx: Context): MapView {
    MapLibre.getInstance(ctx)
    return MapView(ctx).apply { onCreate(null) }
}

private fun addLayers(s: Style) {
    if (s.getSource("sos-places") == null) s.addSource(GeoJsonSource("sos-places", FeatureCollection.fromFeatures(emptyList())))
    if (s.getSource("sos-events") == null) s.addSource(GeoJsonSource("sos-events", FeatureCollection.fromFeatures(emptyList())))
    if (s.getLayer("sos-places-fill") == null) s.addLayer(
        FillLayer("sos-places-fill", "sos-places").withProperties(PropertyFactory.fillColor("#45c9e6"), PropertyFactory.fillOpacity(0.07f)),
    )
    if (s.getLayer("sos-places-line") == null) s.addLayer(
        LineLayer("sos-places-line", "sos-places").withProperties(PropertyFactory.lineColor("#45c9e6"), PropertyFactory.lineWidth(1.5f), PropertyFactory.lineOpacity(0.8f)),
    )
    if (s.getLayer("sos-events") == null) s.addLayer(
        CircleLayer("sos-events", "sos-events").withProperties(
            PropertyFactory.circleColor(Expression.get("color")),
            PropertyFactory.circleRadius(Expression.get("r")),
            PropertyFactory.circleOpacity(0.85f),
            PropertyFactory.circleStrokeColor("#06080a"),
            PropertyFactory.circleStrokeWidth(1f),
            // the most severe draw on top
            PropertyFactory.circleSortKey(Expression.get("rank")),
        ),
    )
}
