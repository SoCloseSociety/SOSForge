import { useStore } from './store'
import type { ServerMessage } from './types'

/** Client websocket: reconnexion exponentielle, et watchdog sur le heartbeat.
 *
 * Ce fichier porte la promesse centrale du produit: **le flux ne doit jamais
 * mentir sur sa propre fraicheur**. Trois pieges y ont ete trouves par les tests
 * et fermes ici, tous les trois du meme genre -- une connexion qui parait vivante
 * sans l'etre.
 */

/** Silence tolere avant de considerer le lien mort. Le serveur emet un tick par
 * seconde: quinze secondes sans rien est deja enorme. */
const SILENCE_LIMIT_MS = 15_000
/** Pas du watchdog. A 2 s, la detection tombe entre 15 et 17 s de silence;
 * a 5 s elle pouvait atteindre 20 s, soit un tiers de plus que le contrat. */
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
    // l'etat doit tomber a "deconnecte" MEME quand on s'arrete pour de bon:
    // sinon l'interface reste sur "EN DIRECT" apres un demontage
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
      // On NE remet PAS le compteur de backoff a zero ici. Une socket ouverte ne
      // prouve rien: un proxy qui accepte le TCP sans rien acheminer ouvrait la
      // connexion, on repartait a 1 s, le watchdog refermait cinq secondes plus
      // tard -- et on martelait le serveur toutes les six secondes sans jamais
      // monter vers le plafond. Le succes, c'est un MESSAGE recu.
      openedAt = Date.now()
      useStore.getState().setConnected(true)
    }

    socket.onmessage = (message) => {
      attempt = 0
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
    if (!connected) return
    // Le point de reference est le dernier message SI on en a recu un, sinon
    // l'ouverture de la socket. Sans ce repli, une premiere connexion qui
    // s'ouvre et reste muette n'etait jamais fermee: `lastMessageAt` valant 0,
    // la garde le laissait passer et l'interface affichait un flux fige en
    // annoncant "EN DIRECT", indefiniment. C'etait exactement le mensonge que
    // ce fichier existe pour interdire.
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
