package co.soclose.sosforge

import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.alert.AlertPrefs
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.live.Link
import co.soclose.sosforge.core.live.LinkRules
import co.soclose.sosforge.core.live.Watchdog
import co.soclose.sosforge.core.net.ApiVersion
import co.soclose.sosforge.core.net.GuidanceReader
import co.soclose.sosforge.core.net.Scope
import co.soclose.sosforge.core.net.ServerAddress
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.core.watch.WatchMode
import co.soclose.sosforge.core.watch.WatchRules
import co.soclose.sosforge.feature.common.DataNote
import co.soclose.sosforge.feature.common.FeedStatusRules
import co.soclose.sosforge.feature.common.LiveFilter
import co.soclose.sosforge.feature.funnel.Step
import co.soclose.sosforge.feature.places.Nearby
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WatchdogTest {
    @Test fun an_open_socket_silent_past_15_s_is_dead() {
        assertFalse(Watchdog.isDead(true, lastMessageAt = 1_000, openedAt = 500, now = 16_000))
        assertTrue(Watchdog.isDead(true, lastMessageAt = 1_000, openedAt = 500, now = 16_001))
    }

    @Test fun a_socket_that_opens_and_never_speaks_is_closed_too() {
        // trap 1: no message yet, the reference is the opening
        assertTrue(Watchdog.isDead(true, lastMessageAt = 0, openedAt = 10_000, now = 25_001))
    }

    @Test fun after_a_reconnect_a_stale_last_message_does_not_kill_the_new_socket() {
        // trap 2: last message from before a 10 min outage, socket opened 3 s ago
        assertFalse(Watchdog.isDead(true, lastMessageAt = 1_000, openedAt = 600_000, now = 603_000))
    }

    @Test fun a_closed_socket_is_not_judged() {
        assertFalse(Watchdog.isDead(false, 1, 1, 1_000_000))
    }

    @Test fun backoff_climbs_1_2_4_8_then_15_s() {
        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 15_000L, 15_000L), (1..6).map { Watchdog.backoffMs(it) })
    }

    @Test fun a_socket_stuck_connecting_times_out_after_10_s() {
        assertFalse(Watchdog.connectTimedOut(false, startedAt = 1_000, now = 11_000))
        assertTrue(Watchdog.connectTimedOut(false, startedAt = 1_000, now = 11_001))
        assertFalse(Watchdog.connectTimedOut(true, startedAt = 1_000, now = 99_000))
        assertFalse(Watchdog.connectTimedOut(false, startedAt = 0, now = 99_000))
    }

    @Test fun a_busy_server_is_retried_with_jitter_and_never_sooner_than_5_s() {
        assertTrue(Watchdog.isBusy(1013, null))
        assertTrue(Watchdog.isBusy(null, 403))
        assertTrue(Watchdog.isBusy(null, 429))
        assertFalse(Watchdog.isBusy(1000, null))
        assertFalse(Watchdog.isBusy(null, null))
        assertEquals(1_000L, Watchdog.retryDelayMs(1, busy = false, random = 0.0))
        assertEquals(1_300L, Watchdog.retryDelayMs(1, busy = false, random = 1.0))
        assertTrue(Watchdog.retryDelayMs(1, busy = true, random = 0.0) >= 5_000L)
        // two phones behind one proxy do not come back on the same second
        assertTrue(Watchdog.retryDelayMs(3, true, 0.1) != Watchdog.retryDelayMs(3, true, 0.9))
    }

    @Test fun live_only_with_an_open_socket_that_spoke_within_15_s() {
        assertEquals(Link.LIVE, LinkRules.status(true, 10_000, 9_000, 20_000, true, true))
        assertEquals(Link.RECONNECTING, LinkRules.status(true, 10_000, 9_000, 25_001, true, true))
        // opened, nothing heard yet: not live
        assertEquals(Link.CONNECTING, LinkRules.status(true, 0, 9_000, 10_000, false, true))
        // a message from BEFORE this socket opened proves nothing about it
        assertEquals(Link.RECONNECTING, LinkRules.status(true, 5_000, 9_000, 10_000, true, true))
        assertEquals(Link.OFFLINE, LinkRules.status(false, 10_000, 9_000, 10_500, true, false))
        assertEquals(Link.BUSY, LinkRules.status(false, 10_000, 9_000, 10_500, true, true, busy = true))
    }
}

