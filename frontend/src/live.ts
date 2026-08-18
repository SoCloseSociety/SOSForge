import { useStore } from './store'
import type { ServerMessage } from './types'

/** Websocket client: exponential reconnection, and a watchdog on the
 * heartbeat.
 *
 * This file carries the product's central promise: **the feed must never
 * lie about its own freshness**. Three traps were found here by the tests
 * and closed here, all three of the same kind -- a connection that looks
 * alive without being alive.
 */

/** Silence tolerated before considering the link dead. The server emits a
 * tick every second: fifteen seconds with nothing is already huge. */
const SILENCE_LIMIT_MS = 15_000
/** Watchdog step. At 2 s, detection lands between 15 and 17 s of silence;
 * at 5 s it could reach 20 s, a third more than the contract. */
const WATCHDOG_STEP_MS = 2_000

export function connectLive(): () => void {
  let socket: WebSocket | null = null
  let closed = false
  let attempt = 0
  let openedAt = 0
  let reconnectTimer: number | undefined
  let watchdog: number | undefined

  const url = () => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${location.host}/ws`
  }

  const scheduleReconnect = () => {
    // state must fall to "disconnected" EVEN when we're stopping for good:
    // otherwise the interface stays on "LIVE" after an unmount
    useStore.getState().setConnected(false)
    if (closed) return
    attempt += 1
    const delay = Math.min(1000 * 2 ** (attempt - 1), 15000)
    reconnectTimer = window.setTimeout(open, delay)
  }

  const open = () => {
    if (closed) return
    socket = new WebSocket(url())

    socket.onopen = () => {
      // We do NOT reset the backoff counter here. An open socket proves
      // nothing: a proxy that accepts the TCP connection without forwarding
      // anything would open the connection, we'd restart at 1 s, the
      // watchdog would close it again five seconds later -- and we'd hammer
      // the server every six seconds without ever climbing toward the
      // ceiling. Success is a MESSAGE received.
      openedAt = Date.now()
      useStore.getState().setConnected(true)
    }

    socket.onmessage = (message) => {
      attempt = 0
      try {
        useStore.getState().ingest(JSON.parse(message.data) as ServerMessage)
      } catch {
        /* unreadable message: we ignore it rather than kill the connection */
      }
    }

    socket.onclose = () => {
      socket = null
      scheduleReconnect()
    }

    socket.onerror = () => socket?.close()
  }

  watchdog = window.setInterval(() => {
    const { lastMessageAt, connected } = useStore.getState()
    if (!connected) return
    // The reference point is the last message IF we've received one,
    // otherwise the socket's opening. Without this fallback, a first
    // connection that opens and stays silent was never closed:
    // `lastMessageAt` being 0, the guard let it through and the interface
    // showed a frozen feed while announcing "LIVE", indefinitely. That was
    // exactly the lie this file exists to forbid.
    const reference = lastMessageAt || openedAt
    if (reference && Date.now() - reference > SILENCE_LIMIT_MS) {
      socket?.close()
    }
  }, WATCHDOG_STEP_MS)

  open()

  return () => {
    closed = true
    window.clearTimeout(reconnectTimer)
    window.clearInterval(watchdog)
    useStore.getState().setConnected(false)
    socket?.close()
  }
}
