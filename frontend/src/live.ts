import { useStore } from './store'
import type { ServerMessage } from './types'

/** Client websocket: reconnexion exponentielle, et watchdog sur le heartbeat.
 *
 * Le watchdog compte: un websocket peut rester "open" alors que la connexion est
 * morte (wifi coupe, proxy qui avale les paquets). Le serveur envoie un tick par
 * seconde; si rien n'arrive pendant 15 s, on considere le lien perdu et on
 * reconnecte au lieu d'afficher un flux fige en pretendant qu'il est live.
 */
export function connectLive(): () => void {
  let socket: WebSocket | null = null
  let closed = false
  let attempt = 0
  let reconnectTimer: number | undefined
  let watchdog: number | undefined

  const url = () => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${location.host}/ws`
  }

  const scheduleReconnect = () => {
    if (closed) return
    attempt += 1
    const delay = Math.min(1000 * 2 ** (attempt - 1), 15000)
    useStore.getState().setConnected(false)
    reconnectTimer = window.setTimeout(open, delay)
  }

  const open = () => {
    if (closed) return
    socket = new WebSocket(url())

    socket.onopen = () => {
      attempt = 0
      useStore.getState().setConnected(true)
    }

    socket.onmessage = (message) => {
      try {
        useStore.getState().ingest(JSON.parse(message.data) as ServerMessage)
      } catch {
        /* message illisible: on ignore plutot que de tuer la connexion */
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
    if (connected && lastMessageAt && Date.now() - lastMessageAt > 15000) {
      socket?.close()
    }
  }, 5000)

  open()

  return () => {
    closed = true
    window.clearTimeout(reconnectTimer)
    window.clearInterval(watchdog)
    socket?.close()
  }
}
