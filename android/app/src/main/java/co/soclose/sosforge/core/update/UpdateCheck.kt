package co.soclose.sosforge.core.update

import co.soclose.sosforge.core.net.ServerAddress
import co.soclose.sosforge.core.net.SosJson
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException

/**
 * `GET /download/latest.json` (tasks/todo.md, Round 2, served by lane G), `Cache-Control: no-cache`:
 * `{"app", "version", "version_code", "url", "size", "sha256", "cert_sha256", "min_sdk", "released", "notes"}`.
 * Every field is optional here: a missing one makes the release NOT offered, never a crash.
 */
@Serializable
data class LatestRelease(
    val app: String = "",
    val version: String = "",
    @SerialName("version_code") val versionCode: Int = 0,
    val url: String = "",
    val size: Long = 0,
    val sha256: String = "",
    @SerialName("cert_sha256") val certSha256: String = "",
    @SerialName("min_sdk") val minSdk: Int = 0,
    val released: String = "",
    val notes: Map<String, String> = emptyMap(),
)

/** What Settings says about updates. */
sealed interface UpdateState {
    data object NotChecked : UpdateState
    data object UpToDate : UpdateState
    /** The server has no release published yet (404, before lane G's deploy). */
    data object NonePublished : UpdateState
    data object Unreachable : UpdateState
    /** A release was found but is NOT offered, and why (another signer, needs a newer Android...). */
    data class Refused(val why: Why) : UpdateState
    data class Available(val release: LatestRelease, val downloadUrl: String) : UpdateState

    enum class Why { OTHER_APP, OTHER_SIGNER, NEEDS_NEWER_ANDROID, BAD_URL }
}

/**
 * Whether to suggest an update, pure (UpdateRulesTest). The app never installs anything itself (no
 * REQUEST_INSTALL_PACKAGES): it shows a quiet card whose link opens the browser. It suggests a release
 * only when it is THIS app, newer, installable on this Android, served from an https address (or a
 * cleartext dev host), and signed with the SAME certificate as this build: a different signer could
 * not even install over this one, and would be someone else's APK.
 */
object UpdateRules {
    const val APP_ID = "co.soclose.sosforge"
    /** The release key's certificate (~/.soclose/android/sosforge.cert.sha256, every SOSForge APK since 0.1.0). */
    const val PINNED_CERT_SHA256 = "610d345a896dfc71bd0ddc4122668c9ffcce0e16bc495d6ed4f573812a98bdf8"
    /** How often a running app re-reads latest.json. */
    const val EVERY_MS = 6 * 3_600_000L

    fun normalizeHex(s: String): String = s.lowercase().filter { it.isLetterOrDigit() }

    /** The download link, absolute: `url` may be relative to the server ("/download/..."). Null when unusable. */
    fun resolve(origin: String, url: String): String? {
        val abs = if (url.startsWith("/")) origin.trimEnd('/') + url else url
        val u = abs.toHttpUrlOrNull() ?: return null
        return when {
            u.scheme == "https" -> u.toString()
            u.scheme == "http" && u.host.lowercase() in ServerAddress.CLEARTEXT_HOSTS -> u.toString()
            else -> null
        }
    }

    fun decide(r: LatestRelease, origin: String, myCode: Int, sdk: Int): UpdateState {
        if (r.app != APP_ID) return UpdateState.Refused(UpdateState.Why.OTHER_APP)
        if (r.versionCode <= myCode) return UpdateState.UpToDate
        if (normalizeHex(r.certSha256) != PINNED_CERT_SHA256) return UpdateState.Refused(UpdateState.Why.OTHER_SIGNER)
        if (r.minSdk > sdk) return UpdateState.Refused(UpdateState.Why.NEEDS_NEWER_ANDROID)
        val link = resolve(origin, r.url) ?: return UpdateState.Refused(UpdateState.Why.BAD_URL)
        return UpdateState.Available(r, link)
    }

    /**
     * What an answer to latest.json says. A 404 is "nothing published yet"; so is a 200 in HTML:
     * production answers /download/latest.json with the web app's index.html (SPA fallback, captured
     * 2026-10-27), which is a reachable server with no release file, not an unreachable one.
     */
    fun answer(code: Int, contentType: String?, body: String): Pair<UpdateState, LatestRelease?> = when {
        code == 404 -> UpdateState.NonePublished to null
        code !in 200..299 -> UpdateState.Unreachable to null
        contentType?.contains("html", ignoreCase = true) == true || body.trimStart().startsWith("<") -> UpdateState.NonePublished to null
        else -> runCatching { SosJson.decodeFromString(LatestRelease.serializer(), body) }.getOrNull()
            ?.let { UpdateState.NotChecked to it } ?: (UpdateState.Unreachable to null)
    }

    /** A card the owner dismissed stays hidden for THAT version only; a newer one shows again. */
    fun showCard(state: UpdateState, dismissedCode: Int): Boolean =
        state is UpdateState.Available && state.release.versionCode > dismissedCode
}

/** Reads latest.json once. Blocking: run it on Dispatchers.IO. */
class UpdateCheck(private val http: OkHttpClient) {
    fun read(origin: String): Pair<UpdateState, LatestRelease?> {
        val url = origin.trimEnd('/') + "/download/latest.json"
        val req = Request.Builder().url(url).header("Accept", "application/json").header("Cache-Control", "no-cache").build()
        return try {
            http.newCall(req).execute().use { r ->
                UpdateRules.answer(r.code, r.header("Content-Type"), if (r.isSuccessful) r.body?.string().orEmpty() else "")
            }
        } catch (_: IOException) {
            UpdateState.Unreachable to null
        } catch (_: IllegalArgumentException) {
            UpdateState.Unreachable to null
        }
    }
}
