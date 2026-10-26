package co.soclose.sosforge.core.net

import co.soclose.sosforge.core.alert.AlertPrefs
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.ApiVersion.Companion.EVENTS_ETAG
import kotlinx.serialization.json.JsonElement
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Why a read failed, in the words the screen needs. A network error says NOTHING about the server:
 * it is "unreachable, retrying", never "the server is down".
 */
sealed interface ApiError {
    data object Unreachable : ApiError
    data class Refused(val code: Int, val detail: String? = null) : ApiError
    data object Unreadable : ApiError
}

class ApiException(val error: ApiError, cause: Throwable? = null) : IOException(error.toString(), cause)

/**
 * One poll of `/api/events`: a body, or "not modified" (ETag), with the ETag to send next time, and
 * [arrivedAt], the device time its headers arrived (the server stamped `server_time` before sending
 * them; reading the time after the download and the decode would make the server look late).
 */
data class EventsRead(val reply: EventsReply?, val etag: String?, val notModified: Boolean, val arrivedAt: Long = 0L)

/**
 * The REST half of the contract (backend/app/main.py). Blocking calls: run them on Dispatchers.IO.
 * The scope query is built here, ONE place, so the socket and the poll can never disagree about
 * what "near me" means (lane B uses the same parser for both).
 */
class SosApi(val origin: String, private val http: OkHttpClient) {

    private fun url(path: String): HttpUrl = (origin.trimEnd('/') + path).toHttpUrl()

    /** Device time the last response's headers arrived (per call site, read right after [get]). */
    @Volatile private var headersAt: Long = 0L

    private fun get(u: HttpUrl, etag: String? = null): Triple<Int, String?, String?> {
        val req = Request.Builder().url(u).header("Accept", "application/json").apply {
            if (etag != null) header("If-None-Match", etag)
        }.build()
        try {
            http.newCall(req).execute().use { r ->
                headersAt = System.currentTimeMillis()
                return Triple(r.code, if (r.code == 304) null else r.body?.string(), r.header("ETag"))
            }
        } catch (e: IOException) {
            throw ApiException(ApiError.Unreachable, e)
        }
    }

    private inline fun <reified T> read(u: HttpUrl): T {
        val (code, body, _) = get(u)
        if (code !in 200..299 || body == null) throw ApiException(ApiError.Refused(code, body?.take(200)))
        return runCatching { SosJson.decodeFromString<T>(body) }.getOrElse { throw ApiException(ApiError.Unreadable, it) }
    }

    /** What the server offers. A 404 is the legacy server (production before lane B): [ApiVersion.NONE]. */
    fun version(): ApiVersion {
        val (code, body, _) = get(url("/api/version"))
        if (code == 404) return ApiVersion.NONE
        if (code !in 200..299 || body == null) throw ApiException(ApiError.Refused(code))
        return runCatching { SosJson.decodeFromString<ApiVersion>(body) }.getOrElse { ApiVersion.NONE }
    }

    fun sources(): SourcesReply = read(url("/api/sources"))

    fun geocode(q: String): GeocodeReply = read(url("/api/geocode").newBuilder().addQueryParameter("q", q).build())

    /** One event, or null when the server no longer knows it (404: purged). */
    fun event(id: String): SosEvent? {
        val u = url("/api/events").newBuilder().addPathSegments(id.trimStart('/')).build()
        val (code, body, _) = get(u)
        if (code == 404) return null
        if (code !in 200..299 || body == null) throw ApiException(ApiError.Refused(code))
        val el = runCatching { SosJson.parseToJsonElement(body) }.getOrElse { throw ApiException(ApiError.Unreadable, it) }
        val ev = (el as? kotlinx.serialization.json.JsonObject)?.get("event") ?: throw ApiException(ApiError.Unreadable)
        return runCatching { SosJson.decodeFromJsonElement(SosEvent.serializer(), ev) }.getOrElse { throw ApiException(ApiError.Unreadable, it) }
    }

    /**
     * `GET /api/events`. With [scope] and a server that offers zones, the server filters; with [since]
     * (empty = "everything, and mint me a cursor") and `events_cursor`, it returns only what changed.
     * Either way the CLIENT filters again: the server is never trusted to be the only gate.
     * A 400 `bad_since` (a cursor the server no longer reads) is [ApiError.Refused] with code 400.
     */
    fun events(version: ApiVersion, scope: Scope?, since: String?, etag: String?, limit: Int = 300): EventsRead {
        val b = url("/api/events").newBuilder().addQueryParameter("limit", limit.toString())
        if (scope != null && version.has(ApiVersion.EVENTS_ZONE)) scope.addTo(b)
        if (since != null && version.has(ApiVersion.EVENTS_CURSOR)) b.addQueryParameter("since", since)
        val (code, body, newTag) = get(b.build(), etag.takeIf { version.has(EVENTS_ETAG) })
        val arrived = headersAt
        if (code == 304) return EventsRead(null, newTag ?: etag, notModified = true, arrivedAt = arrived)
        if (code !in 200..299 || body == null) throw ApiException(ApiError.Refused(code, body?.take(200)))
        val reply = runCatching { SosJson.decodeFromString<EventsReply>(body) }.getOrElse { throw ApiException(ApiError.Unreadable, it) }
        return EventsRead(reply, newTag, notModified = false, arrivedAt = arrived)
    }

