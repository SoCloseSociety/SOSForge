/** `connectLive` est ce qui tient la regle produit: "le flux ne doit jamais
 * mentir sur sa propre fraicheur". Trois mecanismes, chacun protege ici par
 * les tests qui portent son nom: la reconnexion exponentielle (1 s, 2 s, 4 s...
 * plafonnee a 15 s), le watchdog de 15 s qui ferme une socket "open" mais
 * morte, et le nettoyage qui arrete tout sans relancer de reconnexion. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { connectLive } from '../live'
import { useStore } from '../store'
import { NOW, makeStats, resetStore } from './helpers'

/** Faux WebSocket: le vrai reseau n'a rien a faire dans un test unitaire.
 * `close()` declenche `onclose` (comme un navigateur, mais en synchrone), et
 * les helpers `open` / `message` / `error` simulent le serveur. */
class FakeWebSocket {
  /** toutes les instances creees, dans l'ordre: compter les reconnexions */
  static instances: FakeWebSocket[] = []

  url: string
  closeCalls = 0
  onopen: (() => void) | null = null
  onmessage: ((message: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close(): void {
    this.closeCalls += 1
    this.onclose?.()
  }

  /* -- cote "serveur": les evenements que le test declenche -- */
  open(): void {
    this.onopen?.()
  }

  message(data: string): void {
    this.onmessage?.({ data })
  }

  error(): void {
    this.onerror?.()
  }
}

/** La derniere socket creee: apres une reconnexion, c'est elle la vivante. */
function lastSocket(): FakeWebSocket {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
}

/** Un tick serveur valide, horodate sur l'horloge (fausse) courante. */
function tick(): string {
  return JSON.stringify({
    type: 'tick',
    server_time: new Date(Date.now()).toISOString(),
    stats: makeStats(),
    sources: [],
    clients: 1,
  })
}

/** Verifie qu'une reconnexion part exactement apres `ms`: rien a ms - 1,
 * une nouvelle socket a ms. C'est le coeur du test de backoff. */
function expectReconnectAfter(ms: number): void {
  const before = FakeWebSocket.instances.length
  vi.advanceTimersByTime(ms - 1)
  expect(FakeWebSocket.instances.length).toBe(before)
  vi.advanceTimersByTime(1)
  expect(FakeWebSocket.instances.length).toBe(before + 1)
}

let stop: (() => void) | null = null

beforeEach(() => {
  // Horloge figee: les delais de backoff et les 15 s du watchdog se testent
  // sans attendre, et Date.now() est deterministe.
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  resetStore()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  // Toujours nettoyer la connexion AVANT de rendre les vrais timers, sinon
  // l'interval du watchdog fuit dans le test suivant.
  stop?.()
  stop = null
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('connectLive: URL', () => {
  it("construit l'URL sur l'hote courant, en ws: quand la page est en http:", () => {
    stop = connectLive()
    // jsdom sert la page sur http://localhost:3000: la socket doit viser le
    // meme hote (le proxy /ws), jamais un hote code en dur.
    expect(lastSocket().url).toBe(`ws://${location.host}/ws`)
    expect(lastSocket().url).toBe('ws://localhost:3000/ws')
  })
})

describe('connectLive: messages', () => {
  it('un message illisible (JSON casse) ne tue PAS la connexion', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()

    expect(() => socket.message('{pas du json')).not.toThrow()

    // La connexion est toujours la, et le message suivant est bien ingere.
    expect(useStore.getState().connected).toBe(true)
    expect(socket.closeCalls).toBe(0)
    socket.message(tick())
    expect(useStore.getState().lastMessageAt).toBe(Date.now())
  })
})

describe('connectLive: reconnexion exponentielle', () => {
  it('onopen met connected a vrai', () => {
    stop = connectLive()
    expect(useStore.getState().connected).toBe(false)
    lastSocket().open()
    expect(useStore.getState().connected).toBe(true)
  })

  it('une fermeture reconnecte apres 1 s, puis 2 s, 4 s, 8 s, plafonne a 15 s', () => {
    stop = connectLive()
    // Chaque echec double le delai; le plafond de 15 s est verifie deux fois
    // pour prouver qu'il tient (et ne repart pas a 16 s, 32 s...).
    for (const delay of [1000, 2000, 4000, 8000, 15000, 15000]) {
      lastSocket().close()
      expect(useStore.getState().connected).toBe(false)
      expectReconnectAfter(delay)
    }
  })

  it('seul un MESSAGE recu remet le compteur de backoff a zero', () => {
    stop = connectLive()
    // Deux echecs: on est monte a un delai de 2 s.
    lastSocket().close()
    expectReconnectAfter(1000)
    lastSocket().close()
    expectReconnectAfter(2000)

    // Une socket qui s'ouvre ne prouve rien: un proxy peut accepter le TCP sans
    // rien acheminer. Le backoff doit continuer a monter.
    lastSocket().open()
    expect(useStore.getState().connected).toBe(true)
    lastSocket().close()
    expectReconnectAfter(4000)

    // Un message, en revanche, prouve que le lien porte des donnees.
    lastSocket().open()
    lastSocket().message(tick())
    lastSocket().close()
    expectReconnectAfter(1000)
  })

  it('une erreur ferme la socket, ce qui enclenche le cycle de reconnexion', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.error()
    expect(socket.closeCalls).toBe(1)
    expect(useStore.getState().connected).toBe(false)
    expectReconnectAfter(1000)
  })
})

describe('connectLive: watchdog de 15 s', () => {
  it('15 s sans message en se croyant connecte: la socket est FERMEE pour forcer la reconnexion', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.message(tick())

    // Silence total ensuite (wifi coupe, proxy qui avale les paquets): la
    // socket reste "open" mais le lien est mort. Le watchdog verifie toutes les
    // 2 s, donc la detection tombe entre 15 et 17 s -- et non jusqu'a 20 s
    // comme avec l'ancien pas de 5 s.
    vi.advanceTimersByTime(15_000)
    expect(socket.closeCalls).toBe(0) // 15 s pile: pas encore depasse
    const avant = FakeWebSocket.instances.length

    vi.advanceTimersByTime(2_000)
    expect(socket.closeCalls).toBe(1)
    expect(useStore.getState().connected).toBe(false)

    // Et la fermeture forcee declenche bien une reconnexion, au premier palier
    // du backoff puisque ce lien avait recu au moins un message.
    vi.advanceTimersByTime(1_000)
    expect(FakeWebSocket.instances.length).toBe(avant + 1)
  })

  it('des ticks reguliers empechent toute fermeture: une connexion saine ne se fait jamais couper', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.message(tick())

    // Une minute de vie nominale: un tick toutes les 5 s. Le watchdog passe
    // douze fois et ne doit jamais toucher a la socket.
    for (let i = 0; i < 12; i += 1) {
      vi.advanceTimersByTime(5_000)
      socket.message(tick())
    }
    expect(socket.closeCalls).toBe(0)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(useStore.getState().connected).toBe(true)
  })

  it('laisse au snapshot le temps d arriver avant de juger', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()

    // Une connexion qui vient de s'ouvrir n'a pas encore recu son snapshot:
    // la fermer serait couper une connexion qui demarre.
    vi.advanceTimersByTime(10_000)
    expect(socket.closeCalls).toBe(0)
  })

