package co.soclose.sosforge.core.alert

import co.soclose.sosforge.core.feed.isEnded
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.net.Severity
import co.soclose.sosforge.core.net.SosEvent
import kotlinx.serialization.Serializable

/** What the owner asked to be alerted about (funnel step 3, Settings). */
@Serializable
data class AlertPrefs(
    val kinds: Set<String> = DEFAULT_KINDS,
    val minSeverity: String = Severity.MODERATE.key,
) {
    val floor: Severity get() = Severity.of(minSeverity)

    companion object {
        /**
         * The sane default: every hazard that kills people within hours. Heat, drought, space weather
         * and "other" are opt-in: they are regional and slow, and an alarm that fires for everything is
         * an alarm people switch off (frontend/src/alarm.ts MIN_SEVERITY says the same).
         */
        val DEFAULT_KINDS: Set<String> = setOf(
            Kind.EARTHQUAKE, Kind.TSUNAMI, Kind.VOLCANO, Kind.CYCLONE, Kind.FLOOD,
            Kind.WILDFIRE, Kind.STORM, Kind.LANDSLIDE, Kind.AVALANCHE,
        ).map { it.key }.toSet()
    }
}

/** Quiet hours, device local time, a window that may cross midnight. Minutes since midnight. */
@Serializable
data class QuietHours(val enabled: Boolean = false, val fromMin: Int = 23 * 60, val toMin: Int = 7 * 60) {
    /** Is [localMinute] (0..1439) inside the window? from == to is an empty window, never "all day". */
    fun active(localMinute: Int): Boolean {
        if (!enabled || fromMin == toMin) return false
        val m = ((localMinute % 1440) + 1440) % 1440
        return if (fromMin < toMin) m in fromMin until toMin else m >= fromMin || m < toMin
    }
}

/** The three alerting levels, one Android channel each (plus the silent watch-status channel). */
enum class Level(val rank: Int) {
    /** Moderate, minor, info: a quiet digest. */
    DIGEST(0),
    /** Severe near a place. */
    SEVERE(1),
    /** Extreme (or a tsunami) near a place: high importance, sound, never held by quiet hours. */
    EXTREME(2),
}

/**
 * Why an event concerns the owner: the nearest of their places it reaches, or (an event with no
 * position) the place whose country it was issued for ([byCountry], [distanceKm] is NaN then).
 */
data class Match(val level: Level, val place: Place, val distanceKm: Double, val byCountry: Boolean = false)

/** One line of the notification ledger: what was decided for one cluster of solutions. */
@Serializable
data class Entry(
    val key: String,
    /** The event id currently shown for this key (the cluster's representative can change). */
    val eventId: String,
    val revision: Int,
    val level: Level,
    val state: State,
    val notifId: Int,
    val at: Long,
    val placeName: String = "",
    /** The HIGHEST severity rank this key has rung for: a rise above it rings again, nothing else does. */
    val severity: Int = 0,
) {
    enum class State { POSTED, HELD }
}

/**
 * What the app saw of an event near a place without (necessarily) notifying it: its severity and
 * whether it concerned the owner then. This is what lets an event first seen BELOW the threshold ring
 * when it escalates (the macOS agent marked such ids "announced" and never rang the escalation).
 */
@Serializable
data class Seen(val sev: Int, val matched: Boolean)

/** What to do with one change. */
sealed interface Decision {
    data object Nothing : Decision
    /** Show it. [alert]: sound / vibrate / heads-up (false = a quiet update in place). */
    data class Post(val level: Level, val alert: Boolean) : Decision
    data class Hold(val level: Level) : Decision
    data object Cancel : Decision
    data object DropHeld : Decision
}

/** The server's own "this is news" for one live message. */
data class Signal(val breaking: Boolean = false, val escalated: Boolean = false) {
    companion object {
        val NONE = Signal()
    }
}

