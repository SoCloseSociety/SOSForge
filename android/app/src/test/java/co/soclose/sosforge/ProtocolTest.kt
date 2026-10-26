package co.soclose.sosforge

import co.soclose.sosforge.core.feed.FeedChange
import co.soclose.sosforge.core.feed.FeedReducer
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.feed.isCurrentAlert
import co.soclose.sosforge.core.live.Protocol
import co.soclose.sosforge.core.live.ServerMessage
import co.soclose.sosforge.core.net.GeocodeReply
import co.soclose.sosforge.core.net.Iso
import co.soclose.sosforge.core.net.SosJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test fun every_line_of_the_real_socket_capture_parses_to_a_known_message() {
        val types = Fixtures.ws.map { it::class.simpleName }
        assertTrue(Fixtures.ws.none { it is ServerMessage.Unknown })
        assertTrue("snapshot first", Fixtures.ws.first() is ServerMessage.Snapshot)
        assertTrue(types.contains("Tick"))
        assertTrue(types.contains("Purge"))
        assertTrue(types.contains("Upsert"))
    }

    @Test fun the_snapshot_keeps_every_event_and_its_server_time() {
        val s = Fixtures.snapshot
        assertEquals(300, s.events.size)
        assertEquals(0, s.skipped)
        assertNotNull(s.serverTimeMs)
        assertTrue(s.sources.size >= 19)
        assertNotNull(s.stats)
    }

    @Test fun a_tick_carries_server_time_and_sources() {
        val t = Fixtures.ws.filterIsInstance<ServerMessage.Tick>().first()
        assertEquals(Iso.ms("2026-10-26T18:01:39.889784+00:00"), t.serverTimeMs)
        assertTrue(t.sources.isNotEmpty())
    }

    @Test fun a_purge_lists_ids_and_its_reason() {
        val p = Fixtures.ws.filterIsInstance<ServerMessage.Purge>().first()
        assertTrue(p.ids.contains("gdacs:WF1033178"))
        assertTrue(p.reason.isNotBlank())
    }

    @Test fun event_and_update_are_told_apart_and_carry_primary_and_breaking() {
        val ups = Fixtures.ws.filterIsInstance<ServerMessage.Upsert>()
        assertTrue(ups.any { it.isNew })
        assertTrue(ups.any { !it.isNew })
        // the capture predates lane F: no escalated flag, read as false
        assertTrue(ups.none { it.escalated })
    }

    @Test fun lane_f_escalated_flag_is_read_when_present() {
        // shape built from the capture's own first update line, plus lane F's documented `escalated: true`
        val line = Fixtures.wsLines.first { it.startsWith("{\"type\": \"update\"") }
        val withFlag = line.trimEnd().removeSuffix("}") + ", \"escalated\": true}"
        val m = Protocol.parse(withFlag) as ServerMessage.Upsert
        assertTrue(m.escalated)
    }

    @Test fun garbage_is_null_not_a_crash() {
        assertNull(Protocol.parse("not json"))
        assertTrue(Protocol.parse("{\"type\":\"what\"}") is ServerMessage.Unknown)
        // one broken event in a snapshot is skipped and counted, the rest survives
        val s = Protocol.parse("{\"type\":\"snapshot\",\"server_time\":\"2026-10-26T18:00:00Z\",\"events\":[{\"nope\":1},{\"id\":\"a\",\"time\":\"2026-10-26T17:00:00Z\"}]}") as ServerMessage.Snapshot
        assertEquals(1, s.events.size)
        assertEquals(1, s.skipped)
    }

    @Test fun lane_b_refusal_message_is_understood() {
        val m = Protocol.parse("{\"type\":\"error\",\"code\":\"bad_zones\",\"reason\":\"latitude out of range: 95.0\"}")
        assertEquals(ServerMessage.Refused("bad_zones", "latitude out of range: 95.0"), m)
    }

    @Test fun the_rest_fixtures_decode() {
        assertEquals(50, Fixtures.events.events.size)
        assertTrue(Fixtures.sources.sources.size >= 19)
        val g = SosJson.decodeFromString(GeocodeReply.serializer(), Fixtures.text("geocode.json"))
        assertEquals("Tokyo, Japan", g.results.first().name)
    }

    @Test fun emsc_and_usgs_times_both_parse() {
        // EMSC writes ISO with Z, the backend writes +00:00: both are instants, never "now"
        assertNotNull(Iso.ms("2026-10-26T17:44:08.220000Z"))
        assertNotNull(Iso.ms("2026-10-26T17:44:08.220000+00:00"))
        assertNull(Iso.ms("yesterday"))
    }
}

