package co.soclose.sosforge

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import co.soclose.sosforge.core.Prefs
import co.soclose.sosforge.core.alert.AlertEngine
import co.soclose.sosforge.core.alert.Notifier
import co.soclose.sosforge.core.feed.FeedChange
import co.soclose.sosforge.core.feed.FeedReducer
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.live.Link
import co.soclose.sosforge.core.live.LinkRules
import co.soclose.sosforge.core.live.LiveClient
import co.soclose.sosforge.core.live.ServerMessage
import co.soclose.sosforge.core.net.ApiError
import co.soclose.sosforge.core.net.ApiException
import co.soclose.sosforge.core.net.ApiVersion
import co.soclose.sosforge.core.net.Guidance
import co.soclose.sosforge.core.net.Scope
import co.soclose.sosforge.core.net.ServerAddress
import co.soclose.sosforge.core.net.SosApi
import co.soclose.sosforge.core.net.SosEvent
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.core.net.SourceHealth
import co.soclose.sosforge.core.watch.Watch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.OkHttpClient
import java.io.File

/**
 * Which socket to open (pure, LiveUrlTest):
 *  - a zoned server (`ws_zones`) and at least one place: ALWAYS zoned. In the foreground the zones
 *    carry everything (the screens show the whole zone); while only the Watch runs they also carry the
 *    owner's floor and kinds. After a refusal, the zones alone;
 *  - a zoned server and no place: no socket (its unfiltered snapshot is ~880 KiB); the world list is read
 *    over REST, with its age;
 *  - a legacy server (404 on /api/version, production today): the plain socket, filtered on the phone;
 *  - not known yet: null, "not now".
 * `chunks=1` (a split snapshot for slow links) is OPT-IN: sent only when the server lists
 * `snapshot_chunked`, never on the legacy path, never after a refusal (it could be what was refused).
 */
object LiveUrl {
    enum class Mode { ZONED, PLAIN, NONE, UNKNOWN }

    /**
     * The zones of the socket: the owner's places, plus where the person is right now while the app
     * is on screen (the Live "near me" view). The Watch, alone, never carries "here": the alerts are
     * the places the owner chose, and the position is never read in the background.
     */
    fun zonePlaces(places: List<co.soclose.sosforge.core.geo.Place>, here: co.soclose.sosforge.core.Here?): List<co.soclose.sosforge.core.geo.Place> =
        if (here == null) places else places + co.soclose.sosforge.core.geo.Place(HERE_ID, "", here.lat, here.lon, co.soclose.sosforge.core.geo.Place.DEFAULT_RADIUS_KM, "here", countryCode = here.countryCode)

    const val HERE_ID = "__here__"

    fun mode(v: ApiVersion?, hasPlaces: Boolean): Mode = when {
        v == null -> Mode.UNKNOWN
        !v.wsScope -> Mode.PLAIN
        hasPlaces -> Mode.ZONED
        else -> Mode.NONE
    }

    fun url(origin: String, v: ApiVersion?, places: List<co.soclose.sosforge.core.geo.Place>, prefs: co.soclose.sosforge.core.alert.AlertPrefs, foreground: Boolean, refused: Boolean): String? =
        when (mode(v, places.isNotEmpty())) {
            Mode.UNKNOWN, Mode.NONE -> null
            Mode.PLAIN -> ServerAddress.wsUrl(origin)
            Mode.ZONED -> {
                val s = Scope.of(places, prefs, everything = foreground)!!
                val chunks = !refused && v?.has(ApiVersion.SNAPSHOT_CHUNKED) == true
                ServerAddress.wsUrl(origin, (if (refused) s.zonesOnly() else s).wsQuery() + if (chunks) "&chunks=1" else "")
            }
        }
}

/** The disk copy of the feed: what a cold start on a dead link shows, with its age, never as LIVE. */
@Serializable
private data class FeedCache(
    val savedAt: Long,
    val skew: Long,
    val serverTime: Long? = null,
    val events: List<SosEvent> = emptyList(),
    val sources: List<SourceHealth> = emptyList(),
)

/**
 * The one process-wide owner of the feed, the socket, the alert engine and the owner's choices. The
 * activity and the Watch service both read from here, so there is ONE socket whoever keeps the process
 * alive: unscoped (the world feed) while the app is on screen, scoped to the owner's places (lane B,
 * when offered) while only the Watch runs, closed otherwise.
 */
