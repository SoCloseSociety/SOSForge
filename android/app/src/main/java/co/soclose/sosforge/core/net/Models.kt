package co.soclose.sosforge.core.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/**
 * The hazard kinds of the backend (backend/app/models/event.py `Kind`), plus OTHER for a value this
 * build does not know yet: a new kind must land in the feed as "other", never as a crash.
 */
enum class Kind(val key: String) {
    EARTHQUAKE("earthquake"), TSUNAMI("tsunami"), VOLCANO("volcano"), CYCLONE("cyclone"), FLOOD("flood"),
    WILDFIRE("wildfire"), STORM("storm"), HEAT("heat"), DROUGHT("drought"), LANDSLIDE("landslide"),
    AVALANCHE("avalanche"), SPACE_WEATHER("space_weather"), OTHER("other");

    companion object {
        fun of(key: String?): Kind = entries.firstOrNull { it.key == key } ?: OTHER
    }
}

/** The reserved status scale (frontend/src/styles.css --sev-*). Ranked: info 0 ... extreme 4. */
enum class Severity(val key: String, val rank: Int) {
    INFO("info", 0), MINOR("minor", 1), MODERATE("moderate", 2), SEVERE("severe", 3), EXTREME("extreme", 4);

    companion object {
        fun of(key: String?): Severity = entries.firstOrNull { it.key == key } ?: INFO
    }
}

/**
 * One normalised event, exactly as `event.public()` serialises it (frontend/src/types.ts SosEvent).
 * Every field the server may omit or add later is optional: the fixtures are verbatim captures, and a
 * field unknown to this build (`last_seen` is not in types.ts) is simply ignored.
 */
@Serializable
data class SosEvent(
    val id: String,
    val source: String = "",
    @SerialName("source_id") val sourceId: String = "",
    val kind: String = "other",
    val time: String,
    @SerialName("received_at") val receivedAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    val lat: Double? = null,
    val lon: Double? = null,
    @SerialName("depth_km") val depthKm: Double? = null,
    val magnitude: Double? = null,
    @SerialName("mag_type") val magType: String? = null,
    val place: String = "",
    val region: String? = null,
    val country: String? = null,
    @SerialName("country_code") val countryCode: String? = null,
    val severity: String = "info",
    val ongoing: Boolean = false,
    val preliminary: Boolean = false,
    @SerialName("intensity_mmi") val intensityMmi: Double? = null,
    @SerialName("felt_reports") val feltReports: Int? = null,
    val instruction: String? = null,
    val description: String? = null,
    @SerialName("response_type") val responseType: String? = null,
    @SerialName("intensity_cdi") val intensityCdi: Double? = null,
    @SerialName("wave_eta") val waveEta: String? = null,
    @SerialName("wave_eta_site") val waveEtaSite: String? = null,
    @SerialName("wave_max_m") val waveMaxM: Double? = null,
    @SerialName("wave_max_site") val waveMaxSite: String? = null,
    val expires: String? = null,
    val tsunami: Boolean = false,
    val alert: String? = null,
    val title: String = "",
    val url: String? = null,
    @SerialName("forecast_track") val forecastTrack: JsonElement? = null,
    @SerialName("cluster_id") val clusterId: String? = null,
    val revision: Int = 0,
) {
    val kindOf: Kind get() = Kind.of(kind)
    val severityOf: Severity get() = Severity.of(severity)

    /** A position is only a position inside the globe (lesson 15: a wrong place is worse than none). */
    val hasPosition: Boolean
        get() = lat != null && lon != null && lat in -90.0..90.0 && lon in -180.0..180.0

    /** The event's own instant in epoch ms, or null when the stamp is unreadable (never "now"). */
    val timeMs: Long? get() = Iso.ms(time)
    val expiresMs: Long? get() = Iso.ms(expires)

    /** The key two solutions of ONE event share (BMKG and USGS for one quake): what is notified once. */
    val clusterKey: String get() = clusterId?.takeIf { it.isNotBlank() } ?: id

    /** A CAP "all clear": the warning is LIFTED, it must not stay on a lock screen. */
    val isAllClear: Boolean get() = responseType.equals("AllClear", ignoreCase = true)

    /** What a row shows as its name: the place, else the title, never an empty line. */
    val displayPlace: String get() = Entities.decode(place.ifBlank { title.ifBlank { id } })

    /** Upcoming: a warning published before its onset (lead time), not yet running. */
    fun isUpcoming(serverNow: Long): Boolean = (timeMs ?: Long.MIN_VALUE) > serverNow
}

@Serializable
data class Stats(
    @SerialName("total_buffered") val totalBuffered: Int = 0,
    @SerialName("last_hour") val lastHour: Int = 0,
    @SerialName("earthquakes_last_hour") val earthquakesLastHour: Int = 0,
    @SerialName("max_magnitude_last_hour") val maxMagnitudeLastHour: Double? = null,
    @SerialName("tsunami_active") val tsunamiActive: Int = 0,
    @SerialName("by_source") val bySource: Map<String, Int> = emptyMap(),
    @SerialName("server_time") val serverTime: String? = null,
)

