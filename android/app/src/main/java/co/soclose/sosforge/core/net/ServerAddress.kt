package co.soclose.sosforge.core.net

import java.net.URI

/**
 * What the owner types ("sosforge.soclose.co", "https://sosforge.soclose.co/api/", "http://10.8.0.1:8300")
 * becomes one canonical origin ("https://sosforge.soclose.co"). A bare host defaults to https; plain http
 * is accepted for [CLEARTEXT_HOSTS] only. Pure, unit-tested; null when not an origin.
 * (Ported from GStatsForge 0.2.4 core/net/ServerAddress.kt.)
 */
object ServerAddress {
    const val DEFAULT = "https://sosforge.soclose.co"

    /**
     * The ONLY hosts plain http may reach: this phone (an `adb reverse` to `make dev-api` :8300), the
     * emulator's host alias, and the WireGuard hub. EXACTLY the `<domain>` list of
     * res/xml/network_security_config.xml (RulesTest compares the two).
     */
    val CLEARTEXT_HOSTS: Set<String> = setOf("localhost", "127.0.0.1", "10.0.2.2", "10.8.0.1")

    fun normalize(raw: String): String? {
        var s = raw.trim()
        if (s.isEmpty() || s.any { it.isWhitespace() }) return null
        if (!s.contains("://")) s = "https://$s"
        val uri = runCatching { URI(s) }.getOrNull() ?: return null
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        val host = uri.host?.takeIf { it.isNotBlank() } ?: return null
        if (uri.userInfo != null || uri.query != null || uri.fragment != null) return null
        val path = (uri.path ?: "").trimEnd('/')
        if (path.isNotEmpty() && path != "/api") return null
        val port = if (uri.port != -1) ":${uri.port}" else ""
        val h = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host
        val origin = "$scheme://${h.lowercase()}$port"
        if (scheme == "http" && !allowsCleartext(origin)) return null
        return origin
    }

    fun allowsCleartext(origin: String): Boolean {
        val host = runCatching { URI(origin).host }.getOrNull()?.lowercase() ?: return false
        return host in CLEARTEXT_HOSTS
    }

    /** The host the owner sees ("sosforge.soclose.co"). */
    fun hostOf(origin: String): String = runCatching { URI(origin).host }.getOrNull() ?: origin

    /** `https://` -> `wss://`, `http://` -> `ws://`, then `/ws` (plus the zone query when the server has it). */
    fun wsUrl(origin: String, query: String = ""): String {
        val base = origin.replaceFirst(Regex("^https://"), "wss://").replaceFirst(Regex("^http://"), "ws://")
        return base.trimEnd('/') + "/ws" + (if (query.isEmpty()) "" else "?$query")
    }
}
