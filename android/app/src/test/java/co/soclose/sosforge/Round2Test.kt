package co.soclose.sosforge

import android.location.LocationManager
import co.soclose.sosforge.core.Here
import co.soclose.sosforge.core.alert.AlertRules
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.map.MapStyles
import co.soclose.sosforge.core.map.PackPlan
import co.soclose.sosforge.core.net.Severity
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.core.sys.Fix
import co.soclose.sosforge.core.sys.LocateRules
import co.soclose.sosforge.core.update.LatestRelease
import co.soclose.sosforge.core.update.UpdateRules
import co.soclose.sosforge.core.update.UpdateState
import co.soclose.sosforge.feature.common.NearMe
import co.soclose.sosforge.feature.places.LocateFlow
import co.soclose.sosforge.feature.places.LocateStep
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LocateRulesTest {
    @Test fun every_outcome_of_use_my_location_has_its_own_state() {
        assertEquals(LocateStep.IDLE, LocateFlow.afterFix(Fix.Ok(1.0, 2.0, 0)))
        assertEquals(LocateStep.DENIED, LocateFlow.afterFix(Fix.Denied))
        assertEquals(LocateStep.LOCATION_OFF, LocateFlow.afterFix(Fix.LocationOff))
        assertEquals(LocateStep.FAILED, LocateFlow.afterFix(Fix.NoFix))
    }

    @Test fun a_refusal_is_for_good_only_once_android_stops_asking() {
        // first refusal: Android will ask again -> plain "refused"
        assertEquals(LocateStep.DENIED, LocateFlow.afterRefusal(askedBefore = false, systemWillAskAgain = false))
        assertEquals(LocateStep.DENIED, LocateFlow.afterRefusal(askedBefore = true, systemWillAskAgain = true))
        // asked before and no rationale any more: the system dialog will not show -> point to the settings
        assertEquals(LocateStep.DENIED_FOREVER, LocateFlow.afterRefusal(askedBefore = true, systemWillAskAgain = false))
    }

    @Test fun a_last_known_fix_is_used_at_once_when_recent_and_refused_when_old() {
        assertTrue(LocateRules.recentEnough(60_000))
        assertFalse(LocateRules.recentEnough(3 * 60_000))
        assertTrue(LocateRules.usableFallback(9 * 60_000))
        assertFalse("a 30 min old fix may be another city", LocateRules.usableFallback(30 * 60_000))
        assertFalse(LocateRules.usableFallback(null))
        assertFalse("a fix from the future (clock change) is not trusted", LocateRules.usableFallback(-5))
    }

    @Test fun the_providers_are_asked_fused_first_and_only_when_enabled() {
        assertEquals(listOf(LocationManager.FUSED_PROVIDER, LocationManager.GPS_PROVIDER), LocateRules.providers(34) { it != LocationManager.NETWORK_PROVIDER })
        assertEquals(listOf(LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER), LocateRules.providers(29) { true })
        assertTrue(LocateRules.providers(34) { false }.isEmpty())
        assertEquals(15_000L, LocateRules.TIMEOUT_MS)
    }
}

/**
 * The latest.json contract (tasks/todo.md, Round 2): NOT captured, lane G has not deployed it yet
 * (https://sosforge.soclose.co/download/latest.json answers 404); built from the contract's own example.
 */
class UpdateRulesTest {
    private val pinned = UpdateRules.PINNED_CERT_SHA256
    private fun rel(code: Int = 8, cert: String = pinned, url: String = "/download/sosforge-v0.1.3.apk", app: String = "co.soclose.sosforge", minSdk: Int = 26) =
        SosJson.decodeFromString(
            LatestRelease.serializer(),
            """{"app": "$app", "version": "0.1.3", "version_code": $code, "url": "$url", "size": 40000000, "sha256": "ab", "cert_sha256": "$cert", "min_sdk": $minSdk, "released": "2026-10-28", "notes": {"en": "x", "fr": "y"}}""",
        )

    private val origin = "https://sosforge.soclose.co"

    @Test fun a_newer_release_signed_by_the_same_key_is_offered_with_an_absolute_link() {
        val s = UpdateRules.decide(rel(), origin, myCode = 7, sdk = 34)
        assertTrue(s is UpdateState.Available)
        assertEquals("https://sosforge.soclose.co/download/sosforge-v0.1.3.apk", (s as UpdateState.Available).downloadUrl)
    }

    @Test fun the_same_or_an_older_version_is_up_to_date() {
        assertEquals(UpdateState.UpToDate, UpdateRules.decide(rel(code = 7), origin, 7, 34))
        assertEquals(UpdateState.UpToDate, UpdateRules.decide(rel(code = 6), origin, 7, 34))
    }

    @Test fun another_signer_is_never_offered_whatever_its_version() {
        val s = UpdateRules.decide(rel(code = 99, cert = "00".repeat(32)), origin, 7, 34)
        assertEquals(UpdateState.Refused(UpdateState.Why.OTHER_SIGNER), s)
        // the same digest written with colons and capitals is the same key
        val colons = pinned.uppercase().chunked(2).joinToString(":")
        assertTrue(UpdateRules.decide(rel(cert = colons), origin, 7, 34) is UpdateState.Available)
    }

    @Test fun another_app_an_android_too_old_or_a_cleartext_link_are_refused() {
        assertEquals(UpdateState.Refused(UpdateState.Why.OTHER_APP), UpdateRules.decide(rel(app = "co.soclose.other"), origin, 7, 34))
        assertEquals(UpdateState.Refused(UpdateState.Why.NEEDS_NEWER_ANDROID), UpdateRules.decide(rel(minSdk = 36), origin, 7, 34))
        assertEquals(UpdateState.Refused(UpdateState.Why.BAD_URL), UpdateRules.decide(rel(url = "http://evil.example/x.apk"), origin, 7, 34))
        // the emulator's host alias is a cleartext host in a DEBUG build only; a release refuses it
        val dev = UpdateRules.decide(rel(url = "/download/x.apk"), "http://10.0.2.2:8361", 7, 34)
        if (co.soclose.sosforge.core.net.DevHosts.OVERRIDE) assertTrue(dev is UpdateState.Available)
        else assertEquals(UpdateState.Refused(UpdateState.Why.BAD_URL), dev)
    }