/**
 * THE ALERTING RULES, pure (no Android import): JUnit runs them on the JVM (AlertRulesTest, LedgerTest).
 * Every rule that decides whether to interrupt a person lives here, with its reason. Lesson 24: such a
 * rule is judged by how often it fires on real traffic, so the tests also replay the verbatim capture.
 */
object AlertRules {
    /** A point-in-time event older than this is history, not an alert (frontend WatchPanel ALARM_MAX_AGE_S). */
    const val MAX_AGE_MS = 3_600_000L

    /**
     * Is this event still news on the age rule? Computed on the EVENT's own timestamp against the
     * SERVER clock, never on its arrival (lesson 4: the first GDACS cycle pushed 96 alerts several days
     * old as "live"). A running alert stays news while it runs (it carries the time it was ISSUED,
     * often days old: an age cut drops exactly the alerts that matter); a warning dated in the future
     * (lead time) is news; anything past its own `expires` is not; an unreadable timestamp is not.
     */
    fun isNews(e: SosEvent, serverNow: Long): Boolean {
        if (e.isEnded(serverNow)) return false
        if (e.ongoing) return true
        val t = e.timeMs ?: return false
        return serverNow - t <= MAX_AGE_MS
    }

    /** The severity rank that rings: a tsunami flag counts as extreme whatever the label says (alarm.ts). */
    fun rankOf(e: SosEvent): Int = if (Geo.isTsunami(e)) Severity.EXTREME.rank else e.severityOf.rank

    fun levelOf(e: SosEvent): Level = when (rankOf(e)) {
        Severity.EXTREME.rank -> Level.EXTREME
        Severity.SEVERE.rank -> Level.SEVERE
        else -> Level.DIGEST
    }

    /** The kind and severity filters the owner chose. A tsunami flag counts as extreme and as "tsunami". */
    fun passesPrefs(e: SosEvent, prefs: AlertPrefs): Boolean {
        val kindOk = e.kind in prefs.kinds || (e.tsunami && Kind.TSUNAMI.key in prefs.kinds)
        return kindOk && rankOf(e) >= prefs.floor.rank
    }

    /**
     * Does this event concern one of the owner's places? The nearest place it reaches, or null.
     *
     * An event WITHOUT a position (most Meteoalarm warnings: no geometry is published) cannot be
     * distance-matched. It matches a place only by the place's COUNTRY, only at severe and above, and
     * only where the owner left "country-wide warnings" on for that place. Lesson 24 is why it is
     * explicit and per place: "notify unplaced extremes" rang 63 times a day from Los Angeles for
     * other coasts; the switch shows its live count so the owner decides on their own number.
     */
    fun match(e: SosEvent, places: List<Place>, prefs: AlertPrefs): Match? {
        if (places.isEmpty() || !passesPrefs(e, prefs)) return null
        if (!e.hasPosition) return countryMatch(e, places)
        var best: Match? = null
        for (p in places) {
            val d = Geo.distanceKm(p, e) ?: continue
            if (d <= Geo.reachKm(p, e) && (best == null || d < best.distanceKm)) best = Match(levelOf(e), p, d)
        }
        return best
    }

    /** The floor of a country-wide (positionless) warning. */
    val COUNTRY_FLOOR: Severity = Severity.SEVERE

    /** The place an event WITHOUT a position concerns by country, at [COUNTRY_FLOOR] and above. */
    fun countryMatch(e: SosEvent, places: List<Place>): Match? {
        if (e.hasPosition || rankOf(e) < COUNTRY_FLOOR.rank) return null
        val p = countryPlace(e, places) ?: return null
        return Match(levelOf(e), p, Double.NaN, byCountry = true)
    }

    private fun countryPlace(e: SosEvent, places: List<Place>): Place? {
        val cc = e.countryCode?.uppercase()?.takeIf { it.length == 2 } ?: return null
        return places.firstOrNull { it.countryWide && it.countryCode?.uppercase() == cc }
    }

