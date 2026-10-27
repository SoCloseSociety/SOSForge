package co.soclose.sosforge.core.geo

import co.soclose.sosforge.core.net.SosEvent
import kotlinx.serialization.Serializable
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * A place the owner watches. It lives on THIS device only: it is sent to the server solely as a zone
 * of a scoped socket or poll (lane B), and never stored there.
 */
@Serializable
data class Place(
    val id: String,
    val name: String,
    val lat: Double,
    val lon: Double,
    val radiusKm: Int = DEFAULT_RADIUS_KM,
    /** "here" (the phone's position, once) or "search" (a geocoded name). */
    val origin: String = "search",
    /** ISO alpha-2 of the place's country, when known (geocoder): what positionless warnings match on. */
    val countryCode: String? = null,
    /**
     * Country-wide warnings: events WITHOUT a position (Meteoalarm, most of them) matched by country,
     * severe and above only. Explicit and per place, with its live count next to the switch (lesson 24:
     * "same country" rang 63 times a day for Los Angeles; a country the size of Greece is another story).
     */
    val countryWide: Boolean = true,
) {
    val valid: Boolean get() = lat in -90.0..90.0 && lon in -180.0..180.0 && radiusKm in MIN_RADIUS_KM..MAX_RADIUS_KM

    companion object {
        const val DEFAULT_RADIUS_KM = 300
        const val MIN_RADIUS_KM = 10
        // the server's own bound on a zone (backend/app/scope.py MAX_RADIUS_KM)
        const val MAX_RADIUS_KM = 2000
        /** The radii the owner picks from: a valley, a city, a region, a country, a sea. */
        val RADII = listOf(50, 100, 300, 600, 1000)
        /** At most this many places: the server takes ten zones per socket. */
        const val MAX_PLACES = 10
    }
}

object Geo {
    private const val EARTH_RADIUS_KM = 6371.0088

    /** Great-circle distance, the same formula as backend/app/scope.py and frontend/src/arrival.ts. */
    fun distanceKm(aLat: Double, aLon: Double, bLat: Double, bLon: Double): Double {
        val dLat = Math.toRadians(bLat - aLat)
        val dLon = Math.toRadians(bLon - aLon)
        val p1 = Math.toRadians(aLat)
        val p2 = Math.toRadians(bLat)
        val h = sin(dLat / 2) * sin(dLat / 2) + cos(p1) * cos(p2) * sin(dLon / 2) * sin(dLon / 2)
        return 2 * EARTH_RADIUS_KM * asin(min(1.0, sqrt(h)))
    }

    fun distanceKm(place: Place, event: SosEvent): Double? =
        if (!event.hasPosition) null else distanceKm(place.lat, place.lon, event.lat!!, event.lon!!)

    /**
     * How far an event reaches a place, in km. The owner's radius, with ONE explicit exception: a
     * tsunami crosses a basin, so it reaches at least [TSUNAMI_REACH_KM] whatever the radius (a place on
     * the coast 600 km from the epicentre is in its path). Nothing else is widened behind the owner's back.
     */
    fun reachKm(place: Place, event: SosEvent): Double =
        if (isTsunami(event)) maxOf(place.radiusKm.toDouble(), TSUNAMI_REACH_KM) else place.radiusKm.toDouble()

    /** The radius to ASK the server for, so its zone filter never drops what [reachKm] would keep. */
    fun scopeRadiusKm(place: Place, tsunamiWatched: Boolean): Int =
        if (tsunamiWatched) maxOf(place.radiusKm, TSUNAMI_REACH_KM.toInt()) else place.radiusKm

    const val TSUNAMI_REACH_KM = 1000.0

    fun isTsunami(e: SosEvent): Boolean = e.tsunami || e.kind == "tsunami"

    /** A point of the circle around a place, for drawing its radius on the map (a polygon ring). */
    fun circle(lat: Double, lon: Double, radiusKm: Double, steps: Int = 72): List<Pair<Double, Double>> {
        val d = radiusKm / EARTH_RADIUS_KM
        val la = Math.toRadians(lat)
        val lo = Math.toRadians(lon)
        return (0..steps).map { i ->
            val brg = 2 * Math.PI * i / steps
            val lat2 = asin(sin(la) * cos(d) + cos(la) * sin(d) * cos(brg))
            val lon2 = lo + kotlin.math.atan2(sin(brg) * sin(d) * cos(la), cos(d) - sin(la) * sin(lat2))
            var lonDeg = Math.toDegrees(lon2)
            if (lonDeg > 180) lonDeg -= 360
            if (lonDeg < -180) lonDeg += 360
            Math.toDegrees(lat2) to lonDeg
        }
    }
}
