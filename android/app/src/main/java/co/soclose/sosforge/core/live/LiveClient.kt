package co.soclose.sosforge.core.live

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import kotlin.random.Random

/** The socket as the screens and the watch see it. Times are device milliseconds. */
data class SocketState(
    val running: Boolean = false,
    val connected: Boolean = false,
    /** When the current attempt began (0 when none is in flight). */
    val startedAt: Long = 0L,
    val openedAt: Long = 0L,
    val lastMessageAt: Long = 0L,
    val everReceived: Boolean = false,
    val attempt: Int = 0,
    val nextRetryAt: Long = 0L,
    val lastError: String? = null,
    /** The server said it is at capacity (1013, or a refused handshake): "server busy, retrying". */
    val busy: Boolean = false,
    /** The query the current socket was opened with (empty = the plain, unscoped feed). */
    val query: String = "",
    /** lane B refused our scope: the next sockets go plain, the client filters alone. */
    val refused: String? = null,
)

/**
 * The websocket (frontend/src/live.ts, ported): ONE current socket only. Every callback carries the
 * generation of the socket that produced it and is IGNORED unless it is the current one (GStats lesson:
 * a stale socket's late callbacks once double-applied a stream). Messages are batched about every
 * [BATCH_MS] so a burst of 200 GDACS re-polls costs one state change, not 200.
 *
 * The rules (silence, connect timeout, backoff with jitter, busy) are [Watchdog]'s, pure and tested.
 */
