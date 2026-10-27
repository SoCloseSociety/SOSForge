/** Events with no position, in Near me (adversarial review of PR #5,
 * 2026-10-27, HIGH): an unplaced event below severe went nowhere -- no feed,
 * no map, no count -- and a severe one was counted "elsewhere" although its
 * location is unknown. Most Meteoalarm warnings are unplaced; the one named
 * in the review, live that day, is the first fixture below (id, place, kind,
 * severity and country verbatim from /api/events; times made relative). */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { countriesIn, decode, type CountryTable } from '../countries'
import table from '../data/countries.json'
import { splitByScope } from '../scope'
import { useStore } from '../store'
import { makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage, SosEvent } from '../types'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()
const unplaced = (over: Partial<SosEvent>) =>
  makeEvent({ lat: null, lon: null, depth_km: null, magnitude: null, mag_type: null, region: null, ...over })

const HARZ = unplaced({
  id: 'meteoalarm:2.49.0.0.276.0.DWD.PVW.1793088660000.1c9db1be-5609-47fd-b916-ccd4f73928d6.MUL',
  source: 'meteoalarm',
  kind: 'storm',
  severity: 'moderate',
  place: 'Kreis Harz - Bergland (Oberharz)',
  title: 'Kreis Harz - Bergland (Oberharz)',
  country: 'germany',
  country_code: 'DE',
  time: ago(180),
  received_at: ago(170),
  ongoing: true,
})
const DODEKANISA = unplaced({
  id: 'meteoalarm:2.49.0.0.300.0.GR.261026122700.001000013',
  source: 'meteoalarm',
  kind: 'storm',
  severity: 'moderate',
  place: 'Dodekanisa Islands',
  title: 'Dodekanisa Islands',
  country: 'greece',
  country_code: 'GR',
  time: ago(60),
  ongoing: true,
})
// WMO names neither a position nor a country here (live, same day)
const LIANZHOU = unplaced({
  id: 'wmo:44188241600000_20261027100233',
  source: 'wmo',
  kind: 'wildfire',
  severity: 'severe',
  place: 'Lianzhou city',
  title: 'Lianzhou city',
  country: null,
  country_code: null,
  time: ago(300),
  ongoing: true,
})

const shapes = decode(table as CountryTable)

describe('the countries table, offline', () => {
  it('names the country of a point, and both sides of a border', () => {
    expect(countriesIn(shapes, 51.75, 10.62)).toEqual(['DE']) // the Harz
    expect(countriesIn(shapes, 9.51, 100.01)).toEqual(['TH']) // Ko Samui
    expect(countriesIn(shapes, 48.85, 2.35)).toEqual(['FR'])
    expect(countriesIn(shapes, 35.68, 139.76)).toEqual(['JP'])
    // 1:50m puts downtown El Paso in Mexico: the margin keeps the US
    expect(countriesIn(shapes, 31.76, -106.49)).toEqual(['MX', 'US'])
    expect(countriesIn(shapes, 30, -40)).toEqual([]) // mid-Atlantic
  })
})

describe('splitting unplaced events', () => {
  it('keeps the ones of the reader’s country, counts the severe others apart', () => {
    const split = splitByScope([HARZ, DODEKANISA, LIANZHOU], { lat: 51.9, lon: 10.43 }, ['DE'])
    expect(split.near.map((e) => e.id)).toEqual([HARZ.id])
    expect(split.unlocated).toBe(1) // Lianzhou: severe, no position, no country
    expect(split.severeElsewhere).toBe(0) // never "elsewhere"
    expect(split.unmatched).toBe(0)
  })

  it('while the country is unknown, nothing is matched and that is counted', () => {
    const split = splitByScope([HARZ, DODEKANISA], { lat: 51.9, lon: 10.43 }, null)
    expect(split.near).toEqual([])
    expect(split.unmatched).toBe(2)
  })
})

/** The reader's browser answers with a position (permission granted). */
function locateAt(lat: number, lon: number) {
  const watchers: PositionCallback[] = []
  Object.defineProperty(navigator, 'geolocation', {
    configurable: true,
    value: {
      watchPosition: vi.fn((ok: PositionCallback) => watchers.push(ok)),
      clearWatch: vi.fn(),
      getCurrentPosition: vi.fn(),
    },
  })
  Object.defineProperty(navigator, 'permissions', {
    configurable: true,
    value: { query: vi.fn(async () => ({ state: 'granted' })) },
  })
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  return () =>
    watchers.forEach((ok) => ok({ coords: { latitude: lat, longitude: lon, accuracy: 3000 } } as GeolocationPosition))
}

async function renderNearMe(events: SosEvent[], lat: number, lon: number) {
  const { default: App } = await import('../App')
  const fix = locateAt(lat, lon)
  useStore.getState().ingest({
    type: 'snapshot',
    server_time: new Date().toISOString(),
    events,
    stats: makeStats(),
    sources: [],
  } as ServerMessage)
  render(<App />)
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })
  act(() => fix())
}

describe('Near me, rendered', () => {
  beforeEach(() => {
    resetStore()
    localStorage.clear()
    vi.stubGlobal('WebSocket', class { close() {} })
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, headers: { get: () => null } })))
  })
  afterEach(() => vi.unstubAllGlobals())

  it('a reader in Goslar sees the Harz warning, labelled for Germany, not "nothing near you"', async () => {
    const tokyo = makeEvent({ id: 'jma:far', time: ago(5) })
    await renderNearMe([HARZ, DODEKANISA, tokyo], 51.9, 10.43)
    expect(useStore.getState().here).toEqual({ lat: 51.9, lon: 10.43 })
    const row = await screen.findByText('Kreis Harz - Bergland (Oberharz)', {}, { timeout: 3000 })
    expect(row.closest('.item')?.textContent).toContain('Germany, no exact area')
    expect(screen.queryByText(/Nothing within 1,000 km of you/)).toBeNull()
    expect(screen.queryByText(/Nothing near you/)).toBeNull()
    // Greece is not the reader's country: not in this feed
    expect(screen.queryByText('Dodekanisa Islands')).toBeNull()
  })

  it('a severe alert with no position is counted "without a location", never "elsewhere"', async () => {
    await renderNearMe([HARZ, LIANZHOU], 51.9, 10.43)
    await screen.findByText('Kreis Harz - Bergland (Oberharz)', {}, { timeout: 3000 })
    expect(screen.getByRole('button', { name: /1 severe without a location/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Nothing severe elsewhere/ })).toBeInTheDocument()
  })
})