    /** The per-kind before / during / after guidance, or null when this server has none (404). */
    fun guidance(lang: String): Guidance? {
        val (code, body, _) = get(url("/api/guidance").newBuilder().addQueryParameter("lang", lang).build())
        if (code == 404 || code == 422) return null
        if (code !in 200..299 || body == null) throw ApiException(ApiError.Refused(code))
        val el: JsonElement = runCatching { SosJson.parseToJsonElement(body) }.getOrElse { throw ApiException(ApiError.Unreadable, it) }
        return GuidanceReader.read(el, lang)
    }

    companion object {
        fun client(userAgent: String): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            // GDACS-sized replies on a bad mobile link: a wide read timeout, the watchdog guards the socket
            .readTimeout(30, TimeUnit.SECONDS)
            .addInterceptor { chain -> chain.proceed(chain.request().newBuilder().header("User-Agent", userAgent).build()) }
            .build()
    }
}

/**
 * What one client wants to hear about (backend/app/scope.py, integration branch): zones
 * `lat,lon,radius_km;...` (at most 10, 1..2000 km), a severity floor, kinds, and `unplaced` = the
 * ISO alpha-2 countries whose POSITIONLESS events we still want (at most 10, only with zones).
 * [minSeverity] / [kinds] null = everything (the foreground: the screens show the whole zone, only the
 * notifications use the owner's floor). Built from the owner's places and prefs; pure, unit-tested.
 */
data class Scope(
    val zones: List<Triple<Double, Double, Int>>,
    val minSeverity: String?,
    val kinds: List<String>?,
    val unplaced: List<String> = emptyList(),
) {
    val zonesParam: String get() = zones.joinToString(";") { (la, lo, r) -> "${fmt(la)},${fmt(lo)},$r" }
    val kindsParam: String get() = kinds.orEmpty().sorted().joinToString(",")

    fun addTo(b: HttpUrl.Builder) {
        if (zones.isNotEmpty()) b.addQueryParameter("zones", zonesParam)
        if (minSeverity != null) b.addQueryParameter("min_severity", minSeverity)
        if (!kinds.isNullOrEmpty()) b.addQueryParameter("kinds", kindsParam)
        if (zones.isNotEmpty() && unplaced.isNotEmpty()) b.addQueryParameter("unplaced", unplaced.joinToString(","))
    }

    /** After a server refusal (`error` then 1008): the zones alone, the one parameter we cannot drop. */
    fun zonesOnly(): Scope = Scope(zones, null, null, emptyList())

    /** The websocket query string (`zones=...&min_severity=...&kinds=...`), URL-encoded. */
    fun wsQuery(): String {
        val b = "https://x/".toHttpUrl().newBuilder()
        addTo(b)
        return b.build().encodedQuery.orEmpty()
    }

    companion object {
        private fun fmt(v: Double) = String.format(Locale.US, "%.4f", v)

        /**
         * The scope of the owner's alerts. Kinds the server knows only (an unknown kind is a 400 there,
         * lesson: a refused parameter is never silently ignored, so we never send one). A tsunami can
         * arrive as an earthquake event with `tsunami: true`: when tsunamis are watched, earthquakes are
         * asked for too, and the client keeps only what [co.soclose.sosforge.core.alert.AlertRules] keeps.
         */
        fun of(places: List<Place>, prefs: AlertPrefs, everything: Boolean = false): Scope? {
            if (places.isEmpty()) return null
            val tsunami = everything || Kind.TSUNAMI.key in prefs.kinds
            val kinds = prefs.kinds.filter { k -> Kind.entries.any { it.key == k } }.toMutableSet()
            if (tsunami) kinds += Kind.EARTHQUAKE.key
            val countries = places.filter { it.countryWide }.mapNotNull { it.countryCode?.uppercase()?.takeIf { c -> Regex("^[A-Z]{2}$").matches(c) } }
                .distinct().take(10)
            return Scope(
                zones = places.take(Place.MAX_PLACES).map { Triple(it.lat, it.lon, Geo.scopeRadiusKm(it, tsunami)) },
                minSeverity = if (everything) null else prefs.floor.key,
                kinds = if (everything) null else kinds.toList(),
                unplaced = countries,
            )
        }
    }
}
