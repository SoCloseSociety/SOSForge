package co.soclose.sosforge.core.map

import android.content.Context
import android.os.Build
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import co.soclose.sosforge.app
import co.soclose.sosforge.core.geo.Place
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import org.maplibre.android.MapLibre
import org.maplibre.android.geometry.LatLngBounds
import org.maplibre.android.offline.OfflineManager
import org.maplibre.android.offline.OfflineRegion
import org.maplibre.android.offline.OfflineRegionError
import org.maplibre.android.offline.OfflineRegionStatus
import org.maplibre.android.offline.OfflineTilePyramidRegionDefinition
import kotlin.math.PI
import kotlin.math.asinh
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.tan

/** The basemap styles (frontend/src/components/MapView.tsx, OpenFreeMap): one per theme. */
object MapStyles {
    const val DARK = "https://tiles.openfreemap.org/styles/dark"
    const val LIGHT = "https://tiles.openfreemap.org/styles/positron"
    val ALL = listOf(DARK, LIGHT)
}

/**
 * Which offline packs should exist, pure (PackPlanTest). Measured on OpenFreeMap on 2026-10-27
 * (sampled tile sizes, scratch tool): the world at zooms 0..4 is about 52 MB (zoom 2 alone is 14 MB,
 * its 16 tiles average 877 KiB); a 300 km place is about 13 to 15 MB at zooms 5..9, 23 to 30 MB at
 * 5..10, 41 to 50 MB at 5..11 (Athens, Tokyo). So: the world at 0..4, and each place down to the
 * deepest zoom whose CUMULATIVE tile count from zoom 5 stays within [PLACE_TILE_BUDGET] (5..9 for
 * 300 km, deeper for a smaller radius, never past [PLACE_MAX_ZOOM]). The two styles share their tiles
 * (one TileJSON), so the second style costs only its style, sprites and glyphs.
 */
object PackPlan {
    const val WORLD_MAX_ZOOM = 4
    const val PLACE_MIN_ZOOM = 5
    const val PLACE_MAX_ZOOM = 12
    const val PLACE_TILE_BUDGET = 200
    /** The map's own cache, beside the packs (MapLibre's default is 50 MB too; said here on purpose). */
    const val AMBIENT_CACHE_BYTES = 50L * 1024 * 1024

    @Serializable
    data class Bounds(val south: Double, val west: Double, val north: Double, val east: Double)

    @Serializable
    data class Spec(val key: String, val style: String, val bounds: Bounds, val minZoom: Int, val maxZoom: Int, val placeId: String?)

    val WORLD = Bounds(-85.0, -180.0, 85.0, 180.0)

    /** The square around a place's radius, clamped to the Web Mercator band and the antimeridian. */
    fun bounds(p: Place): Bounds {
        val dLat = p.radiusKm / 111.0
        val dLon = p.radiusKm / (111.0 * cos(Math.toRadians(p.lat)).coerceAtLeast(0.05))
        return Bounds(
            (p.lat - dLat).coerceIn(-85.0, 85.0), (p.lon - dLon).coerceIn(-180.0, 180.0),
            (p.lat + dLat).coerceIn(-85.0, 85.0), (p.lon + dLon).coerceIn(-180.0, 180.0),
        )
    }

    private fun tileX(lon: Double, z: Int): Int = floor((lon + 180.0) / 360.0 * (1 shl z)).toInt().coerceIn(0, (1 shl z) - 1)
    private fun tileY(lat: Double, z: Int): Int =
        floor((1 - asinh(tan(Math.toRadians(lat))) / PI) / 2 * (1 shl z)).toInt().coerceIn(0, (1 shl z) - 1)

    fun tilesAt(b: Bounds, z: Int): Long =
        (tileX(b.east, z) - tileX(b.west, z) + 1).toLong() * (tileY(b.south, z) - tileY(b.north, z) + 1).toLong()

    fun tiles(b: Bounds, minZ: Int, maxZ: Int): Long = (minZ..maxZ).sumOf { tilesAt(b, it) }

    fun placeMaxZoom(b: Bounds): Int {
        var z = PLACE_MIN_ZOOM
        while (z < PLACE_MAX_ZOOM && tiles(b, PLACE_MIN_ZOOM, z + 1) <= PLACE_TILE_BUDGET) z++
        return z
    }

    fun wanted(places: List<Place>, styles: List<String> = MapStyles.ALL): List<Spec> = styles.flatMap { style ->
        val tag = if (style == MapStyles.DARK) "dark" else "light"
        listOf(Spec("world|$tag", style, WORLD, 0, WORLD_MAX_ZOOM, null)) + places.map { p ->
            val b = bounds(p)
            // the radius is part of the key: a new radius is a new pack (the old one goes)
            Spec("place:${p.id}:${p.radiusKm}|$tag", style, b, PLACE_MIN_ZOOM, placeMaxZoom(b), p.id)
        }
    }

    /** Keys to delete (a removed place, a changed radius) and specs to create. */
    fun diff(existing: Set<String>, wanted: List<Spec>): Pair<Set<String>, List<Spec>> =
        (existing - wanted.map { it.key }.toSet()) to wanted.filter { it.key !in existing }
}

/** One pack as Settings shows it. */
data class PackState(
    val key: String,
    val placeId: String?,
    val completed: Long = 0,
    val required: Long = 0,
    val bytes: Long = 0,
    val complete: Boolean = false,
    val error: String? = null,
)

/**
 * The offline packs, through MapLibre's OfflineManager (main thread only, as MapLibre requires).
 * Downloads run on UNMETERED networks only: a metered network pauses every pack, the next unmetered
 * one resumes them (also from the background, through [OfflineMapWorker] with an UNMETERED constraint).
 * A removed place, or a place whose radius changed, loses its pack.
 */