class GeoTest {
    @Test fun haversine_matches_known_distances() {
        // great-circle: Paris -> London ~344 km, Tokyo -> Osaka ~392 km (not the 397 km of the road)
        assertEquals(344.0, Geo.distanceKm(48.8566, 2.3522, 51.5074, -0.1278), 3.0)
        assertEquals(392.4, Geo.distanceKm(35.6762, 139.6503, 34.6937, 135.5023), 3.0)
        assertEquals(0.0, Geo.distanceKm(10.0, 10.0, 10.0, 10.0), 1e-9)
    }

    @Test fun a_place_circle_closes_and_sits_at_its_radius() {
        val ring = Geo.circle(35.0, 139.0, 300.0)
        assertEquals(ring.first().first, ring.last().first, 1e-6)
        ring.forEach { (la, lo) -> assertEquals(300.0, Geo.distanceKm(35.0, 139.0, la, lo), 1.0) }
    }

    @Test fun a_place_out_of_the_globe_is_not_valid() {
        assertFalse(Place("x", "x", 95.0, 0.0).valid)
        assertFalse(Place("x", "x", 0.0, 0.0, radiusKm = 5000).valid)
        assertTrue(Place("x", "x", -33.9, 151.2).valid)
    }

    @Test fun the_scope_is_lane_b_syntax_and_asks_for_earthquakes_when_tsunamis_are_watched() {
        val s = Scope.of(listOf(Place("a", "A", 35.6762, 139.6503, 100), Place("b", "B", -6.2, 106.8, 300)), AlertPrefs(kinds = setOf("tsunami"), minSeverity = "severe"))!!
        assertEquals("35.6762,139.6503,1000;-6.2000,106.8000,1000", s.zonesParam)
        assertEquals("earthquake,tsunami", s.kindsParam)
        assertEquals("severe", s.minSeverity)
        val decoded = java.net.URLDecoder.decode(s.wsQuery(), "UTF-8")
        assertEquals("zones=35.6762,139.6503,1000;-6.2000,106.8000,1000&min_severity=severe&kinds=earthquake,tsunami", decoded)
        assertNull(Scope.of(emptyList(), AlertPrefs()))
    }

    @Test fun an_unknown_kind_is_never_sent_to_the_server() {
        val s = Scope.of(listOf(Place("a", "A", 1.0, 1.0)), AlertPrefs(kinds = setOf("earthquake", "meteor")))!!
        assertEquals("earthquake", s.kindsParam)
    }
}

class ServerAddressTest {
    @Test fun owner_typing_becomes_one_origin() {
        assertEquals("https://sosforge.soclose.co", ServerAddress.normalize("sosforge.soclose.co"))
        assertEquals("https://sosforge.soclose.co", ServerAddress.normalize(" https://sosforge.soclose.co/api/ "))
        assertEquals(
            if (co.soclose.sosforge.core.net.DevHosts.OVERRIDE) "http://10.0.2.2:8300" else null,
            ServerAddress.normalize("http://10.0.2.2:8300"),
        )
        assertNull("plain http to the internet is refused", ServerAddress.normalize("http://sosforge.soclose.co"))
        assertNull(ServerAddress.normalize("ftp://x"))
        assertNull(ServerAddress.normalize("https://x.co/somewhere"))
        assertNull(ServerAddress.normalize(""))
    }

    @Test fun websocket_url_follows_the_scheme() {
        assertEquals("wss://sosforge.soclose.co/ws", ServerAddress.wsUrl("https://sosforge.soclose.co"))
        assertEquals("ws://10.0.2.2:8300/ws?zones=1", ServerAddress.wsUrl("http://10.0.2.2:8300", "zones=1"))
    }
}

class ScreensLogicTest {
    private val now = Fixtures.snapshotTime
    private val all = co.soclose.sosforge.core.net.Kind.entries.map { it.key }.toSet()

    @Test fun the_time_window_never_cuts_a_running_alert() {
        val events = Fixtures.snapshot.events
        val live15 = LiveFilter.apply(events, 15, all, now)
        val running = events.filter { it.ongoing && (it.expiresMs ?: Long.MAX_VALUE) > now }
        assertTrue(live15.containsAll(running))
        // and an ended one is cut like a point event
        assertTrue(LiveFilter.apply(events, 0, all, now).size == events.size)
    }

    @Test fun kinds_filter_keeps_tsunami_flagged_quakes_under_tsunamis() {
        val q = Fixtures.real().copy(tsunami = true)
        assertEquals(1, LiveFilter.apply(listOf(q), 0, setOf("tsunami"), now).size)
        assertEquals(0, LiveFilter.apply(listOf(q.copy(tsunami = false)), 0, setOf("tsunami"), now).size)
    }