    /**
     * Close enough to a place to be worth remembering (twice its reach, or its country when it has no
     * position): what [Seen] records, so an escalation of something seen below the floor still rings.
     */
    fun isNear(e: SosEvent, places: List<Place>): Boolean =
        if (e.hasPosition) places.any { p -> (Geo.distanceKm(p, e) ?: Double.MAX_VALUE) <= 2 * Geo.reachKm(p, e) }
        else countryPlace(e, places) != null

    /**
     * The decision for an event that is (still) in the feed. In order:
     *
     *  - an event that no longer concerns the owner (relocated, downgraded, filtered) or that has ENDED
     *    (`expires` passed) takes its notification away, and is never notified;
     *  - the FIRST full read after install ([history]) is history: nothing in it rings. Every later
     *    snapshot is not: an id never seen before is judged like any live message (a reconnect snapshot
     *    carries what happened during the outage -- the macOS agent swallowed a nearby M6.6 that way);
     *  - news = the server said so ([Signal.breaking] / [Signal.escalated]) or the age rule ([isNews]);
     *  - an id seen before rings only if it ESCALATED (lane F's flag, or a rank above what was seen
     *    while still news) or if it did not concern the owner then and does now (relocated into a zone);
     *  - never twice: a key that rang is updated IN PLACE, silently, on a real revision; only a rank
     *    above the highest it rang for rings again;
     *  - a key cancelled before is never notified again ([retired]);
     *  - quiet hours HOLD everything but [Level.EXTREME], which is never held.
     */
    fun decide(
        entry: Entry?,
        e: SosEvent,
        match: Match?,
        serverNow: Long,
        signal: Signal,
        quiet: Boolean,
        retired: Boolean,
        seen: Seen?,
        history: Boolean,
    ): Decision {
        if (match == null || e.isEnded(serverNow)) {
            return when (entry?.state) {
                Entry.State.POSTED -> Decision.Cancel
                Entry.State.HELD -> Decision.DropHeld
                null -> Decision.Nothing
            }
        }
        val level = match.level
        val rank = rankOf(e)
        if (entry == null) {
            if (retired || history) return Decision.Nothing
            val news = isNews(e, serverNow) || signal.breaking || signal.escalated
            val ring = when {
                seen == null -> news
                signal.escalated -> true
                rank > seen.sev -> news
                !seen.matched -> news
                else -> false
            }
            if (!ring) return Decision.Nothing
            return if (quiet && level != Level.EXTREME) Decision.Hold(level) else Decision.Post(level, alert = true)
        }
        return when (entry.state) {
            Entry.State.POSTED -> when {
                rank > entry.severity -> Decision.Post(level, alert = !quiet || level == Level.EXTREME)
                entry.eventId == e.id && e.revision <= entry.revision && level == entry.level -> Decision.Nothing
                else -> Decision.Post(level, alert = false)
            }
            Entry.State.HELD -> when {
                level == Level.EXTREME -> Decision.Post(level, alert = true)
                quiet -> Decision.Hold(level)
                else -> Decision.Post(level, alert = true)
            }
        }
    }

    /** A withdrawn event (purge, all-clear): what was shown goes, what was waiting is dropped. */
    fun decideRemoved(entry: Entry?): Decision = when (entry?.state) {
        Entry.State.POSTED -> Decision.Cancel
        Entry.State.HELD -> Decision.DropHeld
        null -> Decision.Nothing
    }

    /**
     * When an [Level.EXTREME] alert posts during quiet hours, every HELD warning is dropped: the owner is
     * now awake and looking at the app, and a morning batch of older, lesser warnings would only bury
     * the one that mattered (idea from KontentReach KrNotifyRules: "a newer event that posts leaves no
     * older one held for the morning"). Dropped ones stay in the feed; they are never notified.
     */
    fun dropsHeldOnPost(level: Level, quiet: Boolean): Boolean = quiet && level == Level.EXTREME

    /** A stable notification id for a key (an Int, as Android wants), never under 100 (1 and 2 are taken). */
    fun notifIdFor(key: String): Int {
        val h = key.hashCode() and 0x7fffffff
        return if (h < 100) h + 100 else h
    }
}
