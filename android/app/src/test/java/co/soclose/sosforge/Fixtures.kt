package co.soclose.sosforge

import co.soclose.sosforge.core.live.Protocol
import co.soclose.sosforge.core.live.ServerMessage
import co.soclose.sosforge.core.net.EventsReply
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.core.net.SourcesReply

/**
 * The fixtures are VERBATIM captures of production (lesson 8), taken 2026-10-26 18:00 UTC:
 *   events.json   = GET https://sosforge.soclose.co/api/events?limit=50
 *   sources.json  = GET https://sosforge.soclose.co/api/sources
 *   ws.ndjson     = the first seconds of wss://sosforge.soclose.co/ws, one message per line
 *   geocode.json  = GET https://sosforge.soclose.co/api/geocode?q=Tokyo
 *
 * The v2 contract (integration branch feat/sosforge-upgrade-2026-10, not deployed yet), captured
 * verbatim from that API run locally on :8360, 2026-10-26 20:13 UTC (tools: a capture script outside the repo):
 *   v2-version.json         = GET /api/version
 *   v2-events-full.json     = GET /api/events?zones=37.98,23.73,300;34.05,-118.24,300&unplaced=GR&since=&limit=300
 *   v2-events-delta.json    = the same with since=<its cursor>, 20 s later (an empty delta)
 *   v2-events-bad-since.json= GET /api/events?since=garbage (HTTP 400)
 *   v2-ws-zoned.ndjson      = 6 s of /ws?zones=...&unplaced=GR
 *   v2-ws-error.ndjson      = /ws?zones=95,0,100; its LAST line {"_closed":1008,...} is the close frame
 *                             written by the capture script, not a server message
 *   guidance-en.json, guidance-fr.json = GET /api/guidance?lang=en|fr
 * Tests that need a shape production does not serve yet (lane B's v2) say so where they build it.
 */
object Fixtures {
    fun text(name: String): String =
        requireNotNull(javaClass.classLoader?.getResource("fixtures/$name")) { "missing fixtures/$name" }.readText()

    val wsLines: List<String> by lazy { text("ws.ndjson").lines().filter { it.isNotBlank() } }
    val ws: List<ServerMessage> by lazy { wsLines.map { requireNotNull(Protocol.parse(it)) { "unparsed: ${it.take(80)}" } } }
    val snapshot: ServerMessage.Snapshot by lazy { ws.first() as ServerMessage.Snapshot }
    val events: EventsReply by lazy { SosJson.decodeFromString(EventsReply.serializer(), text("events.json")) }
    val sources: SourcesReply by lazy { SosJson.decodeFromString(SourcesReply.serializer(), text("sources.json")) }

    /** The server clock at the snapshot (all "now"s of the replay tests). */
    val snapshotTime: Long get() = snapshot.serverTimeMs!!

    /** A copy of a real event moved / re-ranked: the shape stays the server's, only the values change. */
    fun real(kind: String = "earthquake", pick: (SosEvent) -> Boolean = { it.kind == kind && it.hasPosition }): SosEvent =
        snapshot.events.first(pick)
}
