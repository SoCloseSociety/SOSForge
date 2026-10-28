package co.soclose.sosforge.core.sys

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.SystemClock
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/** What "use my location" got. Every case has its own honest sentence on screen. */
sealed interface Fix {
    /** A position. [ageMs]: how old it is (a recent last-known fix answers at once). */
    data class Ok(val lat: Double, val lon: Double, val ageMs: Long) : Fix
    /** The permission is not granted (never asked, refused, "only this time" expired, revoked). */
    data object Denied : Fix
    /** The phone's own location switch is off: no provider can answer. */
    data object LocationOff : Fix
    /** Nothing within the timeout, and no recent last-known fix. */
    data object NoFix : Fix
}

/** The choices of [Locate], pure (LocateRulesTest). */
object LocateRules {
    /**
     * One overall wait for a fresh fix, whatever the number of providers. 30 s, not 15: with an
     * APPROXIMATE grant Android throttles the request to LOW_POWER (owner's Fold, 2026-10-27: the
     * fused request became "ProviderRequest[@+10m0s0ms, LOW_POWER]"), and a coarse fix can take time.
     */
    const val TIMEOUT_MS = 30_000L
    /**
     * A last-known fix this recent answers at once (from ANY provider, the passive one included).
     * 30 min: for a place with a 50 km radius at least, a coarse position half an hour old is the
     * same town; older, it may be another city and is refused rather than used.
     */
    const val RECENT_MS = 30 * 60_000L
    /** After the wait, a last-known fix up to this old is still taken (it says its age). */
    const val STALE_MS = 30 * 60_000L

    /** The providers worth asking, in order (fused first when the platform has it). */
    fun providers(sdk: Int, enabled: (String) -> Boolean): List<String> = buildList {
        if (sdk >= 31) add(LocationManager.FUSED_PROVIDER)
        add(LocationManager.NETWORK_PROVIDER)
        add(LocationManager.GPS_PROVIDER)
    }.filter(enabled)

    fun recentEnough(ageMs: Long?): Boolean = ageMs != null && ageMs in 0..RECENT_MS
    fun usableFallback(ageMs: Long?): Boolean = ageMs != null && ageMs in 0..STALE_MS
}

/**
 * One coarse position, once, for "use my location" and for the Live "near me" view. No Play
 * Services: the platform LocationManager. The position is kept on this device; it reaches the server
 * only as a zone of a scoped socket or poll. Coarse is enough: the smallest radius offered is 50 km,
 * a coarse fix is within about 2 km.
 *
 * Verified on the emulator (0.1.2): granted, refused, refused for good, "only this time", revoked
 * later, the system location switched off, and no fix within the timeout.
 */
object Locate {
    fun granted(ctx: Context): Boolean =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    fun locationEnabled(ctx: Context): Boolean {
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return false
        return runCatching { LocationManagerCompat.isLocationEnabled(lm) }.getOrDefault(false)
    }

    private fun ageMs(loc: Location): Long = (SystemClock.elapsedRealtimeNanos() - loc.elapsedRealtimeNanos) / 1_000_000L

    suspend fun fix(ctx: Context, timeoutMs: Long = LocateRules.TIMEOUT_MS): Fix {
        if (!granted(ctx)) return Fix.Denied
        if (!locationEnabled(ctx)) return Fix.LocationOff
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return Fix.NoFix
        val providers = LocateRules.providers(Build.VERSION.SDK_INT) { p -> runCatching { lm.isProviderEnabled(p) }.getOrDefault(false) }
        if (providers.isEmpty()) return Fix.LocationOff
        // 1. the last known fix of EVERY provider, the passive one too (it holds what any app received)
        val last = freshestLastKnown(ctx, lm)
        if (last != null && LocateRules.recentEnough(ageMs(last))) return Fix.Ok(last.latitude, last.longitude, ageMs(last))
        // 2. continuous updates on every provider at once, the first location wins, ONE wait for all.
        // Not a single-shot getCurrentLocation: under coarse throttling that one came back EMPTY on the
        // owner's Fold after 2.7 s (0.1.1), and nothing asked again.
        val fresh = withTimeoutOrNull(timeoutMs) { firstUpdate(ctx, lm, providers) }
        if (fresh != null) return Fix.Ok(fresh.latitude, fresh.longitude, 0L)
        if (!granted(ctx)) return Fix.Denied
        // 3. the wait is over: a fix that arrived meanwhile through another app, if it is recent enough
        val again = freshestLastKnown(ctx, lm) ?: last
        if (again != null && LocateRules.usableFallback(ageMs(again))) return Fix.Ok(again.latitude, again.longitude, ageMs(again))
        return Fix.NoFix
    }