class FeedTest {
    private val t0 = Fixtures.snapshotTime

    @Test fun the_snapshot_is_sorted_by_EVENT_time_not_arrival() {
        val r = FeedReducer.reduce(FeedState(), Fixtures.snapshot, deviceNow = t0)
        val times = r.state.events.mapNotNull { it.timeMs }
        assertEquals(times.sortedDescending(), times)
        assertTrue(r.changes.all { it is FeedChange.Upserted && it.fromSnapshot })
    }

    @Test fun the_clock_skew_follows_the_server_not_the_phone() {
        // a phone 7 minutes slow: ages must still be the server's
        val phone = t0 - 7 * 60_000L
        val r = FeedReducer.reduce(FeedState(), Fixtures.snapshot, deviceNow = phone)
        assertEquals(t0, r.state.serverNow(phone))
    }

    @Test fun replaying_the_whole_capture_keeps_the_list_sorted_and_applies_purges() {
        var s = FeedState()
        var now = t0
        for (m in Fixtures.ws) {
            s = FeedReducer.reduce(s, m, now).state
            now += 200
            if (m is ServerMessage.Purge) m.ids.forEach { id -> assertNull(s.byId(id)) }
        }
        val times = s.events.mapNotNull { it.timeMs }
        assertEquals(times.sortedDescending(), times)
        assertEquals(s.events.size, s.events.map { it.id }.toSet().size)
    }

    @Test fun a_secondary_solution_is_credited_never_listed() {
        val base = FeedReducer.reduce(FeedState(), Fixtures.snapshot, t0).state
        val e = Fixtures.real().copy(id = "usgs:copy", source = "usgs", clusterId = "cluster-x")
        val r = FeedReducer.reduce(base, ServerMessage.Upsert(e, isNew = true, primary = false, breaking = true), t0)
        assertNull(r.state.byId("usgs:copy"))
        assertTrue(r.changes.isEmpty())
        assertTrue("usgs" in r.state.clusterSources["cluster-x"].orEmpty())
    }

    @Test fun an_older_revision_arriving_late_is_ignored() {
        val e = Fixtures.real()
        val s = FeedReducer.reduce(FeedState(), ServerMessage.Upsert(e.copy(revision = 3), true, true, false), t0).state
        val r = FeedReducer.reduce(s, ServerMessage.Upsert(e.copy(revision = 2, magnitude = 9.9), false, true, false), t0)
        assertEquals(3, r.state.byId(e.id)!!.revision)
        assertTrue(r.changes.isEmpty())
    }

    @Test fun a_cap_all_clear_removes_the_warning() {
        val e = Fixtures.real("storm") { it.kind == "storm" }
        val s = FeedReducer.reduce(FeedState(), ServerMessage.Upsert(e, true, true, false), t0).state
        val r = FeedReducer.reduce(s, ServerMessage.Upsert(e.copy(responseType = "AllClear", revision = e.revision + 1), false, true, false), t0)
        assertNull(r.state.byId(e.id))
        assertEquals(listOf(FeedChange.Removed(e.id, "lifted")), r.changes)
    }

    @Test fun the_count_cut_never_drops_a_running_alert() {
        // 75 of the 300 real snapshot events are ongoing, many issued days ago: cut to 100, all survive
        val now = t0
        val sorted = Fixtures.snapshot.events.sortedWith(FeedReducer.byEventTime)
        val running = sorted.filter { it.isCurrentAlert(now) }
        assertTrue("the capture has running alerts", running.size > 20)
        val cut = FeedReducer.trim(sorted, now, max = 100)
        assertTrue(cut.containsAll(running))
        assertEquals(100, cut.size)
        // what was cut is the OLDEST point events, never a running one
        val dropped = sorted - cut.toSet()
        assertTrue(dropped.none { it.isCurrentAlert(now) })
    }

    @Test fun a_poll_never_makes_the_socket_look_alive() {
        val s = FeedState(lastMessageAt = 1234L)
        val r = FeedReducer.fromPoll(s, Fixtures.events.events, emptyList(), t0, full = true, deviceNow = t0)
        assertEquals(1234L, r.state.lastMessageAt)
        assertFalse(r.state.fromCache)
    }

    @Test fun a_zoned_poll_merges_and_never_shrinks_the_world_feed() {
        val world = FeedReducer.reduce(FeedState(), Fixtures.snapshot, t0).state
        val one = Fixtures.events.events.take(3)
        val r = FeedReducer.fromPoll(world, one, removed = emptyList(), serverTimeMs = t0, full = false, deviceNow = t0)
        assertTrue(r.state.events.size >= world.events.size)
    }
}
