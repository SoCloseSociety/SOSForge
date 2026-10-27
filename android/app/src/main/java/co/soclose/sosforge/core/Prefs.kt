package co.soclose.sosforge.core

import android.content.Context
import co.soclose.sosforge.core.alert.AlertPrefs
import co.soclose.sosforge.core.alert.QuietHours
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.ServerAddress
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.ui.theme.ThemeMode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.builtins.ListSerializer

/**
 * The owner's choices, on this device only (no backup, no cloud transfer: see the res/xml backup and extraction rules). Every
 * value is a StateFlow so a screen and the watch service see the same thing; an unreadable stored
 * value reads as its default, never a crash.
 */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("sos", Context.MODE_PRIVATE)

    private fun <T> flow(read: () -> T) = MutableStateFlow(read())

    private val _origin = flow { ServerAddress.normalize(sp.getString(K_ORIGIN, null) ?: "") ?: ServerAddress.DEFAULT }
    val origin: StateFlow<String> = _origin.asStateFlow()

    private val _theme = flow { ThemeMode.fromKey(sp.getString(K_THEME, null)) }
    val theme: StateFlow<ThemeMode> = _theme.asStateFlow()

    private val _textSize = flow { TextSize.fromKey(sp.getString(K_TEXT, null)) }
    val textSize: StateFlow<TextSize> = _textSize.asStateFlow()

    private val _onboarded = flow { sp.getBoolean(K_ONBOARDED, false) }
    val onboarded: StateFlow<Boolean> = _onboarded.asStateFlow()

    private val _places = flow {
        runCatching { SosJson.decodeFromString(ListSerializer(Place.serializer()), sp.getString(K_PLACES, "[]")!!) }
            .getOrDefault(emptyList()).filter { it.valid }
    }
    val places: StateFlow<List<Place>> = _places.asStateFlow()

    private val _alerts = flow {
        runCatching { SosJson.decodeFromString(AlertPrefs.serializer(), sp.getString(K_ALERTS, null)!!) }.getOrDefault(AlertPrefs())
    }
    val alerts: StateFlow<AlertPrefs> = _alerts.asStateFlow()

    private val _quiet = flow {
        runCatching { SosJson.decodeFromString(QuietHours.serializer(), sp.getString(K_QUIET, null)!!) }.getOrDefault(QuietHours())
    }
    val quiet: StateFlow<QuietHours> = _quiet.asStateFlow()

    /** The owner switched the live Watch (foreground service) on. */
    private val _watchLive = flow { sp.getBoolean(K_WATCH, false) }
    val watchLive: StateFlow<Boolean> = _watchLive.asStateFlow()

    fun setOrigin(v: String) { sp.edit().putString(K_ORIGIN, v).apply(); _origin.value = v }
    fun setTheme(v: ThemeMode) { sp.edit().putString(K_THEME, v.key).apply(); _theme.value = v }
    fun setTextSize(v: TextSize) { sp.edit().putString(K_TEXT, v.key).apply(); _textSize.value = v }
    fun setOnboarded(v: Boolean) { sp.edit().putBoolean(K_ONBOARDED, v).apply(); _onboarded.value = v }
    fun setWatchLive(v: Boolean) { sp.edit().putBoolean(K_WATCH, v).apply(); _watchLive.value = v }

    fun setPlaces(v: List<Place>) {
        val kept = v.filter { it.valid }.take(Place.MAX_PLACES)
        sp.edit().putString(K_PLACES, SosJson.encodeToString(ListSerializer(Place.serializer()), kept)).apply()
        _places.value = kept
    }

    fun setAlerts(v: AlertPrefs) { sp.edit().putString(K_ALERTS, SosJson.encodeToString(AlertPrefs.serializer(), v)).apply(); _alerts.value = v }
    fun setQuiet(v: QuietHours) { sp.edit().putString(K_QUIET, SosJson.encodeToString(QuietHours.serializer(), v)).apply(); _quiet.value = v }

    /** Small facts the diagnostics show (last poll, its result, the poll cursor and ETag). */
    fun getString(key: String): String? = sp.getString(key, null)
    fun putString(key: String, value: String?) { sp.edit().putString(key, value).apply() }
    fun getLong(key: String): Long = sp.getLong(key, 0L)
    fun putLong(key: String, value: Long) { sp.edit().putLong(key, value).apply() }

    companion object {
        private const val K_ORIGIN = "origin"
        private const val K_THEME = "theme"
        private const val K_TEXT = "text_size"
        private const val K_ONBOARDED = "onboarded"
        private const val K_PLACES = "places"
        private const val K_ALERTS = "alerts"
        private const val K_QUIET = "quiet"
        private const val K_WATCH = "watch_live"
        const val K_POLL_AT = "poll_at"
        const val K_POLL_RESULT = "poll_result"
        const val K_POLL_CURSOR = "poll_cursor"
        const val K_POLL_ETAG = "poll_etag"
        const val K_WATCH_STARTED = "watch_started"
        const val K_WATCH_ERROR = "watch_error"
    }
}

/**
 * The owner's text size (fleet rule, 2026-10-10: every app has one). Four steps, ONE root scale that
 * multiplies the system font scale. (Kit: GStatsForge 0.2.4, GStats commit cdb56f0, core/TextSize.kt.)
 */
enum class TextSize(val key: String, val scale: Float) {
    SMALL("small", 0.9f),
    DEFAULT("default", 1.0f),
    LARGE("large", 1.15f),
    XLARGE("xlarge", 1.3f);

    companion object {
        fun fromKey(key: String?): TextSize = entries.firstOrNull { it.key == key } ?: DEFAULT
    }
}