class OfflineMaps(private val ctx: Context) {
    private val _packs = MutableStateFlow<Map<String, PackState>>(emptyMap())
    val packs: StateFlow<Map<String, PackState>> = _packs.asStateFlow()
    private val regions = mutableMapOf<String, OfflineRegion>()
    private var unmetered = false
    @Volatile private var ready = false

    /** MapLibre's native part cannot load on the JVM (unit and screenshot tests): nothing happens there. */
    private val available: Boolean get() = Build.FINGERPRINT != "robolectric" && runCatching { MapLibre.getInstance(ctx) }.isSuccess

    private fun manager(): OfflineManager? {
        if (!available) return null
        val m = OfflineManager.getInstance(ctx)
        if (!ready) {
            ready = true
            m.setMaximumAmbientCacheSize(PackPlan.AMBIENT_CACHE_BYTES, null)
        }
        return m
    }

    /** Must run on the main thread. */
    fun sync(places: List<Place>, unmeteredNow: Boolean) {
        unmetered = unmeteredNow
        val m = runCatching { manager() }.getOrNull() ?: return
        m.listOfflineRegions(object : OfflineManager.ListOfflineRegionsCallback {
            override fun onList(offlineRegions: Array<OfflineRegion>?) {
                val byKey = offlineRegions.orEmpty().associateBy { keyOf(it) }
                val wanted = PackPlan.wanted(places)
                val (drop, create) = PackPlan.diff(byKey.keys, wanted)
                drop.forEach { k ->
                    byKey[k]?.let { r ->
                        r.setDownloadState(OfflineRegion.STATE_INACTIVE)
                        r.delete(object : OfflineRegion.OfflineRegionDeleteCallback {
                            override fun onDelete() {
                                regions.remove(k)
                                _packs.value = _packs.value - k
                            }
                            override fun onError(error: String) {
                                _packs.value = _packs.value + (k to (_packs.value[k] ?: PackState(k, null)).copy(error = error))
                            }
                        })
                    }
                }
                byKey.filterKeys { it !in drop }.forEach { (k, r) -> track(k, r, wanted.firstOrNull { it.key == k }?.placeId) }
                create.forEach { spec -> create(m, spec) }
            }

            override fun onError(error: String) = Unit
        })
    }

    /** The network changed: resume on unmetered, pause on metered. Main thread. */
    fun onNetwork(unmeteredNow: Boolean) {
        unmetered = unmeteredNow
        regions.forEach { (k, r) -> if (_packs.value[k]?.complete != true) r.setDownloadState(if (unmetered) OfflineRegion.STATE_ACTIVE else OfflineRegion.STATE_INACTIVE) }
    }

    val allComplete: Boolean get() = _packs.value.isNotEmpty() && _packs.value.values.all { it.complete }

    private fun create(m: OfflineManager, spec: PackPlan.Spec) {
        val b = LatLngBounds.from(spec.bounds.north, spec.bounds.east, spec.bounds.south, spec.bounds.west)
        val def = OfflineTilePyramidRegionDefinition(spec.style, b, spec.minZoom.toDouble(), spec.maxZoom.toDouble(), ctx.resources.displayMetrics.density)
        m.createOfflineRegion(def, spec.key.toByteArray(), object : OfflineManager.CreateOfflineRegionCallback {
            override fun onCreate(offlineRegion: OfflineRegion) = track(spec.key, offlineRegion, spec.placeId)
            override fun onError(error: String) {
                _packs.value = _packs.value + (spec.key to PackState(spec.key, spec.placeId, error = error))
            }
        })
    }

    private fun track(key: String, r: OfflineRegion, placeId: String?) {
        regions[key] = r
        r.setObserver(object : OfflineRegion.OfflineRegionObserver {
            override fun onStatusChanged(status: OfflineRegionStatus) {
                _packs.value = _packs.value + (key to PackState(
                    key, placeId, status.completedResourceCount, status.requiredResourceCount, status.completedResourceSize, status.isComplete,
                ))
                if (status.isComplete) r.setDownloadState(OfflineRegion.STATE_INACTIVE)
            }

            override fun onError(error: OfflineRegionError) {
                _packs.value = _packs.value + (key to (_packs.value[key] ?: PackState(key, placeId)).copy(error = error.reason))
            }

            override fun mapboxTileCountLimitExceeded(limit: Long) {
                _packs.value = _packs.value + (key to (_packs.value[key] ?: PackState(key, placeId)).copy(error = "tile limit $limit"))
            }
        })
        r.setDownloadState(if (unmetered) OfflineRegion.STATE_ACTIVE else OfflineRegion.STATE_INACTIVE)
    }

    private fun keyOf(r: OfflineRegion): String = runCatching { String(r.metadata) }.getOrDefault("")

    companion object {
        /** Ask WorkManager to finish the packs on the next unmetered network, app open or not. */
        fun schedule(ctx: Context) {
            val req = OneTimeWorkRequestBuilder<OfflineMapWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build())
                .build()
            runCatching { WorkManager.getInstance(ctx).enqueueUniqueWork("sos-offline-map", ExistingWorkPolicy.KEEP, req) }
        }
    }
}

/** Runs only on an unmetered network: starts the packs and waits (at most 9 min) for them. */
class OfflineMapWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext.app
        withContext(Dispatchers.Main) { app.offline.sync(app.prefs.places.value, unmeteredNow = true) }
        repeat(9 * 12) {
            if (app.offline.allComplete) return Result.success()
            delay(5_000L)
        }
        // not done: run again on the next unmetered window
        return Result.retry()
    }
}
