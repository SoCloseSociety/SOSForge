package co.soclose.sosforge

import co.soclose.sosforge.core.alert.AlertContext
import co.soclose.sosforge.core.alert.AlertPrefs
import co.soclose.sosforge.core.alert.Effect
import co.soclose.sosforge.core.alert.Ledger
import co.soclose.sosforge.core.feed.FeedReducer
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.live.Protocol
import co.soclose.sosforge.core.live.ServerMessage
import co.soclose.sosforge.core.net.SosJson
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * `snapshot_chunked` (coordinator, 2026-10-26: lanes B and D implementing it): a first `snapshot` with
 * `"more": N`, then `snapshot_more` chunks until `remaining` is 0. No server sends it yet, so the frames
 * here are the VERBATIM zoned capture's snapshot split in three, with only the documented keys added.
 */
class ChunkedSnapshotTest {
    private val root = SosJson.parseToJsonElement(Fixtures.text("v2-ws-zoned.ndjson").lines().first()).jsonObject
    private val all = root["events"]!!.jsonArray
    private val third = all.size / 3

    private fun first(): String = JsonObject(root + mapOf("events" to JsonArray(all.take(third)), "more" to JsonPrimitive(2))).toString()
    private fun chunk(from: Int, to: Int, remaining: Int): String = JsonObject(
        mapOf("type" to JsonPrimitive("snapshot_more"), "events" to JsonArray(all.subList(from, to)), "remaining" to JsonPrimitive(remaining), "server_time" to root["server_time"]!!),
    ).toString()

    private val frames get() = listOf(first(), chunk(third, 2 * third, 1), chunk(2 * third, all.size, 0)).map { Protocol.parse(it)!! }

    @Test fun the_chunks_parse_and_add_up_to_the_whole_snapshot() {
        val f = frames
        assertEquals(2, (f[0] as ServerMessage.Snapshot).more)
        assertEquals(1, (f[1] as ServerMessage.SnapshotMore).remaining)
        assertEquals(0, (f[2] as ServerMessage.SnapshotMore).remaining)
        var s = FeedState()
        val t = Fixtures.snapshotTime
        val remaining = mutableListOf<Int>()
        for (m in f) {
            s = FeedReducer.reduce(s, m, t).state
            remaining += s.snapshotRemaining
        }
        assertEquals(listOf(2, 1, 0), remaining)
        assertEquals(all.size, s.events.size)
        val times = s.events.mapNotNull { it.timeMs }
        assertEquals(times.sortedDescending(), times)
    }

    @Test fun on_first_launch_every_chunk_is_history_and_history_ends_with_the_last() {
        val athens = Place("ath", "Athens", 37.98, 23.73, 300, countryCode = "GR")
        val now = Instant.parse(root["server_time"].toString().trim('"')).toEpochMilli()
        val l = Ledger()
        var s = FeedState()
        var rings = 0
        for (m in frames) {
            val r = FeedReducer.reduce(s, m, now)
            s = r.state
            rings += l.onChanges(r.changes, AlertContext(listOf(athens), AlertPrefs(), now, now, false), fullRead = true, complete = s.snapshotRemaining == 0)
                .count { it is Effect.Show }
            if (s.snapshotRemaining > 0) assertTrue("history must not end mid-snapshot", !l.state.historyDone)
        }
        assertEquals(0, rings)
        assertTrue(l.state.historyDone)
    }
}