class SosApp : Application() {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    lateinit var prefs: Prefs private set
    lateinit var http: OkHttpClient private set
    lateinit var notifier: Notifier private set
    lateinit var alerts: AlertEngine private set
    lateinit var live: LiveClient private set
    lateinit var watch: Watch private set
    lateinit var offline: co.soclose.sosforge.core.map.OfflineMaps private set

    /** The latest.json verdict (Settings, and the quiet card on Live). */
    private val _update = MutableStateFlow<co.soclose.sosforge.core.update.UpdateState>(co.soclose.sosforge.core.update.UpdateState.NotChecked)
    val update: StateFlow<co.soclose.sosforge.core.update.UpdateState> = _update.asStateFlow()

    /** The active network does not count bytes (Wi-Fi, Ethernet): offline map packs may download. */
    private val _unmetered = MutableStateFlow(false)
    val unmetered: StateFlow<Boolean> = _unmetered.asStateFlow()

    private val _feed = MutableStateFlow(FeedState())
    val feed: StateFlow<FeedState> = _feed.asStateFlow()
    private val feedLock = Mutex()

    /** null = not read yet for this server. */
    private val _version = MutableStateFlow<ApiVersion?>(null)
    val version: StateFlow<ApiVersion?> = _version.asStateFlow()

    private val _guidance = MutableStateFlow<Guidance?>(null)
    val guidance: StateFlow<Guidance?> = _guidance.asStateFlow()

    val foreground = MutableStateFlow(false)
    val watchRunning = MutableStateFlow(false)
    private val _network = MutableStateFlow(true)
    val network: StateFlow<Boolean> = _network.asStateFlow()

    /** Screenshot tests pin the link state; null in the real app. */
    var pinnedLink: Link? = null