    @Test fun a_dismissed_card_stays_hidden_for_that_version_only() {
        val s = UpdateRules.decide(rel(code = 8), origin, 7, 34)
        assertTrue(UpdateRules.showCard(s, dismissedCode = 0))
        assertFalse(UpdateRules.showCard(s, dismissedCode = 8))
        assertTrue(UpdateRules.showCard(UpdateRules.decide(rel(code = 9), origin, 7, 34), dismissedCode = 8))
        assertFalse(UpdateRules.showCard(UpdateState.UpToDate, 0))
    }
}

class PackPlanTest {
    private val athens = Place("ath", "Athens", 37.98, 23.73, 300)

    @Test fun the_world_is_zooms_0_to_4_and_a_300_km_place_stops_where_the_budget_says() {
        val specs = PackPlan.wanted(listOf(athens), listOf(MapStyles.DARK))
        val world = specs.first { it.placeId == null }
        assertEquals(0 to 4, world.minZoom to world.maxZoom)
        assertEquals(341L, PackPlan.tiles(PackPlan.WORLD, 0, 4))
        val place = specs.first { it.placeId == "ath" }
        // measured 2026-10-27 on OpenFreeMap: z5-9 ~13-15 MB, z5-10 ~23-30 MB for a 300 km radius
        assertEquals(9, place.maxZoom)
        assertTrue(PackPlan.tiles(place.bounds, 5, place.maxZoom) <= PackPlan.PLACE_TILE_BUDGET)
        assertTrue(PackPlan.tiles(place.bounds, 5, place.maxZoom + 1) > PackPlan.PLACE_TILE_BUDGET)
    }

    @Test fun a_small_radius_goes_deeper_a_large_one_shallower_never_past_12() {
        val small = PackPlan.placeMaxZoom(PackPlan.bounds(athens.copy(radiusKm = 50)))
        val large = PackPlan.placeMaxZoom(PackPlan.bounds(athens.copy(radiusKm = 1000)))
        assertTrue(small > 9)
        assertTrue(large < 9)
        assertTrue(small <= PackPlan.PLACE_MAX_ZOOM && large >= PackPlan.PLACE_MIN_ZOOM)
    }

    @Test fun a_removed_place_or_a_new_radius_drops_its_pack() {
        val before = PackPlan.wanted(listOf(athens)).map { it.key }.toSet()
        val (drop, create) = PackPlan.diff(before, PackPlan.wanted(emptyList()))
        assertEquals(setOf("place:ath:300|dark", "place:ath:300|light"), drop)
        assertTrue(create.isEmpty())
        val (drop2, create2) = PackPlan.diff(before, PackPlan.wanted(listOf(athens.copy(radiusKm = 100))))
        assertEquals(2, drop2.size)
        assertEquals(setOf("place:ath:100|dark", "place:ath:100|light"), create2.map { it.key }.toSet())
    }

    @Test fun bounds_stay_inside_the_mercator_band_and_the_antimeridian() {
        val b = PackPlan.bounds(Place("f", "Fiji", -17.7, 179.9, 600))
        assertTrue(b.east <= 180.0 && b.west >= -180.0)
        val n = PackPlan.bounds(Place("s", "Svalbard", 84.0, 15.0, 600))
        assertTrue(n.north <= 85.0)
    }
}

class NearMeTest {
    private val now = Fixtures.snapshotTime
    private val events = Fixtures.snapshot.events
    private val athens = Place("ath", "Athens", 37.98, 23.73, 300, countryCode = "GR")

    @Test fun no_position_and_no_place_means_the_world_by_default() {
        assertFalse(NearMe.defaultNear(NearMe.points(emptyList(), null)))
        assertTrue(NearMe.defaultNear(NearMe.points(emptyList(), Here(37.98, 23.73, now))))
        assertTrue(NearMe.defaultNear(NearMe.points(listOf(athens), null)))
    }

    @Test fun near_keeps_the_reach_and_the_countries_and_nothing_else() {
        val near = NearMe.near(events, listOf(athens))
        assertTrue(near.isNotEmpty())
        near.forEach { e ->
            if (e.hasPosition) assertTrue(Geo.distanceKm(athens, e)!! <= Geo.reachKm(athens, e))
            else assertEquals("GR", e.countryCode)
        }
    }

    @Test fun a_severe_event_far_away_is_still_counted_in_the_near_view() {
        val far = Fixtures.real().copy(id = "far:tsunami", lat = -20.0, lon = -175.0, severity = "severe", tsunami = true, ongoing = true, expires = null)
        val list = NearMe.elsewhereSevere(events + far, listOf(athens), now)
        assertTrue(list.any { it.id == "far:tsunami" })
        assertTrue(list.all { AlertRules.rankOf(it) >= Severity.SEVERE.rank && !NearMe.isNear(it, listOf(athens)) })
    }

    @Test fun here_joins_the_zones_only_while_on_screen() {
        val here = Here(35.68, 139.76, now, "JP")
        val zoned = LiveUrl.zonePlaces(listOf(athens), here)
        assertEquals(2, zoned.size)
        assertEquals(LiveUrl.HERE_ID, zoned.last().id)
        assertNull(LiveUrl.zonePlaces(emptyList(), null).firstOrNull())
    }
}