class LiveClient(
    private val scope: CoroutineScope,
    private val http: OkHttpClient,
    private val clock: () -> Long = System::currentTimeMillis,
    private val random: () -> Double = { Random.nextDouble() },
    private val onBatch: suspend (List<Arrived>, Long) -> Unit,
) {
    private val _state = MutableStateFlow(SocketState())

    /** A message with the device time it ARRIVED (the clock offset is read per message, not per batch). */
    data class Arrived(val message: ServerMessage, val at: Long)
    val state: StateFlow<SocketState> = _state.asStateFlow()

    @Volatile private var gen = 0
    private var socket: WebSocket? = null
    private var loop: Job? = null
    private var drainer: Job? = null
    /** Returns the socket URL, or null for "not now" (the server's contract is not known yet). */
    private var urlProvider: (() -> String?)? = null
    private var openedUrl: String? = null
    private val inbox = Channel<Triple<Int, String, Long>>(Channel.UNLIMITED)
    private val wake = Channel<Unit>(Channel.CONFLATED)

    /** Start (or keep) the socket on [url]. Calling it again with the same URL changes nothing. */
    @Synchronized
    fun start(url: () -> String?) {
        urlProvider = url
        if (drainer?.isActive != true) drainer = scope.launch { drain() }
        if (loop?.isActive == true) {
            // the wanted URL changed (scope, server): reopen at once
            if (url() != openedUrl) reopen()
            return
        }
        _state.update { it.copy(running = true, attempt = 0) }
        loop = scope.launch { run() }
    }

    @Synchronized
    fun stop() {
        loop?.cancel()
        loop = null
        closeCurrent("stopped")
        // the state must fall to "disconnected" even when stopping for good (live.ts scheduleReconnect)
        _state.update { it.copy(running = false, connected = false, startedAt = 0L) }
    }

    /** The network came back, or the scope changed: do not wait out the backoff. */
    fun nudge() {
        wake.trySend(Unit)
    }

    @Synchronized
    private fun reopen() {
        closeCurrent("reopen")
        _state.update { it.copy(attempt = 0) }
        nudge()
    }

    @Synchronized
    private fun closeCurrent(reason: String) {
        gen += 1
        socket?.cancel()
        socket = null
        _state.update { it.copy(connected = false, startedAt = 0L, lastError = reason) }
    }

    private suspend fun run() {
        while (scope.isActive) {
            val provider = urlProvider ?: return
            val url = provider()
            if (url == null) {
                // nothing to open yet: wait for a nudge (the version arrived) or the next step
                withTimeoutOrNull(Watchdog.STEP_MS) { wake.receive() }
                continue
            }
            open(url)
            // watch this socket until it dies: closed, failed, silent, or never opened
            while (scope.isActive) {
                val s = _state.value
                if (socketGone()) break
                val now = clock()
                if (Watchdog.connectTimedOut(s.connected, s.startedAt, now)) {
                    closeCurrent("no answer in ${Watchdog.CONNECT_TIMEOUT_MS / 1000} s")
                    break
                }
                if (Watchdog.isDead(s.connected, s.lastMessageAt, s.openedAt, now)) {
                    closeCurrent("silent for ${Watchdog.SILENCE_LIMIT_MS / 1000} s")
                    break
                }
                withTimeoutOrNull(Watchdog.STEP_MS) { wake.receive() }
            }
            val attempt = _state.value.attempt + 1
            val wait = Watchdog.retryDelayMs(attempt, _state.value.busy, random())
            _state.update { it.copy(attempt = attempt, nextRetryAt = clock() + wait, connected = false, startedAt = 0L) }
            withTimeoutOrNull(wait) { wake.receive() }
        }
    }

    @Synchronized
    private fun socketGone(): Boolean = socket == null

    @Synchronized
    private fun open(url: String) {
        gen += 1
        val mine = gen
        openedUrl = url
        val query = url.substringAfter('?', "")
        _state.update { it.copy(connected = false, openedAt = 0L, startedAt = clock(), query = query) }
        socket = http.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (mine != gen) return webSocket.cancel()
                // An open socket proves nothing: the backoff is NOT reset here (live.ts trap 3), and
                // "busy" is only cleared by a message.
                _state.update { it.copy(connected = true, openedAt = clock(), lastError = null) }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (mine != gen) return
                inbox.trySend(Triple(mine, text, clock()))
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
                ended(mine, "closed $code ${reason.take(80)}".trim(), Watchdog.isBusy(code, null))
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) =
                ended(mine, "closed $code", Watchdog.isBusy(code, null))

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val status = response?.code
                val why = if (status != null) "handshake $status" else t.javaClass.simpleName + (t.message?.let { ": " + it.take(80) } ?: "")
                ended(mine, why, Watchdog.isBusy(null, status))
            }
        })
    }

    @Synchronized
    private fun ended(mine: Int, why: String, busy: Boolean) {
        if (mine != gen) return
        gen += 1
        socket = null
        _state.update { it.copy(connected = false, startedAt = 0L, lastError = why, busy = busy) }
        nudge()
    }

    private suspend fun drain() {
        val batch = ArrayList<Pair<String, Long>>()
        while (scope.isActive) {
            val first = inbox.receive()
            if (first.first == gen) batch += first.second to first.third
            val deadline = clock() + BATCH_MS
            while (true) {
                val left = deadline - clock()
                if (left <= 0) break
                val next = withTimeoutOrNull(left) { inbox.receive() } ?: break
                if (next.first == gen) batch += next.second to next.third
            }
            if (batch.isEmpty()) continue
            val now = clock()
            val messages = batch.mapNotNull { (text, at) -> Protocol.parse(text)?.let { Arrived(it, at) } }
            batch.clear()
            if (messages.isEmpty()) continue
            // Success is a MESSAGE received: only now are the backoff and "busy" reset.
            val refused = messages.map { it.message }.filterIsInstance<ServerMessage.Refused>().firstOrNull()
            _state.update {
                it.copy(
                    lastMessageAt = now, everReceived = true, attempt = 0, busy = false,
                    refused = refused?.let { r -> "${r.code}: ${r.reason}" } ?: it.refused,
                )
            }
            onBatch(messages, now)
        }
    }

    companion object {
        const val BATCH_MS = 200L
    }
}