/** One source as `/api/sources` and the tick describe it (`mode` only on the REST route). */
@Serializable
data class SourceHealth(
    val name: String,
    val connected: Boolean = false,
    @SerialName("last_ok") val lastOk: String? = null,
    @SerialName("last_error") val lastError: String? = null,
    // null when the server did not say: shown as "unknown", never as a guessed 0
    @SerialName("events_seen") val eventsSeen: Long? = null,
    val ingested: Long? = null,
    val errors: Long? = null,
    val mode: String? = null,
)

/**
 * `GET /api/events`. The legacy body is `count, server_time, events`. The v2 body (integration branch
 * feat/sosforge-upgrade-2026-10) adds `scope` / `unplaced` (a zoned read) and `cursor` / `reset` /
 * `reset_reason` / `removed` (an incremental read, `since=`). A v2 delta is never truncated: one too big
 * comes back as a full read with `reset: true`. Absent fields read as "not offered", never as an error.
 */
@Serializable
data class EventsReply(
    val count: Int = 0,
    @SerialName("server_time") val serverTime: String? = null,
    val events: List<SosEvent> = emptyList(),
    /** `{dropped, delivered}` on v2 (an object, not a count): kept raw, read by [Unplaced.of]. */
    val unplaced: JsonElement? = null,
    val cursor: String? = null,
    val removed: List<String> = emptyList(),
    val reset: Boolean = false,
    /** no_cursor | unknown_cursor | expired | too_many_changes */
    @SerialName("reset_reason") val resetReason: String? = null,
)

/** What a zoned read could not place by distance: `dropped` (not sent) and `delivered` (sent by country). */
data class Unplaced(val dropped: Int, val delivered: Int) {
    companion object {
        fun of(el: JsonElement?): Unplaced? = when (el) {
            is JsonObject -> Unplaced(
                (el["dropped"] as? JsonPrimitive)?.contentOrNull?.toIntOrNull() ?: 0,
                (el["delivered"] as? JsonPrimitive)?.contentOrNull?.toIntOrNull() ?: 0,
            )
            is JsonPrimitive -> el.contentOrNull?.toIntOrNull()?.let { Unplaced(it, 0) }
            else -> null
        }
    }
}

@Serializable
data class SourcesReply(
    @SerialName("server_time") val serverTime: String? = null,
    val sources: List<SourceHealth> = emptyList(),
)

@Serializable
data class GeoResult(
    val name: String,
    val lat: Double,
    val lon: Double,
    val type: String? = null,
    @SerialName("country_code") val countryCode: String? = null,
)

@Serializable
data class GeocodeReply(val query: String = "", val results: List<GeoResult> = emptyList())

/**
 * `GET /api/version` (lane B, backend/app/main.py): `{"api": 2, "features": [...], "app": "..."}`.
 * `api` is a NUMBER there, so it is read as a raw element. A 404 is the legacy server: [NONE].
 */
@Serializable
data class ApiVersion(val api: JsonElement? = null, val features: List<String> = emptyList()) {
    fun has(feature: String) = feature in features

    /**
     * A zoned socket (`/ws?zones=&min_severity=&kinds=&unplaced=`). On such a server the app ALWAYS
     * connects with zones: its unfiltered snapshot pins every running, severe+ or tsunami event
     * (about 880 KiB), too heavy for a phone to fetch at every reconnect.
     */
    val wsScope: Boolean get() = has(WS_ZONES) || has(WS_SCOPE_DRAFT)

    /** The poll fallback can ask "what changed since" with a zone, and get a 304 when nothing did. */
    val incremental: Boolean get() = has(EVENTS_CURSOR) && has(EVENTS_ZONE)

    companion object {
        const val WS_ZONES = "ws_zones"
        /** The name lane B's draft used, before the merge: still understood. */
        const val WS_SCOPE_DRAFT = "ws_scope"
        const val WS_OUT_OF_SCOPE = "ws_out_of_scope"
        const val WS_ERROR = "ws_error"
        const val SNAPSHOT_PINNED = "snapshot_pinned"
        const val EVENTS_ZONE = "events_zone"
        const val EVENTS_CURSOR = "events_cursor"
        const val EVENTS_ETAG = "events_etag"
        const val GUIDANCE = "guidance"
        const val ESCALATED = "escalated"
        const val SNAPSHOT_CHUNKED = "snapshot_chunked"
        val NONE = ApiVersion(null, emptyList())
    }
}

/** One line of what to do, with the official page it paraphrases. */
@Serializable
data class GuidanceLine(val text: String, val url: String? = null, val source: String? = null)

/** What to do before / during / after one kind of hazard (lane B `GET /api/guidance?lang=`). */
@Serializable
data class KindGuidance(
    val kind: String,
    val before: List<GuidanceLine> = emptyList(),
    val during: List<GuidanceLine> = emptyList(),
    val after: List<GuidanceLine> = emptyList(),
) {
    val isEmpty: Boolean get() = before.isEmpty() && during.isEmpty() && after.isEmpty()
}

