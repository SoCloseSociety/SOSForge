package co.soclose.sosforge.core.live

import co.soclose.sosforge.core.net.Iso
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.core.net.SourceHealth
import co.soclose.sosforge.core.net.Stats
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.intOrNull

/**
 * What the websocket says (frontend/src/types.ts ServerMessage, backend/app/main.py + hub.py), parsed
 * from the RAW text. Pure, unit-tested against a verbatim capture of wss://sosforge.soclose.co/ws.
 *
 * One unreadable event never takes its snapshot down: it is skipped and counted ([Snapshot.skipped]).
 */
sealed interface ServerMessage {
    data class Snapshot(
        val serverTimeMs: Long?,
        val events: List<SosEvent>,
        val stats: Stats?,
        val sources: List<SourceHealth>,
        val skipped: Int,
        /** A zoned snapshot: what it could not place by distance (null = not said: a plain socket). */
        val unplaced: co.soclose.sosforge.core.net.Unplaced?,
        /** `snapshot_chunked`: how many `snapshot_more` chunks follow (0 / absent = complete). */
        val more: Int = 0,
    ) : ServerMessage

    /** One chunk of a split snapshot (slow links): its events count as snapshot events. */
    data class SnapshotMore(val serverTimeMs: Long?, val events: List<SosEvent>, val remaining: Int, val skipped: Int) : ServerMessage

    /**
     * `event` (new to the server) or `update` (a revision). [escalated] (lane F): the severity rose, or
     * the tsunami flag turned on, inside the breaking window -- news even though the id is not new.
     */
    data class Upsert(
        val event: SosEvent,
        val isNew: Boolean,
        val primary: Boolean,
        val breaking: Boolean,
        val escalated: Boolean = false,
    ) : ServerMessage

    /** The server removed events: stale, cancelled (an EEW withdrawn), lifted, or out_of_scope (lane B). */
    data class Purge(val ids: List<String>, val reason: String) : ServerMessage

    data class Tick(val serverTimeMs: Long?, val stats: Stats?, val sources: List<SourceHealth>, val clients: Int?) : ServerMessage

    /** lane B: a refused scope, sent before the server closes with 1008. */
    data class Refused(val code: String, val reason: String) : ServerMessage

    data class Unknown(val type: String?) : ServerMessage
}

object Protocol {
    fun parse(text: String): ServerMessage? {
        val root = runCatching { SosJson.parseToJsonElement(text) }.getOrNull() as? JsonObject ?: return null
        return parse(root)
    }

    fun parse(root: JsonObject): ServerMessage {
        val type = root.str("type")
        return when (type) {
            "snapshot" -> {
                val arr = root["events"] as? JsonArray ?: JsonArray(emptyList())
                var skipped = 0
                val events = arr.mapNotNull { el ->
                    event(el).also { if (it == null) skipped++ }
                }
                ServerMessage.Snapshot(
                    serverTimeMs = Iso.ms(root.str("server_time")),
                    events = events,
                    stats = root["stats"]?.let { decode<Stats>(it) },
                    sources = sources(root["sources"]),
                    skipped = skipped,
                    unplaced = co.soclose.sosforge.core.net.Unplaced.of(root["unplaced"]),
                    more = (root["more"] as? JsonPrimitive)?.intOrNull ?: 0,
                )
            }
            "snapshot_more" -> {
                val arr = root["events"] as? JsonArray ?: JsonArray(emptyList())
                var skipped = 0
                val events = arr.mapNotNull { el -> event(el).also { if (it == null) skipped++ } }
                ServerMessage.SnapshotMore(
                    serverTimeMs = Iso.ms(root.str("server_time")),
                    events = events,
                    remaining = (root["remaining"] as? JsonPrimitive)?.intOrNull ?: 0,
                    skipped = skipped,
                )
            }
            "event", "update" -> {
                val e = root["event"]?.let { event(it) }
                if (e == null) ServerMessage.Unknown(type)
                else ServerMessage.Upsert(
                    event = e,
                    isNew = type == "event",
                    // the server always says; a missing flag is read as primary (the web's `=== false`)
                    primary = (root["primary"] as? JsonPrimitive)?.booleanOrNull != false,
                    breaking = (root["breaking"] as? JsonPrimitive)?.booleanOrNull == true,
                    escalated = (root["escalated"] as? JsonPrimitive)?.booleanOrNull == true,
                )
            }
            "purge" -> ServerMessage.Purge(
                ids = (root["ids"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull } ?: emptyList(),
                reason = root.str("reason") ?: "stale",
            )
            "tick" -> ServerMessage.Tick(
                serverTimeMs = Iso.ms(root.str("server_time")),
                stats = root["stats"]?.let { decode<Stats>(it) },
                sources = sources(root["sources"]),
                clients = (root["clients"] as? JsonPrimitive)?.intOrNull,
            )
            "error" -> ServerMessage.Refused(root.str("code") ?: "error", root.str("reason") ?: root.str("detail") ?: "")
            else -> ServerMessage.Unknown(type)
        }
    }

    private fun event(el: JsonElement): SosEvent? = decode<SosEvent>(el)

    private fun sources(el: JsonElement?): List<SourceHealth> =
        (el as? JsonArray)?.mapNotNull { decode<SourceHealth>(it) } ?: emptyList()

    private inline fun <reified T> decode(el: JsonElement): T? =
        runCatching { SosJson.decodeFromJsonElement<T>(el) }.getOrNull()

    private fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
}
