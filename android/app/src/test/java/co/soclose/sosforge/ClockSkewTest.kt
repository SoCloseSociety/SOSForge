package co.soclose.sosforge

import co.soclose.sosforge.core.feed.FeedReducer
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.live.ServerMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The server clock on a slow link (lane D's finding on the web, 2026-10-26, fixed there in b865340).
 * A message that waited in the socket (ticks queued behind a snapshot, a backlog draining) ARRIVES late,
 * so `server_time - arrival` reads LOW: every "N s ago" came out up to 20 s too young on Slow 3G.
 * A delay can only make the server look behind, never ahead: the offset is the MAXIMUM seen over a
 * 60 s window (by time, not by count), reset on a new connection.
 *
 * Frames: the verbatim capture's snapshot and tick; only their arrival times are chosen, to model the
 * link. The device clock here equals the server's: the true offset is 0.
 */
class ClockSkewTest {
    private val snap = Fixtures.snapshot
    private val tick = Fixtures.ws.filterIsInstance<ServerMessage.Tick>().first()
    private val s0 = snap.serverTimeMs!!

    private fun tickAt(serverMs: Long) = tick.copy(serverTimeMs = serverMs)

    /** Feeds (message, arrival) pairs and returns the states after each. */
    private fun run(start: FeedState, vararg frames: Pair<ServerMessage, Long>): List<FeedState> {
        var s = start
        return frames.map { (m, at) -> FeedReducer.reduce(s, m, at).state.also { s = it } }
    }

    @Test fun a_backlog_never_makes_ages_too_young_once_one_prompt_message_came() {
        val states = run(
            FeedState(),
            // Slow 3G: the snapshot and the ticks queued behind it all arrive 20 s late, in a burst
            snap to s0 + 20_000,
            tickAt(s0 + 1_000) to s0 + 20_010,
            tickAt(s0 + 2_000) to s0 + 20_020,
            // the link catches up: one tick arrives on time
            tickAt(s0 + 21_000) to s0 + 21_040,
            // then a second backlog: five ticks drain together 6 s late
            tickAt(s0 + 22_000) to s0 + 28_000,
            tickAt(s0 + 23_000) to s0 + 28_010,
            tickAt(s0 + 24_000) to s0 + 28_020,
        )
        val last = states.last()
        // what the screen computes "now" to be on the server, at the moment of the last arrival
        val err = last.serverNow(s0 + 28_020) - (s0 + 28_020)
        assertTrue("server clock read ${err} ms off after a backlog (ages too young by ${-err / 1000} s)", kotlin.math.abs(err) <= 100)
    }

    @Test fun a_delay_can_only_make_the_server_look_behind_never_ahead() {
        // before any prompt message, the estimate is the least-delayed one, and never ahead of the truth
        val states = run(FeedState(), snap to s0 + 20_000, tickAt(s0 + 1_000) to s0 + 20_010, tickAt(s0 + 19_000) to s0 + 20_020)
        states.forEach { assertTrue("skew ${it.clockSkewMs} is ahead of the server", it.clockSkewMs <= 0) }
        assertEquals(-1_020L, states.last().clockSkewMs)
    }

    @Test fun the_window_is_time_based_and_forgets_after_60_s() {
        // a reading older than 60 s no longer holds the estimate up
        val states = run(
            FeedState(),
            snap to s0,
            tickAt(s0 + 61_000) to s0 + 61_000 + 3_000, // 3 s late, 61 s after the good reading
        )
        assertEquals(-3_000L, states.last().clockSkewMs)
    }

    @Test fun a_new_connection_starts_a_new_window() {
        // the phone's clock was changed by 5 min between two connections: the old maximum must not survive
        val first = run(FeedState(), snap to s0, tickAt(s0 + 1_000) to s0 + 1_000).last()
        val after = run(first, snap.copy(serverTimeMs = s0 + 10_000) to s0 + 10_000 + 300_000).last()
        assertEquals(-300_000L, after.clockSkewMs)
    }

    @Test fun a_poll_feeds_the_same_window() {
        // a REST body read after a slow download reads low too: the window keeps the better reading
        val live = run(FeedState(), snap to s0, tickAt(s0 + 1_000) to s0 + 1_000).last()
        val r = FeedReducer.fromPoll(live, emptyList(), emptyList(), serverTimeMs = s0 + 5_000, full = false, deviceNow = s0 + 12_000)
        assertEquals(0L, r.state.clockSkewMs)
    }
}
