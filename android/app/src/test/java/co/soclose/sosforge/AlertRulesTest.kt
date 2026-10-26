package co.soclose.sosforge

import co.soclose.sosforge.core.alert.AlertContext
import co.soclose.sosforge.core.alert.AlertPrefs
import co.soclose.sosforge.core.alert.AlertRules
import co.soclose.sosforge.core.alert.Effect
import co.soclose.sosforge.core.alert.Entry
import co.soclose.sosforge.core.alert.Ledger
import co.soclose.sosforge.core.alert.Level
import co.soclose.sosforge.core.alert.QuietHours
import co.soclose.sosforge.core.feed.FeedChange
import co.soclose.sosforge.core.feed.FeedReducer
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.live.ServerMessage
import co.soclose.sosforge.core.net.SosEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * The rules that decide whether to interrupt a person. Events are REAL ones from the capture, moved in
 * time or re-ranked where a case needs it (the shape stays the server's).
 */
class AlertRulesTest {
    private val now = Fixtures.snapshotTime
    private val iso = { ms: Long -> Instant.ofEpochMilli(ms).toString() }
    private val quake = Fixtures.real()

    /** A place 40 km north of the event, 300 km radius. */
    private val home = Place("home", "Home", quake.lat!! + 0.36, quake.lon!!, 300)
    private val far = Place("far", "Far", -quake.lat!!, quake.lon!! + 179.0, 50)
    private val prefs = AlertPrefs()

    private fun ev(sev: String = "severe", ageMin: Long = 5, revision: Int = 1, id: String = "test:1", ongoing: Boolean = false, expires: Long? = null) =
        quake.copy(id = id, clusterId = null, severity = sev, time = iso(now - ageMin * 60_000L), revision = revision, ongoing = ongoing, expires = expires?.let(iso), tsunami = false)

    private fun ctx(quiet: Boolean = false, places: List<Place> = listOf(home)) = AlertContext(places, prefs, now, now, quiet)

    private fun up(e: SosEvent, snapshot: Boolean = false, breaking: Boolean = false, escalated: Boolean = false) =
        FeedChange.Upserted(e, null, fromSnapshot = snapshot, breaking = breaking, escalated = escalated)

    private fun Ledger.live(vararg c: FeedChange, quiet: Boolean = false) = onChanges(c.toList(), ctx(quiet), fullRead = false)

    /** A ledger that has already taken its first full read as history. */
    private fun seasoned() = Ledger().also { it.onChanges(emptyList(), ctx(), fullRead = true) }

    // ------------------------------------------------------------------- matching

    @Test fun an_event_matches_the_nearest_place_inside_its_radius_only() {
        val m = AlertRules.match(ev(), listOf(far, home), prefs)!!
        assertEquals("home", m.place.id)
        assertTrue(m.distanceKm in 35.0..45.0)
        assertNull(AlertRules.match(ev(), listOf(far), prefs))
    }

    @Test fun an_event_without_a_position_never_matches() {
        assertNull(AlertRules.match(ev().copy(lat = null, lon = null), listOf(home), prefs))
    }

    @Test fun a_tsunami_reaches_1000_km_whatever_the_radius() {
        val coast = Place("c", "Coast", quake.lat!! + 7.0, quake.lon!!, 50) // ~780 km
        val t = ev().copy(kind = "tsunami", tsunami = true)
        assertEquals(Level.EXTREME, AlertRules.match(t, listOf(coast), prefs)!!.level)
        assertNull(AlertRules.match(ev(), listOf(coast), prefs))
    }

    @Test fun the_owners_floor_and_kinds_filter() {
        assertNull(AlertRules.match(ev("minor"), listOf(home), prefs))
        assertNull(AlertRules.match(ev().copy(kind = "heat"), listOf(home), prefs))
        assertEquals(Level.DIGEST, AlertRules.match(ev("moderate"), listOf(home), prefs)!!.level)
    }

    // ------------------------------------------------------------------- freshness (lesson 4)

    @Test fun an_old_point_event_is_not_news_a_running_alert_is() {
        assertTrue(AlertRules.isNews(ev(ageMin = 59), now))
        assertFalse(AlertRules.isNews(ev(ageMin = 61), now))
        // issued three days ago and still running: news (an age cut would drop exactly these)
        assertTrue(AlertRules.isNews(ev(ageMin = 3 * 1440, ongoing = true), now))
        // a warning with lead time (dated in the future) is news
        assertTrue(AlertRules.isNews(ev(ageMin = -120), now))
    }

    @Test fun an_ended_alert_is_never_news_and_never_rings() {
        val ended = ev(ongoing = true, expires = now - 60_000L)
        assertFalse(AlertRules.isNews(ended, now))
        assertTrue(seasoned().live(up(ended, breaking = true)).isEmpty())
    }

    @Test fun an_old_event_arriving_now_does_not_ring() {
        assertTrue(seasoned().live(up(ev(ageMin = 180))).isEmpty())
    }

    // ------------------------------------------------------------------- never twice, update, cancel

    @Test fun a_new_severe_event_near_home_rings_once() {
        val l = seasoned()
        val first = l.live(up(ev()))
        assertEquals(1, first.size)
        assertTrue((first[0] as Effect.Show).alert)
        assertTrue("same revision re-sent by a reconnect", l.live(up(ev(), snapshot = true)).isEmpty())
        assertTrue(l.live(up(ev())).isEmpty())
    }

    @Test fun a_revision_updates_in_place_silently() {
        val l = seasoned()
        val id = (l.live(up(ev()))[0] as Effect.Show).entry.notifId
        val fx = l.live(up(ev(revision = 2)))
        val show = fx.single() as Effect.Show
        assertFalse(show.alert)
        assertEquals(id, show.entry.notifId)
    }

    @Test fun a_purge_cancels_and_the_key_never_rings_again() {
        val l = seasoned()
        val id = (l.live(up(ev()))[0] as Effect.Show).entry.notifId
        // a cancelled EEW: the server purges it with reason "cancelled"
        assertEquals(listOf(Effect.Remove(id, "test:1")), l.live(FeedChange.Removed("test:1", "cancelled")))
        assertTrue(l.live(up(ev(revision = 5))).isEmpty())
    }

    @Test fun a_revision_that_moves_it_away_or_downgrades_it_cancels() {
        val l = seasoned()
        l.live(up(ev()))
        val moved = ev(revision = 2).copy(lat = far.lat, lon = far.lon + 10)
        assertTrue(l.live(up(moved)).single() is Effect.Remove)
        val l2 = seasoned()
        l2.live(up(ev()))
        assertTrue(l2.live(up(ev("minor", revision = 2))).single() is Effect.Remove)
    }

    @Test fun two_solutions_of_one_quake_ring_once() {
        val l = seasoned()
        val a = ev().copy(id = "emsc:1", clusterId = "c1")
        val b = ev().copy(id = "usgs:1", clusterId = "c1", revision = 0)
        assertEquals(1, l.live(up(a)).size)
        val second = l.live(up(b)).single() as Effect.Show
        assertFalse("a new representative updates, never rings", second.alert)
        // the purge of the OLD representative's id does not cancel the cluster's notification
        assertTrue(l.live(FeedChange.Removed("emsc:1", "stale")).isEmpty())
    }

    // ------------------------------------------------------------------- escalation (audit rule 1)

    @Test fun an_event_seen_below_threshold_rings_when_it_escalates() {
        // the macOS agent marked such an id "announced" and never rang the escalation
        val l = seasoned()
        assertTrue(l.live(up(ev("minor"))).isEmpty())
        val fx = l.live(up(ev("severe", revision = 2), escalated = true))
        assertTrue((fx.single() as Effect.Show).alert)
    }

    @Test fun a_rise_without_the_flag_rings_only_while_it_is_news() {
        val l = seasoned()
        l.live(up(ev("minor")))
        assertTrue((l.live(up(ev("severe", revision = 2))).single() as Effect.Show).alert)
        val old = seasoned()
        old.live(up(ev("minor", ageMin = 200)))
        assertTrue("a 3 h old event revised upward is history", old.live(up(ev("severe", ageMin = 200, revision = 2))).isEmpty())
        assertTrue("unless the server says it escalated", (old.live(up(ev("severe", ageMin = 200, revision = 3), escalated = true)).single() as Effect.Show).alert)
    }

    @Test fun a_notified_event_rings_again_only_above_the_severity_it_rang_for() {
        val l = seasoned()
        l.live(up(ev("moderate")))
        assertTrue((l.live(up(ev("extreme", revision = 2))).single() as Effect.Show).alert)
        assertFalse((l.live(up(ev("severe", revision = 3))).single() as Effect.Show).alert)
        assertFalse("back to the rank already rung", (l.live(up(ev("extreme", revision = 4))).single() as Effect.Show).alert)
    }

    // ------------------------------------------------------------------- history (audit rule 2)

    @Test fun only_the_first_full_read_after_install_is_history() {
        val l = Ledger()
        assertTrue(l.onChanges(listOf(up(ev(), snapshot = true)), ctx(), fullRead = true).isEmpty())
        // a reconnect snapshot carrying an M6.6 that happened during the outage: it rings
        val outage = ev(id = "emsc:outage").copy(magnitude = 6.6)
        val fx = l.onChanges(listOf(up(ev(), snapshot = true), up(outage, snapshot = true)), ctx(), fullRead = true)
        assertEquals("emsc:outage", (fx.single() as Effect.Show).event.id)
    }

    @Test fun an_event_that_did_not_concern_you_rings_when_it_moves_into_your_zone() {
        val l = seasoned()
        val away = ev().copy(lat = home.lat + 4.0) // ~400 km: near enough to be remembered, outside 300 km
        assertTrue(l.live(up(away)).isEmpty())
        assertTrue((l.live(up(ev(revision = 2))).single() as Effect.Show).alert)
    }

    // ------------------------------------------------------------------- quiet hours

    @Test fun quiet_hours_cross_midnight_and_an_empty_window_is_never_all_day() {
        val q = QuietHours(true, 23 * 60, 7 * 60)
        assertTrue(q.active(23 * 60))
        assertTrue(q.active(3 * 60))
        assertFalse(q.active(7 * 60))
        assertFalse(q.active(12 * 60))
        assertFalse(QuietHours(true, 8 * 60, 8 * 60).active(8 * 60))
        assertFalse(QuietHours(false).active(3 * 60))
        assertTrue(QuietHours(true, 13 * 60, 15 * 60).active(14 * 60))
    }

    @Test fun quiet_hours_hold_severe_but_never_hold_an_extreme_near_you() {
        val l = seasoned()
        assertTrue(l.live(up(ev("severe")), quiet = true).isEmpty())
        assertEquals(1, l.heldCount)
        val fx = l.live(up(ev("extreme", id = "test:2")), quiet = true)
        assertTrue((fx.single() as Effect.Show).alert)
    }

    @Test fun a_held_warning_is_dropped_when_a_newer_extreme_one_posts() {
        val l = seasoned()
        l.live(up(ev("severe", id = "test:held")), quiet = true)
        l.live(up(ev("extreme", id = "test:urgent")), quiet = true)
        assertEquals(0, l.heldCount)
        // dropped means never notified, even after the night
        assertTrue(l.release(ctx(quiet = false)) { null }.isEmpty())
        assertTrue(l.live(up(ev("severe", id = "test:held", revision = 2))).isEmpty())
    }

    @Test fun held_warnings_post_when_quiet_hours_end_unless_withdrawn() {
        val l = seasoned()
        val kept = ev("severe", id = "a:kept")
        val gone = ev("severe", id = "a:gone")
        l.live(up(kept), up(gone), quiet = true)
        l.live(FeedChange.Removed("a:gone", "lifted"), quiet = true)
        assertTrue("still quiet", l.release(ctx(quiet = true)) { kept }.isEmpty())
        val fx = l.release(ctx(quiet = false)) { id -> if (id == "a:kept") kept else null }
        assertEquals(listOf("a:kept"), fx.map { (it as Effect.Show).event.id })
        assertEquals(Entry.State.POSTED, l.state.entries["a:kept"]!!.state)
    }

    @Test fun a_held_warning_that_escalates_to_extreme_rings_at_once() {
        val l = seasoned()
        l.live(up(ev("severe")), quiet = true)
        assertTrue((l.live(up(ev("extreme", revision = 2)), quiet = true).single() as Effect.Show).alert)
    }

    @Test fun pruning_retires_old_running_alerts_so_they_never_ring_again() {
        val l = seasoned()
        val running = ev(ongoing = true, ageMin = 10 * 1440)
        assertEquals(1, l.live(up(running)).size)
        l.prune(now + Ledger.PRUNE_MS + 1)
        assertTrue(l.live(up(running.copy(revision = 9))).isEmpty())
    }

    @Test fun notification_ids_are_stable_and_never_collide_with_the_fixed_ones() {
        assertEquals(AlertRules.notifIdFor("emsc:1"), AlertRules.notifIdFor("emsc:1"))
        assertTrue((0..5000).all { AlertRules.notifIdFor("k$it") >= 100 })
    }

    // ------------------------------------------------------------------- lesson 24: replay real traffic

    /**
     * How often would these rules ring on REAL traffic? The capture replayed for three places, as a
     * reconnect (not history) so every id is judged. The numbers are printed: they are what decides.
     */
    @Test fun replay_the_real_capture_and_count_the_rings() {
        val tokyo = Place("t", "Tokyo", 35.68, 139.76, 300)
        val la = Place("l", "Los Angeles", 34.05, -118.24, 300)
        val athens = Place("a", "Athens", 37.98, 23.73, 300)
        for (p in listOf(tokyo, la, athens)) {
            val l = seasoned()
            var feed = FeedState()
            var rings = 0
            var silent = 0
            var t = now
            for (m in Fixtures.ws) {
                val r = FeedReducer.reduce(feed, m, t)
                feed = r.state
                val fx = l.onChanges(r.changes, AlertContext(listOf(p), prefs, feed.serverNow(t), t, false), fullRead = m is ServerMessage.Snapshot)
                rings += fx.count { it is Effect.Show && it.alert }
                fx.filterIsInstance<Effect.Show>().filter { it.alert }.forEach { println("  ${p.name} rang: ${it.event.id} ${it.event.kind} ${it.event.severity} ongoing=${it.event.ongoing} ${it.event.time} ${"%.0f".format(it.match.distanceKm)} km ${it.event.displayPlace.take(60)}") }
                silent += fx.count { it is Effect.Show && !it.alert }
                t += 200
            }
            println("replay ${p.name}: rings=$rings silent-updates=$silent posted=${l.state.entries.size} seen=${l.state.seen.size}")
            // a 300 km zone around one city does not ring for the world
            assertTrue("${p.name} rang $rings times", rings <= 5)
        }
    }

    @Test fun replaying_the_capture_a_second_time_rings_nothing() {
        val p = Place("t", "Tokyo", 35.68, 139.76, 1000)
        val l = seasoned()
        repeat(2) { pass ->
            var feed = FeedState()
            var rings = 0
            for (m in Fixtures.ws) {
                val r = FeedReducer.reduce(feed, m, now)
                feed = r.state
                rings += l.onChanges(r.changes, AlertContext(listOf(p), prefs, now, now, false), fullRead = m is ServerMessage.Snapshot).count { it is Effect.Show && it.alert }
            }
            if (pass == 1) assertEquals("never twice", 0, rings)
        }
    }

    @Test fun the_scope_radius_never_drops_what_the_client_keeps() {
        assertEquals(1000, Geo.scopeRadiusKm(home, tsunamiWatched = true))
        assertEquals(300, Geo.scopeRadiusKm(home, tsunamiWatched = false))
    }
}
