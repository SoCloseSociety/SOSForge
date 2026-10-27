package co.soclose.sosforge.core.sys

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.selects.select
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
    /** One overall timeout for a fresh fix, whatever the number of providers. */
    const val TIMEOUT_MS = 15_000L
    /** A last-known fix this recent answers at once, without waiting for a fresh one. */
    const val RECENT_MS = 2 * 60_000L
    /** Older than this, a last-known fix may be from another city: refused rather than used. */
    const val STALE_MS = 10 * 60_000L

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
        val last = providers.mapNotNull { lastKnown(ctx, lm, it) }.minByOrNull { ageMs(it) }
        if (last != null && LocateRules.recentEnough(ageMs(last))) return Fix.Ok(last.latitude, last.longitude, ageMs(last))
        // every provider at once, the first answer wins, ONE timeout for all (0.1.1 waited 12 s per provider in turn)
        val fresh = withTimeoutOrNull(timeoutMs) {
            coroutineScope {
                val asks = providers.map { p -> async { current(ctx, lm, p) } }
                var got: Location? = null
                val pending = asks.toMutableList()
                while (got == null && pending.isNotEmpty()) {
                    val (done, value) = select<Pair<kotlinx.coroutines.Deferred<Location?>, Location?>> {
                        pending.forEach { d -> d.onAwait { d to it } }
                    }
                    pending.remove(done)
                    got = value
                }
                pending.forEach { it.cancel() }
                got
            }
        }
        if (fresh != null) return Fix.Ok(fresh.latitude, fresh.longitude, 0L)
        if (!granted(ctx)) return Fix.Denied
        if (last != null && LocateRules.usableFallback(ageMs(last))) return Fix.Ok(last.latitude, last.longitude, ageMs(last))
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

    private fun lastKnown(ctx: Context, lm: LocationManager, provider: String): Location? {
        if (!granted(ctx)) return null
        return try {
            lm.getLastKnownLocation(provider)
        } catch (_: SecurityException) {
            null
        }
    }

    private suspend fun current(ctx: Context, lm: LocationManager, provider: String): Location? {
        if (!granted(ctx)) return null
        return suspendCancellableCoroutine { cont ->
            try {
                if (Build.VERSION.SDK_INT >= 30) {
                    val cancel = CancellationSignal()
                    cont.invokeOnCancellation { cancel.cancel() }
                    lm.getCurrentLocation(provider, cancel, ContextCompat.getMainExecutor(ctx)) { loc -> if (cont.isActive) cont.resume(loc) }
                } else {
                    val l = object : LocationListener {
                        override fun onLocationChanged(location: Location) {
                            lm.removeUpdates(this)
                            if (cont.isActive) cont.resume(location)
                        }

                        // abstract before API 30: an unimplemented one would crash when Android calls it
                        @Deprecated("platform callback")
                        override fun onStatusChanged(provider: String?, status: Int, extras: android.os.Bundle?) = Unit
                        override fun onProviderEnabled(provider: String) = Unit
                        override fun onProviderDisabled(provider: String) = Unit
                    }
                    cont.invokeOnCancellation { lm.removeUpdates(l) }
                    @Suppress("DEPRECATION")
                    lm.requestSingleUpdate(provider, l, Looper.getMainLooper())
                }
            } catch (_: SecurityException) {
                if (cont.isActive) cont.resume(null)
            } catch (_: IllegalArgumentException) {
                if (cont.isActive) cont.resume(null)
            }
        }
    }
}
