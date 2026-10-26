package co.soclose.sosforge.core.live

/**
 * The freshness rules of the socket, ported from frontend/src/live.ts and kept PURE so they are tested
 * exactly as the web's are. This is the product's central promise: the feed never lies about its own
 * freshness. The traps closed in the web client, each closed here too:
 *
 *  1. a socket that opens and then says nothing is closed (the reference is the opening when no
 *     message came yet: otherwise the very first connection could hang forever announcing LIVE);
 *  2. after a reconnect, the reference is the MOST RECENT of "last message" and "opened": a stale
 *     last-message stamp from before the outage must not close the brand-new socket at once;
 *  3. the backoff is NOT reset when a socket opens, only when a MESSAGE arrives: a proxy that accepts
 *     TCP and forwards nothing would otherwise be hammered every few seconds forever;
 *  4. a socket stuck in CONNECTING (TLS or the upgrade never answering) is abandoned after
 *     [CONNECT_TIMEOUT_MS]: waiting for a close callback that may never come is how a client hangs;
 *  5. a server at its per-address cap answers 1013 ("try again later", or a refused handshake): the
 *     retry waits with JITTER, so the phones behind one proxy do not all come back on the same second.
 */
object Watchdog {
    /** Silence tolerated before the link is declared dead. The server ticks every second. */
    const val SILENCE_LIMIT_MS = 15_000L

    /** Watchdog step: detection lands between 15 and 17 s of silence. */
    const val STEP_MS = 2_000L

    /** Backoff ceiling between reconnection attempts. */
    const val MAX_BACKOFF_MS = 15_000L

    /** A socket not open after this long is given up and retried. */
    const val CONNECT_TIMEOUT_MS = 10_000L

    /** The floor of a retry after the server said it is busy (1013): no point in knocking sooner. */
    const val BUSY_MIN_MS = 5_000L

    /** The instant silence is measured from (0 when the socket is not open). */
    fun reference(lastMessageAt: Long, openedAt: Long): Long = maxOf(lastMessageAt, openedAt)

    /** Should an OPEN socket be closed as dead? */
    fun isDead(connected: Boolean, lastMessageAt: Long, openedAt: Long, now: Long): Boolean {
        if (!connected) return false
        val ref = reference(lastMessageAt, openedAt)
        return ref > 0 && now - ref > SILENCE_LIMIT_MS
    }

    /** Should a socket that never opened be abandoned? [startedAt]: when this attempt began. */
    fun connectTimedOut(connected: Boolean, startedAt: Long, now: Long): Boolean =
        !connected && startedAt > 0 && now - startedAt > CONNECT_TIMEOUT_MS

    /** Delay before attempt [attempt] (1-based): 1 s, 2 s, 4 s, 8 s, then 15 s (live.ts). */
    fun backoffMs(attempt: Int): Long {
        if (attempt <= 1) return 1_000L
        val shift = (attempt - 1).coerceAtMost(20)
        return minOf(1_000L shl shift, MAX_BACKOFF_MS)
    }

    /**
     * The delay actually waited: the backoff plus up to 30 % of jitter ([random] in 0..1), and at least
     * [BUSY_MIN_MS] when the server said it is busy. Pure: the random draw is passed in.
     */
    fun retryDelayMs(attempt: Int, busy: Boolean, random: Double): Long {
        val base = backoffMs(attempt)
        val jittered = base + (base * 0.3 * random.coerceIn(0.0, 1.0)).toLong()
        return if (busy) maxOf(jittered, BUSY_MIN_MS + (BUSY_MIN_MS * random.coerceIn(0.0, 1.0)).toLong()) else jittered
    }

    /** A close code or handshake status that means "at capacity, come back later". */
    fun isBusy(closeCode: Int?, handshakeStatus: Int?): Boolean =
        closeCode == 1013 || handshakeStatus == 403 || handshakeStatus == 429 || handshakeStatus == 503
}

/** What the screen may say about the feed. LIVE is the ONLY state that shows the live pill. */
enum class Link { LIVE, CONNECTING, RECONNECTING, BUSY, OFFLINE }

/**
 * The link state shown everywhere (the pill, the banner, the watch notification). Pure.
 *
 * LIVE needs an open socket AND a message within the silence limit, measured at the moment of drawing:
 * a socket the watchdog has not closed yet but that is already silent is no longer called live.
 */
object LinkRules {
    fun status(
        connected: Boolean,
        lastMessageAt: Long,
        openedAt: Long,
        now: Long,
        everReceived: Boolean,
        networkAvailable: Boolean,
        busy: Boolean = false,
    ): Link {
        val heardSinceOpen = lastMessageAt >= openedAt && lastMessageAt > 0
        if (connected && heardSinceOpen && now - lastMessageAt <= Watchdog.SILENCE_LIMIT_MS) return Link.LIVE
        if (!networkAvailable) return Link.OFFLINE
        if (busy) return Link.BUSY
        return if (everReceived) Link.RECONNECTING else Link.CONNECTING
    }
}
