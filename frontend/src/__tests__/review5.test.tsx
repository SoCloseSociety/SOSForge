/** Review of PR #5 (adversarial, 2026-10-27): the scope, map and banner
 * findings, each reproduced first. */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import { makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage, SosEvent } from '../types'

const map = vi.hoisted(() => ({ ids: [] as string[] }))
vi.mock('../components/MapView', () => ({
  MapView: ({ events }: { events: SosEvent[] }) => {
    map.ids = events.map((e) => e.id)
    return null
  },
}))

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()
const TOKYO = { lat: 35.68, lon: 139.76, name: 'Tokyo' }

const nearQuake = makeEvent({ id: 'jma:near', lat: 35.5, lon: 139.5, severity: 'minor', time: ago(3) })
// a NOAA tsunami warning for Chile, issued 40 min ago, in force
const chileTsunami = makeEvent({
  id: 'tsunami:chile',
  kind: 'tsunami',
  severity: 'severe',
  tsunami: true,
  ongoing: true,
  place: 'Off the coast of Chile',
  lat: -33.0,
  lon: -72.0,
  time: ago(40),
})
// an extreme cyclone ongoing for days
const cyclone = makeEvent({
  id: 'gdacs:tc',
  kind: 'cyclone',
  severity: 'extreme',
  ongoing: true,
  place: 'Bay of Bengal',
  lat: 15,
  lon: 88,
  time: ago(4 * 24 * 60),
  magnitude: null,
})

function feed(events: SosEvent[]) {
  useStore.getState().ingest({
    type: 'snapshot',
    server_time: new Date().toISOString(),
    events,
    stats: makeStats(),
    sources: [],
  } as ServerMessage)
}

async function app() {
  const { default: App } = await import('../App')
  render(<App />)
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })
}

beforeEach(() => {
  resetStore()
  localStorage.clear()
  map.ids = []
  vi.stubGlobal('WebSocket', class { close() {} })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, headers: { get: () => null } })))
})
afterEach(() => vi.unstubAllGlobals())

describe('#3-#5: the elsewhere count is about the world, not the filters', () => {
  it('Live 15 min window: a far tsunami in force 40 min is still counted', async () => {
    feed([nearQuake, chileTsunami])
    useStore.getState().setPlace(TOKYO)
    useStore.getState().setWindow(15)
    await app()
    expect(document.querySelector('.scope-elsewhere')?.textContent).toContain('1 severe elsewhere')
  })

  it('earthquakes only, floor Extreme: the extreme cyclone and the tsunami still count', async () => {
    feed([nearQuake, chileTsunami, cyclone])
    useStore.getState().setPlace(TOKYO)
    const { kinds } = useStore.getState().filters
    for (const kind of [...kinds]) if (kind !== 'earthquake') useStore.getState().toggleKind(kind)
    useStore.getState().setMinSeverity('extreme')
    await app()
    expect(document.querySelector('.scope-elsewhere')?.textContent).toContain('2 severe elsewhere')
  })
})

describe('#6, #7: the open event stays on the map', () => {
  it('a deep-linked far event is on the map, and the first fix does not pull the camera off it', async () => {
    // a reader in Tokyo opens a shared link to the Chile warning
    feed([nearQuake, chileTsunami])
    useStore.setState({ scope: 'near', selected: 'tsunami:chile' })
    const watchers: PositionCallback[] = []
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { watchPosition: (ok: PositionCallback) => watchers.push(ok), clearWatch: () => undefined },
    })
    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: { query: async () => ({ state: 'granted' }) },
    })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    await app()
    act(() =>
      watchers.forEach((ok) =>
        ok({ coords: { latitude: 35.68, longitude: 139.76, accuracy: 3000 } } as GeolocationPosition),
      ),
    )
    expect(useStore.getState().here).not.toBeNull()
    expect(map.ids).toContain('tsunami:chile')
    expect(useStore.getState().focus?.name).not.toBe('near')
  })

  it('an alarm opening an event beyond the scope gives the map that event', async () => {
    feed([nearQuake, chileTsunami])
    useStore.getState().setPlace(TOKYO)
    await app()
    expect(map.ids).toEqual(['jma:near'])
    act(() => useStore.getState().select('tsunami:chile'))
    expect(map.ids).toEqual(['jma:near', 'tsunami:chile'])
  })
})

describe('#19: the banner names a tsunami first', () => {
  it('a newer extreme storm does not push the tsunami out of the banner', async () => {
    const storm = makeEvent({
      id: 'nws:storm',
      kind: 'storm',
      severity: 'extreme',
      ongoing: true,
      place: 'Coastal storm',
      time: ago(1),
      magnitude: null,
    })
    feed([storm, chileTsunami])
    useStore.setState({ scope: 'world' })
    await app()
    const banner = screen.getByRole('alert')
    expect(banner.textContent).toContain('TSUNAMI')
    expect(banner.textContent).toContain('Off the coast of Chile')
    // the other one is still counted, in a form that fits a phone
    expect(banner.querySelector('.banner-others-short')?.textContent).toContain('+1 ongoing')
  })
})

describe('#23: no invented zero before the first snapshot', () => {
  it('the KPI strip says unknown', async () => {
    useStore.setState({ scope: 'world', stats: makeStats({ last_hour: 0 }) })
    await app()
    const kpis = document.querySelector('.kpis')?.textContent ?? ''
    expect(kpis).not.toMatch(/0 in the last hour/)
    expect(kpis).not.toMatch(/0\/0/)
  })
})