    /**
     * The ISO alpha-2 country of a position, through the platform Geocoder when this phone has one
     * (many do through Google's backend, AOSP builds often do not). Null rather than a guess.
     */
    suspend fun countryOf(ctx: Context, lat: Double, lon: Double): String? {
        if (!android.location.Geocoder.isPresent()) return null
        val g = android.location.Geocoder(ctx, java.util.Locale.US)
        return withTimeoutOrNull(8_000L) {
            if (Build.VERSION.SDK_INT >= 33) {
                suspendCancellableCoroutine { cont ->
                    try {
                        g.getFromLocation(lat, lon, 1, object : android.location.Geocoder.GeocodeListener {
                            override fun onGeocode(addresses: MutableList<android.location.Address>) {
                                if (cont.isActive) cont.resume(addresses.firstOrNull()?.countryCode?.uppercase())
                            }

                            override fun onError(errorMessage: String?) {
                                if (cont.isActive) cont.resume(null)
                            }
                        })
                    } catch (_: Exception) {
                        if (cont.isActive) cont.resume(null)
                    }
                }
            } else {
                kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    @Suppress("DEPRECATION")
                    runCatching { g.getFromLocation(lat, lon, 1)?.firstOrNull()?.countryCode?.uppercase() }.getOrNull()
                }
            }
        }?.takeIf { it.length == 2 }
    }

    private fun freshestLastKnown(ctx: Context, lm: LocationManager): Location? =
        runCatching { lm.allProviders }.getOrDefault(emptyList<String>())
            .mapNotNull { lastKnown(ctx, lm, it) }
            .filter { ageMs(it) >= 0 }
            .minByOrNull { ageMs(it) }

    /** Updates from every provider until the first location (then every listener is removed). */
    private suspend fun firstUpdate(ctx: Context, lm: LocationManager, providers: List<String>): Location? {
        if (!granted(ctx)) return null
        return suspendCancellableCoroutine { cont ->
            val listeners = mutableListOf<androidx.core.location.LocationListenerCompat>()
            fun stop() = listeners.forEach { l ->
                try {
                    LocationManagerCompat.removeUpdates(lm, l)
                } catch (_: SecurityException) {
                    // revoked meanwhile: the platform already dropped the request
                }
            }
            val req = androidx.core.location.LocationRequestCompat.Builder(1_000L)
                .setQuality(androidx.core.location.LocationRequestCompat.QUALITY_BALANCED_POWER_ACCURACY)
                .setMinUpdateIntervalMillis(0L)
                .build()
            for (p in providers) {
                val l = androidx.core.location.LocationListenerCompat { loc ->
                    if (cont.isActive) {
                        stop()
                        cont.resume(loc)
                    }
                }
                try {
                    LocationManagerCompat.requestLocationUpdates(lm, p, req, ContextCompat.getMainExecutor(ctx), l)
                    listeners += l
                } catch (_: SecurityException) {
                } catch (_: IllegalArgumentException) {
                }
            }
            if (listeners.isEmpty() && cont.isActive) cont.resume(null)
            cont.invokeOnCancellation { stop() }
        }
    }

    private fun lastKnown(ctx: Context, lm: LocationManager, provider: String): Location? {
        if (!granted(ctx)) return null
        return try {
            lm.getLastKnownLocation(provider)
        } catch (_: SecurityException) {
            null
        }
    }
}