  it('ferme une premiere connexion qui s ouvre et reste MUETTE', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()

    // Le piege que ce test protege: un proxy qui accepte le TCP sans rien
    // acheminer. `lastMessageAt` reste a zero, `connected` est vrai, et sans
    // repli sur l'heure d'ouverture le watchdog ne fermait JAMAIS -- l'interface
    // affichait un flux fige en annoncant "EN DIRECT", indefiniment.
    vi.advanceTimersByTime(18_000)
    expect(socket.closeCalls).toBe(1)
  })
})

describe('connectLive: nettoyage', () => {
  it('arrete la socket, le watchdog et le backoff, sans relancer de reconnexion', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.message(tick())

    stop()
    expect(socket.closeCalls).toBe(1)
    // Plus aucun timer vivant: ni watchdog, ni reconnexion en attente.
    expect(vi.getTimerCount()).toBe(0)

    // Et le temps qui passe ne recree rien.
    vi.advanceTimersByTime(120_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    stop = null
  })

  it('annule aussi une reconnexion deja programmee', () => {
    stop = connectLive()
    // La socket tombe: une reconnexion est posee pour dans 1 s...
    lastSocket().close()
    expect(useStore.getState().connected).toBe(false)

    // ...mais le nettoyage arrive avant. Elle ne doit jamais partir.
    stop()
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(120_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    stop = null
  })
})
