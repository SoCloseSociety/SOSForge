package co.soclose.sosforge.feature.common

import co.soclose.sosforge.LiveUrl
import co.soclose.sosforge.core.Here
import co.soclose.sosforge.core.alert.AlertRules
import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.Severity
import co.soclose.sosforge.core.net.SosEvent

/**
 * The Live "near me" view, pure (NearMeTest). Owner, 2026-10-27: by default Live shows what is near the
 * person (their position, their places); with no position granted and no place, the world's latest.
 * And a severe event elsewhere never vanishes: its count stays on screen in the near view.
 */
object NearMe {
    /** The points "near me" is measured from: the places, plus where the person is (if granted). */
    fun points(places: List<Place>, here: Here?): List<Place> = LiveUrl.zonePlaces(places, here)

    /** The default view: near when there is anything to be near to, the world otherwise. */
    fun defaultNear(points: List<Place>): Boolean = points.isNotEmpty()

    /** Positioned: inside a point's reach. Positionless: issued for a point's country (that place said yes). */
    fun isNear(e: SosEvent, points: List<Place>): Boolean =
        if (e.hasPosition) points.any { p -> (Geo.distanceKm(p, e) ?: Double.MAX_VALUE) <= Geo.reachKm(p, e) }
        else e.countryCode != null && points.any { it.countryWide && it.countryCode.equals(e.countryCode, ignoreCase = true) }

    fun near(events: List<SosEvent>, points: List<Place>): List<SosEvent> = events.filter { isNear(it, points) }

    /** Severe, extreme or tsunami, still current, and NOT near: what the near view must keep counting. */
    fun elsewhereSevere(events: List<SosEvent>, points: List<Place>, serverNow: Long): List<SosEvent> =
        events.filter { !it.isEnded(serverNow) && AlertRules.rankOf(it) >= Severity.SEVERE.rank && !isNear(it, points) }
            .distinctBy { it.clusterKey }
}
