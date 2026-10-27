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
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * One coarse position, once, for "use my location". No Play Services: the platform LocationManager.
 * The position becomes a Place on this device; it is never sent anywhere except as a zone (lane B).
 * Coarse is enough: the smallest radius offered is 50 km, a coarse fix is within about 2 km.
 */
object Locate {
    fun granted(ctx: Context): Boolean =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    suspend fun once(ctx: Context, timeoutMs: Long = 12_000L): Location? {
        if (!granted(ctx)) return null
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return null
        val providers = buildList {
            if (Build.VERSION.SDK_INT >= 31) add(LocationManager.FUSED_PROVIDER)
            add(LocationManager.NETWORK_PROVIDER)
            add(LocationManager.GPS_PROVIDER)
        }.filter { p -> runCatching { lm.isProviderEnabled(p) }.getOrDefault(false) }
        for (p in providers) {
            val fix = withTimeoutOrNull(timeoutMs) { current(ctx, lm, p) }
            if (fix != null) return fix
        }
        return providers.firstNotNullOfOrNull { p -> lastKnown(ctx, lm, p) }
    }

    /**
     * The ISO alpha-2 country of a position, through the platform Geocoder when this phone has one
     * (many do through Google's backend, AOSP builds often do not). Null rather than a guess.
     */
    suspend fun countryOf(ctx: Context, lat: Double, lon: Double): String? {
        if (!android.location.Geocoder.isPresent()) return null
        val g = android.location.Geocoder(ctx, java.util.Locale.US)
        return kotlinx.coroutines.withTimeoutOrNull(8_000L) {
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
