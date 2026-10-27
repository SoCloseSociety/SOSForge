package co.soclose.sosforge.core

import android.content.Context
import android.content.res.Configuration
import androidx.appcompat.app.AppCompatDelegate
import co.soclose.sosforge.R
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.net.Severity
import java.util.Locale

/** Resource ids for the closed vocabularies (frontend/src/i18n.ts `sev.*`, `kind.*`, `response.*`). */
object Words {
    fun severity(s: Severity): Int = when (s) {
        Severity.INFO -> R.string.sev_info
        Severity.MINOR -> R.string.sev_minor
        Severity.MODERATE -> R.string.sev_moderate
        Severity.SEVERE -> R.string.sev_severe
        Severity.EXTREME -> R.string.sev_extreme
    }

    /** Plural, for filters ("Earthquakes"). */
    fun kinds(k: Kind): Int = when (k) {
        Kind.EARTHQUAKE -> R.string.kind_earthquake
        Kind.TSUNAMI -> R.string.kind_tsunami
        Kind.VOLCANO -> R.string.kind_volcano
        Kind.CYCLONE -> R.string.kind_cyclone
        Kind.FLOOD -> R.string.kind_flood
        Kind.WILDFIRE -> R.string.kind_wildfire
        Kind.STORM -> R.string.kind_storm
        Kind.HEAT -> R.string.kind_heat
        Kind.DROUGHT -> R.string.kind_drought
        Kind.LANDSLIDE -> R.string.kind_landslide
        Kind.AVALANCHE -> R.string.kind_avalanche
        Kind.SPACE_WEATHER -> R.string.kind_space_weather
        Kind.OTHER -> R.string.kind_other
    }

    /** Singular, for one event ("Earthquake"). */
    fun kind(k: Kind): Int = when (k) {
        Kind.EARTHQUAKE -> R.string.kind1_earthquake
        Kind.TSUNAMI -> R.string.kind1_tsunami
        Kind.VOLCANO -> R.string.kind1_volcano
        Kind.CYCLONE -> R.string.kind1_cyclone
        Kind.FLOOD -> R.string.kind1_flood
        Kind.WILDFIRE -> R.string.kind1_wildfire
        Kind.STORM -> R.string.kind1_storm
        Kind.HEAT -> R.string.kind1_heat
        Kind.DROUGHT -> R.string.kind1_drought
        Kind.LANDSLIDE -> R.string.kind1_landslide
        Kind.AVALANCHE -> R.string.kind1_avalanche
        Kind.SPACE_WEATHER -> R.string.kind1_space_weather
        Kind.OTHER -> R.string.kind1_other
    }

    /** CAP responseType, a closed vocabulary; null for a value we have no words for (shown as is). */
    fun response(r: String?): Int? = when (r?.lowercase()) {
        "shelter" -> R.string.response_shelter
        "evacuate" -> R.string.response_evacuate
        "avoid" -> R.string.response_avoid
        "prepare" -> R.string.response_prepare
        "execute" -> R.string.response_execute
        "monitor" -> R.string.response_monitor
        "assess" -> R.string.response_assess
        else -> null
    }

    /** Display name of a backend source (frontend/src/format.ts SOURCE_LABEL). */
    val SOURCE_LABEL: Map<String, String> = mapOf(
        "emsc" to "EMSC", "usgs" to "USGS", "tsunami" to "NOAA tsunami", "gdacs" to "GDACS", "nws" to "NWS",
        "volcano" to "USGS volcanoes", "jma" to "JMA", "bmkg" to "BMKG", "geonet" to "GeoNet", "ingv" to "INGV",
        "geofon" to "GEOFON", "eonet" to "NASA EONET", "meteoalarm" to "Meteoalarm", "wmo" to "WMO", "nhc" to "NHC",
        "ash" to "Ash SIGMET", "afad" to "AFAD", "jma_eew" to "JMA EEW", "cenc" to "CENC", "swarm" to "Swarm detection",
        "aftershock" to "USGS aftershock", "space" to "NOAA SWPC", "jma-tsunami" to "JMA tsunami", "ssn" to "SSN (Mexico)",
        "nrcan" to "NRCan", "noa" to "NOA (Greece)", "phivolcs" to "PHIVOLCS", "igp" to "IGP (Peru)",
        "geonet-volcano" to "GeoNet volcanoes",
    )

    fun source(name: String): String = SOURCE_LABEL[name] ?: name

    /**
     * A context in the language the owner picked in Settings. Before Android 13 the per-app locale only
     * reaches activities; a notification built from the Application context would speak the phone's
     * language instead. This is the one place that fixes it.
     */
    fun localized(ctx: Context): Context {
        val tags = AppCompatDelegate.getApplicationLocales()
        if (tags.isEmpty) return ctx
        val loc: Locale = tags[0] ?: return ctx
        val cfg = Configuration(ctx.resources.configuration)
        cfg.setLocale(loc)
        return ctx.createConfigurationContext(cfg)
    }
}