    private val cacheFile by lazy { File(filesDir, "feed-cache.json") }
    private val guidanceDir by lazy { File(filesDir, "guidance").apply { mkdirs() } }
    private var lastCacheWrite = 0L

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        http = SosApi.client("SOSForge-Android/${BuildConfig.VERSION_NAME}")
        notifier = Notifier(this).also { it.createChannels() }
        alerts = AlertEngine(this, prefs, notifier)
        live = LiveClient(scope, http) { batch, now -> onBatch(batch, now) }
        watch = Watch(this)
        offline = co.soclose.sosforge.core.map.OfflineMaps(this)
        scope.launch { loadCache() }
        watchNetwork()
        // a new server: forget what the old one offered, re-read, reconnect
        scope.launch {
            prefs.origin.drop(1).collect {
                _version.value = null
                _guidance.value = null
                prefs.putString(Prefs.K_POLL_CURSOR, null)
                prefs.putString(Prefs.K_POLL_ETAG, null)
                updateLiveDemand()
            }
        }
        // the scope follows the places and prefs (only matters while the Watch runs alone)
        scope.launch {
            combine(combine(prefs.places, prefs.here) { _, _ -> Unit }, prefs.alerts, foreground, watchRunning, version) { _, _, _, _, _ -> Unit }.collect { updateLiveDemand() }
        }
        scope.launch { watch.reconcile() }
        // the offline packs follow the places (a removed place loses its pack), on the main thread
        scope.launch {
            prefs.places.drop(1).collect { syncOfflineMaps() }
        }
        scope.launch {
            _unmetered.drop(1).collect { u ->
                withContext(Dispatchers.Main) { offline.onNetwork(u) }
                if (u) syncOfflineMaps()
            }
        }
    }

    suspend fun syncOfflineMaps() {
        val places = prefs.places.value
        withContext(Dispatchers.Main) { offline.sync(places, _unmetered.value) }
        if (!_unmetered.value) co.soclose.sosforge.core.map.OfflineMaps.schedule(this)
    }

    /**
     * Where the person is, for the Live "near me" view: re-read in the foreground when older than 15 min.
     * A permission no longer granted (refused, "only this time" expired, revoked) forgets the position.
     */
    suspend fun refreshHere(force: Boolean = false) {
        if (!co.soclose.sosforge.core.sys.Locate.granted(this)) {
            if (prefs.here.value != null) prefs.setHere(null)
            return
        }
        val h = prefs.here.value
        if (!force && h != null && System.currentTimeMillis() - h.at < HERE_REFRESH_MS) return
        val f = co.soclose.sosforge.core.sys.Locate.fix(this)
        if (f is co.soclose.sosforge.core.sys.Fix.Ok) {
            val cc = if (h != null && co.soclose.sosforge.core.geo.Geo.distanceKm(h.lat, h.lon, f.lat, f.lon) < 50) h.countryCode
                else co.soclose.sosforge.core.sys.Locate.countryOf(this, f.lat, f.lon)
            prefs.setHere(co.soclose.sosforge.core.Here(f.lat, f.lon, System.currentTimeMillis() - f.ageMs, cc))
        }
    }

    /** Reads latest.json at most every 6 h (or now, from Settings). A 404 is "nothing published yet". */
    suspend fun checkUpdate(force: Boolean = false) {
        val last = prefs.getLong(Prefs.K_UPDATE_AT)
        if (!force && last > 0 && System.currentTimeMillis() - last < co.soclose.sosforge.core.update.UpdateRules.EVERY_MS) {
            prefs.getString(Prefs.K_UPDATE_JSON)?.let { j ->
                runCatching { SosJson.decodeFromString(co.soclose.sosforge.core.update.LatestRelease.serializer(), j) }.getOrNull()?.let { r ->
                    _update.value = co.soclose.sosforge.core.update.UpdateRules.decide(r, prefs.origin.value, BuildConfig.VERSION_CODE, android.os.Build.VERSION.SDK_INT)
                }
            }
            return
        }
        val origin = prefs.origin.value
        val (state, rel) = withContext(Dispatchers.IO) { co.soclose.sosforge.core.update.UpdateCheck(http).read(origin) }
        prefs.putLong(Prefs.K_UPDATE_AT, System.currentTimeMillis())
        _update.value = if (rel != null) {
            prefs.putString(Prefs.K_UPDATE_JSON, SosJson.encodeToString(co.soclose.sosforge.core.update.LatestRelease.serializer(), rel))
            co.soclose.sosforge.core.update.UpdateRules.decide(rel, origin, BuildConfig.VERSION_CODE, android.os.Build.VERSION.SDK_INT)
        } else state
    }

    fun api(): SosApi = SosApi(prefs.origin.value, http)

    fun link(now: Long): Link {
        pinnedLink?.let { return it }
        val s = live.state.value
        return LinkRules.status(s.connected, s.lastMessageAt, s.openedAt, now, s.everReceived, _network.value, s.busy)
    }

    fun onForeground(on: Boolean) {
        foreground.value = on
        if (on) {
            scope.launch { refreshVersion() }
            scope.launch { refreshHere() }
            scope.launch { runCatching { checkUpdate() } }
            scope.launch { syncOfflineMaps() }
        }
    }

    /**
     * One socket, opened only when someone needs it (see the class comment), on the URL [LiveUrl] picks.
     * Until the server's contract is known the provider says "not now": an unfiltered v2 snapshot is
     * about 880 KiB, and a phone must never fetch it by accident.
     */
    fun updateLiveDemand() {
        val fg = foreground.value
        val watching = watchRunning.value
        if (!fg && !watching) {
            live.stop()
            return
        }
        if (_version.value == null) scope.launch { refreshVersion() }
        val origin = prefs.origin.value
        val mode = LiveUrl.mode(_version.value, LiveUrl.zonePlaces(prefs.places.value, if (fg) prefs.here.value else null).isNotEmpty())
        if (mode == LiveUrl.Mode.NONE) {
            live.stop()
            return
        }
        live.start {
            val f = foreground.value
            LiveUrl.url(origin, _version.value, LiveUrl.zonePlaces(prefs.places.value, if (f) prefs.here.value else null), prefs.alerts.value, f, live.state.value.refused != null)
        }
    }

    private var versionJob: kotlinx.coroutines.Job? = null

    /** Reads `/api/version`, retrying with the socket's backoff until it answers (404 = legacy). */
    suspend fun refreshVersion() {
        if (_version.value != null || versionJob?.isActive == true) return
        versionJob = scope.launch {
            var attempt = 0
            while (_version.value == null) {
                val v = withContext(Dispatchers.IO) { runCatching { api().version() }.getOrNull() }
                if (v != null) {
                    _version.value = v
                    live.nudge()
                    if (v.has(ApiVersion.GUIDANCE)) refreshGuidance()
                    break
                }
                attempt++
                kotlinx.coroutines.delay(co.soclose.sosforge.core.live.Watchdog.backoffMs(attempt))
            }
        }
    }

    /** One read of `/api/version` (the worker: no retry loop in a 10 min job). Null when unreachable. */
    suspend fun versionOnce(): ApiVersion? {
        _version.value?.let { return it }
        val v = withContext(Dispatchers.IO) { runCatching { api().version() }.getOrNull() } ?: return null
        _version.value = v
        return v
    }

    /** The world list on a zoned server (its socket only carries the owner's zones): a REST read, with its age. */
    data class WorldRead(val events: List<SosEvent>, val at: Long, val serverTimeMs: Long?, val error: ApiError?)

    private val _world = MutableStateFlow<WorldRead?>(null)
    val world: StateFlow<WorldRead?> = _world.asStateFlow()

    /** Severe+ events of the whole world (a zoned server's socket only carries the zones): the near view's "elsewhere" count. */
    private val _severeWorld = MutableStateFlow<WorldRead?>(null)
    val severeWorld: StateFlow<WorldRead?> = _severeWorld.asStateFlow()

    suspend fun refreshWorld(severeOnly: Boolean = false) {
        val now = System.currentTimeMillis()
        val target = if (severeOnly) _severeWorld else _world
        try {
            val v = _version.value ?: ApiVersion.NONE
            val scope = if (severeOnly) Scope(emptyList(), co.soclose.sosforge.core.net.Severity.SEVERE.key, null) else null
            val r = withContext(Dispatchers.IO) { api().events(v, scope, null, null, limit = if (severeOnly) 200 else 300) }.reply!!
            // the client filters again (a legacy server ignores min_severity)
            val events = r.events.let { if (severeOnly) it.filter { e -> co.soclose.sosforge.core.alert.AlertRules.rankOf(e) >= co.soclose.sosforge.core.net.Severity.SEVERE.rank } else it }
            target.value = WorldRead(events.sortedWith(FeedReducer.byEventTime), now, co.soclose.sosforge.core.net.Iso.ms(r.serverTime), null)
        } catch (e: ApiException) {
            target.value = (target.value ?: WorldRead(emptyList(), 0L, null, null)).copy(error = e.error)
        }
    }

    /** The guidance in the app's language, from the server, kept on disk for the offline case. */
    suspend fun refreshGuidance(lang: String = currentLang()) {
        val f = File(guidanceDir, "$lang.json")
        if (_guidance.value == null) {
            runCatching { SosJson.decodeFromString(Guidance.serializer(), f.readText()) }.getOrNull()?.let { _guidance.value = it }
        }
        val g = withContext(Dispatchers.IO) { runCatching { api().guidance(lang) }.getOrNull() } ?: return
        _guidance.value = g
        withContext(Dispatchers.IO) { runCatching { f.writeText(SosJson.encodeToString(Guidance.serializer(), g)) } }
    }

    fun currentLang(): String {
        val tags = androidx.appcompat.app.AppCompatDelegate.getApplicationLocales()
        val l = (if (!tags.isEmpty) tags[0] else null) ?: resources.configuration.locales[0]
        return when (l?.language) { "fr", "es", "ja" -> l.language; "in", "id" -> "id"; else -> "en" }
    }

    private suspend fun onBatch(batch: List<LiveClient.Arrived>, deviceNow: Long) {
        val changes = mutableListOf<FeedChange>()
        var snapshot = false
        val state = feedLock.withLock {
            var s = _feed.value
            for ((m, at) in batch) {
                // each message is reduced at its OWN arrival time: the clock window reads it per message
                val r = FeedReducer.reduce(s, m, at)
                s = r.state
                changes += r.changes
                if (m is ServerMessage.Snapshot || m is ServerMessage.SnapshotMore) snapshot = true
            }
            _feed.value = s
            s
        }
        // a split snapshot is complete when no chunk remains (snapshot_chunked)
        val complete = state.snapshotRemaining == 0
        alerts.onChanges(changes, state, deviceNow, fullRead = snapshot, complete = complete)
        alerts.releaseIfDue(state, deviceNow)
        if (snapshot && complete) verifyMissingPosted(state)
        if ((snapshot && complete) || deviceNow - lastCacheWrite > 30_000L) saveCache(state, deviceNow)
    }

    /**
     * After a (re)connection, an alert still on the lock screen whose event is not in the snapshot may
     * have been withdrawn while we were away, or may simply be older than the snapshot's window. We do
     * not guess: the server is asked about each one, and only a 404 cancels it.
     */
    suspend fun verifyMissingPosted(state: FeedState) {
        val present = state.events.map { it.id }.toSet()
        val missing = alerts.postedMissing(present).take(20)
        if (missing.isEmpty()) return
        val api = api()
        val gone = withContext(Dispatchers.IO) {
            missing.filter { e -> runCatching { api.event(e.eventId) == null }.getOrDefault(false) }
        }
        if (gone.isNotEmpty()) {
            alerts.onChanges(gone.map { FeedChange.Removed(it.eventId, "gone") }, state, System.currentTimeMillis(), fullRead = false)
        }
    }

    /**
     * Fold a REST poll (the worker's fallback) into the feed and the alerts. [replace]: a legacy world
     * read replaces the list like a snapshot; a zoned read is merged. [fullRead]: the read is the whole
     * picture (no cursor, or a reset), which matters for the first-read-is-history rule.
     */
    suspend fun applyPoll(events: List<SosEvent>, removed: List<String>, serverTimeMs: Long?, replace: Boolean, fullRead: Boolean, arrivedAt: Long? = null) {
        val now = System.currentTimeMillis()
        val (state, changes) = feedLock.withLock {
            val r = FeedReducer.fromPoll(_feed.value, events, removed, serverTimeMs, replace, now, arrivedAt ?: now)
            _feed.value = r.state
            r.state to r.changes
        }
        alerts.onChanges(changes, state, now, fullRead = fullRead)
        alerts.releaseIfDue(state, now, force = true)
        if (fullRead) verifyMissingPosted(state)
        saveCache(state, now)
    }

    private suspend fun loadCache() {
        val c = withContext(Dispatchers.IO) {
            runCatching { SosJson.decodeFromString(FeedCache.serializer(), cacheFile.readText()) }.getOrNull()
        } ?: return
        feedLock.withLock {
            if (_feed.value.events.isNotEmpty()) return
            _feed.value = FeedState(
                events = c.events.sortedWith(FeedReducer.byEventTime),
                sources = c.sources,
                clockSkewMs = c.skew,
                dataServerTime = c.serverTime,
                fromCache = true,
                cacheSavedAt = c.savedAt,
            )
        }
    }

    private suspend fun saveCache(state: FeedState, now: Long) {
        if (state.fromCache || state.events.isEmpty()) return
        lastCacheWrite = now
        // the same rule as the in-memory cut: every running alert is kept, only point events are trimmed
        val kept = FeedReducer.trim(state.events, state.serverNow(now), max = 500)
        withContext(Dispatchers.IO) {
            runCatching {
                val tmp = File(filesDir, "feed-cache.json.tmp")
                tmp.writeText(SosJson.encodeToString(FeedCache.serializer(), FeedCache(now, state.clockSkewMs, state.dataServerTime, kept, state.sources)))
                tmp.renameTo(cacheFile)
            }
        }
    }

    /** Screenshot tests and the emulator check seed the feed from a verbatim capture. */
    fun seedForScreens(events: List<SosEvent>, sources: List<SourceHealth>, serverTimeMs: Long, fromCache: Boolean) {
        val now = System.currentTimeMillis()
        _feed.value = FeedState(
            events = events.sortedWith(FeedReducer.byEventTime),
            sources = sources,
            clockSkewMs = serverTimeMs - now,
            lastMessageAt = now,
            dataServerTime = serverTimeMs,
            fromCache = fromCache,
            cacheSavedAt = if (fromCache) now - 12 * 60_000L else null,
        )
    }

    fun seedVersionForScreens(v: ApiVersion?, g: Guidance?) {
        _version.value = v
        _guidance.value = g
    }

    private fun watchNetwork() {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        _network.value = cm.activeNetwork?.let { cm.getNetworkCapabilities(it)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) } ?: false
        _unmetered.value = cm.activeNetwork?.let { cm.getNetworkCapabilities(it)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) } ?: false
        runCatching {
            cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    _network.value = true
                    // the link is back: do not wait out the backoff
                    live.nudge()
                }

                override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                    _unmetered.value = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
                }

                override fun onLost(network: Network) {
                    _network.value = false
                    _unmetered.value = false
                }
            })
        }
    }

    companion object {
        const val HERE_REFRESH_MS = 15 * 60_000L
        fun errorWords(e: Throwable): ApiError = (e as? ApiException)?.error ?: ApiError.Unreachable
        val EVENT_LIST = ListSerializer(SosEvent.serializer())
    }
}

val Context.app: SosApp get() = applicationContext as SosApp
