package co.soclose.sosforge.core

import android.content.Context
import co.soclose.sosforge.R
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Words for figures. Every age is computed by the caller on the SERVER clock and handed here in
 * seconds. A figure and its unit never break apart: the strings carry a no-break space (U+00A0).
 */
object Fmt {
    /** frontend/src/format.ts formatAge: a negative age is lead time ("in 2 h"), never "just now". */
    fun age(ctx: Context, seconds: Long): String = when {
        seconds < -60 -> {
            val ahead = -seconds
            when {
                ahead < 3600 -> ctx.getString(R.string.age_in_min, (ahead / 60.0).roundToInt())
                ahead < 86400 -> ctx.getString(R.string.age_in_h, (ahead / 3600.0).roundToInt())
                else -> ctx.getString(R.string.age_in_d, (ahead / 86400.0).roundToInt())
            }
        }
        seconds < 5 -> ctx.getString(R.string.age_now)
        seconds < 60 -> ctx.getString(R.string.age_s, seconds.toInt())
        seconds < 3600 -> ctx.getString(R.string.age_min, (seconds / 60).toInt())
        seconds < 86400 -> ctx.getString(R.string.age_h, (seconds / 3600).toInt())
        else -> ctx.getString(R.string.age_d, (seconds / 86400).toInt())
    }

    /** The bucket [age] picks, pure (unit-tested without resources). */
    fun ageBucket(seconds: Long): Pair<String, Long> = when {
        seconds < -60 -> {
            val ahead = -seconds
            when {
                ahead < 3600 -> "in_min" to (ahead / 60.0).roundToInt().toLong()
                ahead < 86400 -> "in_h" to (ahead / 3600.0).roundToInt().toLong()
                else -> "in_d" to (ahead / 86400.0).roundToInt().toLong()
            }
        }
        seconds < 5 -> "now" to 0L
        seconds < 60 -> "s" to seconds
        seconds < 3600 -> "min" to seconds / 60
        seconds < 86400 -> "h" to seconds / 3600
        else -> "d" to seconds / 86400
    }

    fun km(ctx: Context, km: Double): String = ctx.getString(R.string.fmt_km, if (km < 10) String.format(Locale.US, "%.1f", km) else km.roundToInt().toString())

    fun magnitude(m: Double): String = String.format(Locale.US, "%.1f", m)

    private val UTC_CLOCK = DateTimeFormatter.ofPattern("HH:mm", Locale.US).withZone(ZoneOffset.UTC)
    private val LOCAL_CLOCK = DateTimeFormatter.ofPattern("HH:mm", Locale.US)
    private val LOCAL_DAY = DateTimeFormatter.ofPattern("d MMM HH:mm")

    fun utc(ms: Long): String = UTC_CLOCK.format(Instant.ofEpochMilli(ms)) + " UTC"
    fun localClock(ms: Long, zone: ZoneId = ZoneId.systemDefault()): String = LOCAL_CLOCK.withZone(zone).format(Instant.ofEpochMilli(ms))
    fun localDay(ms: Long, locale: Locale, zone: ZoneId = ZoneId.systemDefault()): String =
        LOCAL_DAY.withLocale(locale).withZone(zone).format(Instant.ofEpochMilli(ms))

    fun hm(minutes: Int): String = String.format(Locale.US, "%02d:%02d", (minutes / 60) % 24, minutes % 60)

    /** A coordinate pair as people read it: 35.68 N, 139.76 E. */
    fun latLon(lat: Double, lon: Double): String =
        String.format(Locale.US, "%.2f %s, %.2f %s", kotlin.math.abs(lat), if (lat >= 0) "N" else "S", kotlin.math.abs(lon), if (lon >= 0) "E" else "W")

    /** Regional indicator symbols from an ISO2 code; null when not exactly two letters (never approximate). */
    fun flag(iso2: String?): String? {
        if (iso2 == null || !Regex("^[A-Za-z]{2}$").matches(iso2)) return null
        return iso2.uppercase().map { c -> String(Character.toChars(0x1F1E6 + (c - 'A'))) }.joinToString("")
    }

    /** The country in the owner's language, from the ISO2 code. */
    fun country(iso2: String?, locale: Locale): String? =
        iso2?.takeIf { it.length == 2 }?.let { Locale("", it.uppercase()).getDisplayCountry(locale).ifBlank { null } }
}