@Serializable
data class Guidance(val lang: String = "", val kinds: List<KindGuidance> = emptyList()) {
    fun of(kind: String): KindGuidance? = kinds.firstOrNull { it.kind == kind && !it.isEmpty }
}

/**
 * The guidance catalogue, read TOLERANTLY: lane B's `app/guidance.py` was still being written when this
 * client was, so the reader accepts the shapes such a catalogue can reasonably take -- kinds as an
 * object keyed by kind or as a list with a `kind` field, phases as lists of strings or of objects whose
 * text is `text`/`line` and whose link is `url`/`source_url`/`href` (or a nested `source` object).
 * Anything it cannot read is skipped, never fatal. Pure, unit-tested.
 */
object GuidanceReader {
    fun read(root: JsonElement, lang: String): Guidance {
        val obj = root as? JsonObject ?: return Guidance(lang)
        val kindsEl = obj["kinds"] ?: obj["catalogue"] ?: obj["guidance"] ?: obj
        val kinds = mutableListOf<KindGuidance>()
        when (kindsEl) {
            is JsonObject -> kindsEl.forEach { (k, v) -> kindOf(k, v)?.let(kinds::add) }
            is JsonArray -> kindsEl.forEach { v ->
                val k = ((v as? JsonObject)?.get("kind") as? JsonPrimitive)?.contentOrNull
                if (k != null) kindOf(k, v)?.let(kinds::add)
            }
            else -> Unit
        }
        val l = (obj["lang"] as? JsonPrimitive)?.contentOrNull ?: lang
        return Guidance(l, kinds)
    }

    private fun kindOf(kind: String, el: JsonElement): KindGuidance? {
        val o = el as? JsonObject ?: return null
        val phases = (o["phases"] as? JsonObject) ?: o
        val g = KindGuidance(kind, lines(phases["before"]), lines(phases["during"]), lines(phases["after"]))
        return if (g.isEmpty) null else g
    }

    private fun lines(el: JsonElement?): List<GuidanceLine> {
        val arr = el as? JsonArray ?: return emptyList()
        return arr.mapNotNull { line(it) }
    }

    private fun str(o: JsonObject, vararg keys: String): String? =
        keys.firstNotNullOfOrNull { k -> (o[k] as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull?.takeIf { it.isNotBlank() } }

    private fun line(el: JsonElement): GuidanceLine? = when (el) {
        is JsonPrimitive -> el.contentOrNull?.takeIf { el.isString && it.isNotBlank() }?.let { GuidanceLine(it) }
        is JsonObject -> {
            val text = str(el, "text", "line", "title")
            val nested = el["source"] as? JsonObject
            val url = str(el, "url", "source_url", "href", "link") ?: nested?.let { str(it, "url", "href") }
            val source = str(el, "source_name", "publisher", "agency") ?: nested?.let { str(it, "name", "title") }
                ?: (el["source"] as? JsonPrimitive)?.contentOrNull?.takeIf { !it.startsWith("http") }
            val safeUrl = url?.takeIf { it.startsWith("https://") || it.startsWith("http://") }
            text?.let { GuidanceLine(it, safeUrl, source) }
        }
        else -> null
    }

    /** True when the element says `needs_review` (kept for the UI to say "translation pending"). */
    fun needsReview(el: JsonObject): Boolean = (el["needs_review"] as? JsonPrimitive)?.booleanOrNull == true
}

/**
 * Some feeds hand us HTML-escaped text (Meteoalarm: "East Sterea &amp; Evvoia"). Decoded for DISPLAY,
 * into a plain text node: nothing is ever rendered as HTML, so a decoded "<" stays a character.
 */
object Entities {
    private val NUM = Regex("&#(x[0-9a-fA-F]{1,6}|[0-9]{1,7});")
    fun decode(s: String?): String {
        if (s.isNullOrEmpty() || '&' !in s) return s.orEmpty()
        val named = s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#39;", "'").replace("&apos;", "'")
        val num = NUM.replace(named) { m ->
            val v = m.groupValues[1]
            val cp = runCatching { if (v.startsWith("x")) v.substring(1).toInt(16) else v.toInt() }.getOrNull()
            if (cp != null && Character.isValidCodePoint(cp) && cp != 0) String(Character.toChars(cp)) else m.value
        }
        // &amp; last, so "&amp;lt;" reads "&lt;" and is not decoded twice
        return num.replace("&amp;", "&")
    }
}

/** ISO-8601 parsing as the backend writes it: "2026-10-26T17:44:08.220000Z" or "+00:00". Pure. */
object Iso {
    fun ms(s: String?): Long? {
        if (s.isNullOrBlank()) return null
        return runCatching { java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli() }.getOrNull()
            ?: runCatching { java.time.Instant.parse(s).toEpochMilli() }.getOrNull()
            ?: runCatching {
                // a naive stamp: the backend only writes UTC, so read it as UTC (never as local time)
                java.time.LocalDateTime.parse(s).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
            }.getOrNull()
    }
}
