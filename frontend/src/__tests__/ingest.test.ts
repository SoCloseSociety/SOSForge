/** `ingest` est le point d'entree unique de tout ce qui arrive du websocket.
 * Chaque test protege un comportement qui a une histoire (voir README:
 * revisions, GDACS, horloge serveur). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import type { ServerMessage, SourceHealth } from '../types'
import { NOW, makeEvent, makeStats, minutesAgo, resetStore } from './helpers'

const SOURCES: SourceHealth[] = [
  { name: 'emsc', connected: true, last_ok: null, last_error: null, events_seen: 3, errors: 0 },
]

function eventMessage(
  event = makeEvent(),
  { breaking = false, type = 'event' as 'event' | 'update' } = {},
): ServerMessage {
  return { type, event, primary: true, breaking }
}

beforeEach(() => {
  // Horloge figee: clockSkew et le halo de 30 s se testent sans attendre.
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  resetStore()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('snapshot', () => {
  it('remplace entierement la liste au lieu de fusionner', () => {
    useStore.setState({ events: [makeEvent({ id: 'stale-1' }), makeEvent({ id: 'stale-2' })] })
    const fresh = [makeEvent({ id: 'snap-1' }), makeEvent({ id: 'snap-2' })]
    useStore.getState().ingest({
      type: 'snapshot',
      server_time: new Date(NOW).toISOString(),
      events: fresh,
      stats: makeStats(),
      sources: SOURCES,
    })
    expect(useStore.getState().events.map((e) => e.id)).toEqual(['snap-1', 'snap-2'])
    expect(useStore.getState().connected).toBe(true)
  })

  it('cale clockSkew sur l horloge serveur, pas sur celle du navigateur', () => {
    // Le serveur est 5 s "dans le futur" du navigateur: tous les ages affiches
    // doivent etre calcules avec ce decalage (regle produit: jamais l'horloge
    // du navigateur).
    useStore.getState().ingest({
      type: 'snapshot',
      server_time: new Date(NOW + 5000).toISOString(),
      events: [],
      stats: makeStats(),
      sources: SOURCES,
    })
    expect(useStore.getState().clockSkew).toBe(5000)
  })
})

describe('tick', () => {
  it('rafraichit stats, sources, clients et le clockSkew a chaque battement', () => {
    useStore.getState().ingest({
      type: 'tick',
      server_time: new Date(NOW - 2000).toISOString(),
      stats: makeStats({ last_hour: 7 }),
      sources: SOURCES,
      clients: 4,
    })
    const state = useStore.getState()
    expect(state.stats?.last_hour).toBe(7)
    expect(state.clients).toBe(4)
    expect(state.clockSkew).toBe(-2000)
    expect(state.lastMessageAt).toBe(NOW)
  })
})

describe('events et updates', () => {
  it('une revision d un evenement connu le met a jour sans le dupliquer', () => {
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'q1', magnitude: 5.8 })))
    useStore
      .getState()
      .ingest(eventMessage(makeEvent({ id: 'q1', magnitude: 6.1, revision: 1 }), { type: 'update' }))
    const events = useStore.getState().events
    expect(events).toHaveLength(1)
    expect(events[0].magnitude).toBe(6.1)
    expect(events[0].revision).toBe(1)
  })

  it('le flux reste trie par date d evenement decroissante, quel que soit l ordre d arrivee', () => {
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'mid', time: minutesAgo(30) })))
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'old', time: minutesAgo(90) })))
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'new', time: minutesAgo(1) })))
    expect(useStore.getState().events.map((e) => e.id)).toEqual(['new', 'mid', 'old'])
  })

  it('breaking: false ne marque PAS l evenement comme frais (96 alertes GDACS ne clignotent pas)', () => {
    // L'incident d'origine: au premier cycle GDACS, ~96 alertes vieilles de
    // plusieurs jours arrivaient comme messages `event` et clignotaient toutes
    // en breaking news. Seul le serveur sait distinguer "vient de se produire"
    // de "vient d'entrer dans le buffer": le client doit respecter son verdict.
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'gdacs-old' }), { breaking: false }))
    expect(useStore.getState().fresh.has('gdacs-old')).toBe(false)
  })

  it('breaking: true marque l evenement frais, et le halo s eteint seul apres 30 s', () => {
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'hot' }), { breaking: true }))
    expect(useStore.getState().fresh.has('hot')).toBe(true)
    vi.advanceTimersByTime(30_000)
    expect(useStore.getState().fresh.has('hot')).toBe(false)
  })
})
