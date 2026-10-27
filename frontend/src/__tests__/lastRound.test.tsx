/** Recheck of PR #5 (2026-10-27), the last round: regressions in lane H's
 * fixes, each reproduced first. */
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ClimateButton, OfflineMapButton, STALL_MS, STATUS_DELAY_MS, type BasemapStatus } from '../components/FooterTools'
import { countriesAt } from '../countries'
import { useStore } from '../store'
import { useCountries } from '../useCountries'
import { makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage, SosEvent } from '../types'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()
const ahead = (min: number) => new Date(Date.now() + min * 60_000).toISOString()

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
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
}

beforeEach(() => {
  resetStore()
  localStorage.clear()
  vi.stubGlobal('WebSocket', class { close() {} })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}), headers: { get: () => null } })))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('(1) a territory searched by name keeps its own code, not the parent state', () => {
  it('Nominatim says FR for Reunion and Martinique, NL for Bonaire', async () => {
    await countriesAt(0, 0) // the table is loaded
    const at = (lat: number, lon: number, known: string) =>
      renderHook(() => useCountries({ lat, lon }, known)).result.current
    expect(at(-20.88, 55.45, 'FR')).toEqual(['RE']) // Saint-Denis
    expect(at(14.61, -61.07, 'FR')).toEqual(['MQ']) // Fort-de-France
    expect(at(4.93, -52.33, 'FR')).toEqual(['GF']) // Cayenne
    expect(at(12.15, -68.27, 'NL')).toEqual(['BQ']) // Kralendijk
    expect(at(48.85, 2.35, 'FR')).toEqual(['FR']) // Paris stays France
  })
})

describe('(2) "in force" is bounded, and the fallback keeps to its window', () => {
  it('an EONET fire "open" but last observed 12 days ago is not listed near the reader', async () => {
    const oldFire = makeEvent({
      id: 'eonet:fire',
      source: 'eonet',
      kind: 'wildfire',
      severity: 'moderate',
      ongoing: true,
      expires: null,
      updated_at: null,
      magnitude: null,
      place: 'Old fire',
      lat: 35.5,
      lon: 139.5,
      time: ago(12 * 24 * 60),
    })
    const warning = makeEvent({
      id: 'wmo:storm',
      source: 'wmo',
      kind: 'storm',
      severity: 'severe',
      ongoing: true,
      expires: ahead(600),
      magnitude: null,
      place: 'Storm in force',
      lat: 35.6,
      lon: 139.6,
      time: ago(3 * 24 * 60),
    })
    feed([oldFire, warning])
    useStore.getState().setPlace({ lat: 35.68, lon: 139.76, name: 'Tokyo', country: 'JP' })
    useStore.setState({ connected: true })
    await app()
    const places = [...document.querySelectorAll('.feed .item .place')].map((el) => el.textContent ?? '')
    expect(places.some((p) => p.includes('Storm in force'))).toBe(true)
    expect(places.some((p) => p.includes('Old fire'))).toBe(false)
  })

  it('nothing near: the nearest list holds events of the window, not every alert in force', async () => {
    const chile = makeEvent({
      id: 'wmo:chile',
      source: 'wmo',
      kind: 'storm',
      severity: 'severe',
      ongoing: true,
      expires: ahead(600),
      magnitude: null,
      place: 'Chile storm in force',
      lat: -33,
      lon: -71,
      time: ago(3 * 24 * 60),
    })
    const paris = makeEvent({ id: 'emsc:paris', place: 'Recent quake', lat: 48.85, lon: 2.35, time: ago(30) })
    feed([chile, paris])
    useStore.getState().setPlace({ lat: 35.68, lon: 139.76, name: 'Tokyo', country: 'JP' })
    useStore.setState({ connected: true })
    await app()
    const places = [...document.querySelectorAll('.feed .item .place')].map((el) => el.textContent ?? '')
    expect(places).toHaveLength(1)
    expect(places[0]).toContain('Recent quake')
    // and the far alert in force is still COUNTED
    expect(document.querySelector('.scope-elsewhere')?.textContent).toContain('1 severe elsewhere')
  })

  it('the place AT the tsunami (Padang, Live window): the feed lists what the banner names', async () => {
    const bmkg = makeEvent({
      id: 'bmkg:padang',
      source: 'bmkg',
      severity: 'extreme',
      tsunami: true,
      ongoing: false,
      magnitude: 7.1,
      place: 'Pantai Barat Sumatera',
      lat: -0.95,
      lon: 99.9,
      time: ago(30),
    })
    feed([bmkg])
    useStore.getState().setPlace({ lat: -0.95, lon: 100.35, name: 'Padang', country: 'ID' })
    useStore.getState().setWindow(15)
    useStore.setState({ connected: true })
    await app()
    expect(screen.getByRole('alert').textContent).toContain('TSUNAMI')
    expect(document.querySelector('.feed .item')?.textContent).toContain('Pantai Barat Sumatera')
    expect(screen.queryByText(/Nothing in this window/)).toBeNull()
  })
})