    @Test fun nearby_lists_the_most_severe_first_within_reach() {
        val e = Fixtures.real()
        val p = Place("p", "P", e.lat!!, e.lon!!, 300)
        val near = Nearby.of(p, Fixtures.snapshot.events, now, max = 10)
        assertTrue(near.all { it.second <= Geo.reachKm(p, it.first) })
        val ranks = near.map { it.first.severityOf.rank }
        assertEquals(ranks.sortedDescending(), ranks)
    }

    @Test fun the_banner_says_the_truth_about_the_data() {
        val live = FeedState(lastMessageAt = 1)
        assertEquals(DataNote.NONE, FeedStatusRules.note(Link.LIVE, live))
        assertEquals(DataNote.CACHE, FeedStatusRules.note(Link.LIVE, live.copy(fromCache = true)))
        assertEquals(DataNote.UNREACHABLE, FeedStatusRules.note(Link.RECONNECTING, live))
        assertEquals(DataNote.OFFLINE, FeedStatusRules.note(Link.OFFLINE, live.copy(fromCache = true)))
        assertEquals(DataNote.BUSY, FeedStatusRules.note(Link.BUSY, live))
        assertEquals(DataNote.WAITING, FeedStatusRules.note(Link.CONNECTING, FeedState()))
    }

    @Test fun ages_pick_the_web_buckets_and_lead_time_is_said_as_lead_time() {
        assertEquals("now" to 0L, Fmt.ageBucket(3))
        assertEquals("s" to 42L, Fmt.ageBucket(42))
        assertEquals("min" to 12L, Fmt.ageBucket(12 * 60 + 5))
        assertEquals("h" to 3L, Fmt.ageBucket(3 * 3600 + 1))
        assertEquals("d" to 2L, Fmt.ageBucket(2 * 86400 + 1))
        assertEquals("in_h" to 2L, Fmt.ageBucket(-2 * 3600))
        // a warning starting in 30 s is "now", not "in 0 min"
        assertEquals("now" to 0L, Fmt.ageBucket(-30))
    }

    @Test fun feed_html_entities_are_decoded_for_display_and_only_once() {
        val real = Fixtures.snapshot.events.first { it.place.contains("&amp;") }
        assertFalse(real.displayPlace.contains("&amp;"))
        assertEquals("East Sterea & Evvoia", co.soclose.sosforge.core.net.Entities.decode("East Sterea &amp; Evvoia"))
        assertEquals("&lt;", co.soclose.sosforge.core.net.Entities.decode("&amp;lt;"))
        assertEquals("temperatures < 32F", co.soclose.sosforge.core.net.Entities.decode("temperatures < 32F"))
        assertEquals("caf\u00e9", co.soclose.sosforge.core.net.Entities.decode("caf&#233;"))
    }

    @Test fun a_warning_before_its_onset_is_upcoming_not_running() {
        val w = Fixtures.snapshot.events.first { it.ongoing && (it.timeMs ?: 0) > now }
        assertTrue(w.isUpcoming(now))
        assertFalse(Fixtures.real().isUpcoming(now))
    }

    @Test fun a_flag_is_exact_or_absent() {
        assertEquals("🇯🇵", Fmt.flag("JP"))
        assertNull(Fmt.flag("12"))
        assertNull(Fmt.flag(null))
        assertNull(Fmt.flag("JPN"))
    }

    @Test fun watch_mode_says_plainly_when_the_live_watch_is_not_running() {
        assertEquals(WatchMode.OFF, WatchRules.mode(false, true, true))
        assertEquals(WatchMode.LIVE, WatchRules.mode(true, true, true))
        assertEquals(WatchMode.LIVE_NOT_RUNNING, WatchRules.mode(true, true, false))
        assertEquals(WatchMode.PERIODIC, WatchRules.mode(true, false, false))
    }

    @Test fun the_funnel_goes_forward_and_back_without_stranding() {
        assertNull(Step.WELCOME.back())
        assertEquals(Step.PLACES, Step.WELCOME.next())
        assertEquals(Step.DONE, Step.DONE.next())
        assertEquals(Step.BACKGROUND, Step.DONE.back())
        assertEquals(4, Step.QUESTIONS.size)
        var s = Step.WELCOME
        repeat(10) { s = s.next() }
        assertEquals(Step.DONE, s)
    }
}