describe('(3) a failed climate chunk: a sentence, reload first, the raw text behind "Show more"', () => {
  it('the asset URL is not on the card until asked for', async () => {
    const url = '/assets/ClimatePanel-Bq3xZ9aLongHashThatDoesNotBreak.js'
    const load = () => Promise.reject(new Error(`Failed to fetch dynamically imported module: ${url}`))
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    render(<ClimateButton load={load as never} />)
    fireEvent.click(screen.getByRole('button', { name: /Climate indicators/ }))
    await screen.findByRole('button', { name: 'Reload the page' })
    const more = document.querySelector('.climate-failed-more') as HTMLDetailsElement
    expect(more.open).toBe(false)
    expect(more.querySelector('code')?.textContent).toContain(url)
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    spy.mockRestore()
  })
})

const MISSING: BasemapStatus = {
  type: 'sosforge:basemap-status',
  saved: false,
  saving: false,
  files: 132,
  missingFiles: 118,
  missingBytes: 15_662_270,
  unmeasuredFiles: 4,
  unmetered: null,
}

function worker(reply: ((message: { type: string }) => unknown) | null) {
  const events = new EventTarget()
  const sent: string[] = []
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: Object.assign(events, {
      controller: {
        postMessage: (message: { type: string }, ports?: MessagePort[]) => {
          sent.push(message.type)
          const answer = reply?.(message)
          if (answer && ports?.[0]) ports[0].postMessage(answer)
        },
      },
    }),
  })
  const broadcast = (data: unknown) => act(() => void events.dispatchEvent(new MessageEvent('message', { data })))
  return { sent, broadcast }
}

const statuses = (sent: string[]) => sent.filter((t) => t === 'sosforge:basemap-status').length

describe('(4) one status question in flight, and none by itself on Save-Data', () => {
  it('a worker that does not answer is not asked again before STALL_MS', async () => {
    const w = worker(null) // never answers
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    render(<OfflineMapButton />)
    act(() => useStore.setState({ received: true, snapshotRemaining: 0 }))
    act(() => void vi.advanceTimersByTime(STATUS_DELAY_MS))
    expect(statuses(w.sent)).toBe(1)
    w.broadcast({ type: 'sosforge:basemap-failed', files: 1, failed: 1, bytes: 0 })
    w.broadcast({ type: 'sosforge:basemap-failed', files: 1, failed: 1, bytes: 0 })
    expect(statuses(w.sent)).toBe(1)
  })

  it('on Save-Data: no stall poll; pressing the button while it saves is the question', async () => {
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } })
    let saving = false
    const w = worker((m) => {
      if (m.type === 'sosforge:basemap-save') saving = true
      return m.type === 'sosforge:basemap-status' ? { ...MISSING, saving } : null
    })
    act(() => useStore.setState({ received: true, snapshotRemaining: 0 }))
    render(<OfflineMapButton />)
    const button = screen.getByRole('button', { name: 'Save the world map for offline use' })
    fireEvent.click(button) // ask
    await screen.findByRole('button', { name: /~16 MB/ })
    fireEvent.click(button) // save
    expect(w.sent).toContain('sosforge:basemap-save')
    const before = statuses(w.sent)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    act(() => void vi.advanceTimersByTime(STALL_MS * 4))
    vi.useRealTimers()
    expect(statuses(w.sent)).toBe(before)
    fireEvent.click(button) // "how does it stand?"
    expect(statuses(w.sent)).toBe(before + 1)
    Object.defineProperty(navigator, 'connection', { configurable: true, value: undefined })
  })
})

describe('(5) on Save-Data the first press does not take the focus away', () => {
  it('the same button asks, then offers the size, and keeps the focus', async () => {
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { effectiveType: '2g' } })
    worker((m) => (m.type === 'sosforge:basemap-status' ? MISSING : null))
    act(() => useStore.setState({ received: true, snapshotRemaining: 0 }))
    render(<OfflineMapButton />)
    const button = screen.getByRole('button', { name: 'Save the world map for offline use' })
    button.focus()
    fireEvent.click(button)
    const sized = await screen.findByRole('button', { name: /~16 MB/ })
    expect(sized).toBe(button)
    expect(document.activeElement).toBe(button)
    Object.defineProperty(navigator, 'connection', { configurable: true, value: undefined })
  })
})
