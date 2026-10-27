/** Final review of PR #5 (2026-10-27): lane H's findings, each reproduced
 * first. Numbers are the review's. */
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { countriesAt, countriesIn, decode, type CountryTable } from '../countries'
import table from '../data/countries.json'
import { ClimateButton, focusables } from '../components/FooterTools'
import { SearchBar } from '../components/SearchBar'
import { SourcesBar } from '../components/SourcesBar'
import { splitByScope } from '../scope'
import { useStore } from '../store'
import { useCountries } from '../useCountries'
import { useNearMe } from '../useNearMe'
import { makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage, SosEvent, SourceHealth } from '../types'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()
const TOKYO = { lat: 35.68, lon: 139.76, name: 'Tokyo' }
const shapes = decode(table as CountryTable)

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

describe('#0 (#4 again): what the banner names is counted, whatever the window', () => {
  it('a BMKG tsunami-potential quake 30 min old, not "ongoing", under the Live window', async () => {
    // regional.py: tsunami=True forces EXTREME; ongoing stays at its default
    const bmkg = makeEvent({
      id: 'bmkg:20261027',
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
    useStore.getState().setPlace(TOKYO)
    useStore.getState().setWindow(15)
    useStore.setState({ connected: true })
    await app()
    expect(screen.getByRole('alert').textContent).toContain('TSUNAMI')
    expect(document.querySelector('.scope-elsewhere')?.textContent).toContain('1 severe elsewhere')
  })
})

describe('#2: a warning in force near the place is in the feed, whatever its onset', () => {
  it('the WMO storm for Guayas, issued two days ago and in force, 45 km away', async () => {
    const guayas = makeEvent({
      id: 'wmo:guayas',
      source: 'wmo',
      kind: 'storm',
      severity: 'severe',
      ongoing: true,
      magnitude: null,
      place: 'Guayas, El Oro',
      lat: -2.59,
      lon: -79.9,
      time: '2026-10-25T18:45:00Z',
      expires: '2099-10-29T04:00:00Z',
    })
    feed([guayas])
    useStore.getState().setPlace({ lat: -2.19, lon: -79.89, name: 'Guayaquil', country: 'EC' })
    await app()
    expect(document.querySelector('.feed .item')?.textContent).toContain('Guayas, El Oro')
  })
})

describe('#1: no source is "online" on the word of a dead link', () => {
  it('the KPI and every source button say the state is not known', async () => {
    const sources: SourceHealth[] = [
      { name: 'emsc', connected: true, last_ok: ago(1), last_error: null, events_seen: 3, errors: 0 },
    ]
    useStore.setState({ received: true, connected: false, sources, scope: 'world' })
    await app()
    expect(document.querySelector('.kpis')?.textContent).toContain('last known, reconnecting')
    expect(screen.getAllByRole('button', { name: /EMSC/ })[0]).toHaveAccessibleName(
      'EMSC (push), state not known while reconnecting',
    )
    expect(screen.queryByRole('button', { name: /EMSC.*, online/ })).toBeNull()
  })
})

describe('#3: the climate dialog traps Tab among what can be reached', () => {
  it('a link inside a hidden detail list is not a stop', () => {
    const root = document.createElement('div')
    root.innerHTML =
      '<button>Close</button><button>Show more</button><dl hidden><dd><a href="/x">data</a></dd></dl>' +
      '<details><summary>More</summary><a href="/y">inside</a></details>'
    document.body.append(root)
    expect(focusables(root).map((el) => el.textContent)).toEqual(['Close', 'Show more', 'More'])
    root.remove()
  })
})

describe('#4: a failed climate chunk stays inside its dialog', () => {
  it('says so, offers a retry, and the tracker keeps running', async () => {
    let fail = true
    const load = () =>
      fail
        ? Promise.reject(new Error('Failed to fetch dynamically imported module: /assets/ClimatePanel-old.js'))
        : Promise.resolve({ default: () => <p>panel here</p> })
    render(
      <>
        <p>live feed</p>
        <ClimateButton load={load as never} />
      </>,
    )
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    fireEvent.click(screen.getByRole('button', { name: /Climate indicators/ }))
    expect(
      await screen.findByText('The climate indicators could not be loaded. Reload the page to get the current version.'),
    ).toBeInTheDocument()
    expect(screen.getByText('live feed')).toBeInTheDocument()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('panel here')).toBeInTheDocument()
    spy.mockRestore()
  })
})

describe('#6: a searched place keeps the geocoder country', () => {
  it('"Indonesia", put at sea by Nominatim, still gets the ID alerts with no position', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          results: [{ name: 'Indonesia', lat: -2.4834, lon: 117.8903, bbox: [-11, 6, 94, 141], country_code: 'ID' }],
        }),
      })),
    )
    render(<SearchBar />)
    const box = screen.getByRole('combobox')
    fireEvent.change(box, { target: { value: 'indonesia' } })
    await screen.findByRole('listbox', {}, { timeout: 3000 })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(useStore.getState().place).toMatchObject({ name: 'Indonesia', country: 'ID' })
    // the table alone finds no land there
    expect(countriesIn(shapes, -2.4834, 117.8903)).toEqual([])
    const { result } = renderHook(() => useCountries({ lat: -2.4834, lon: 117.8903 }, 'ID'))
    expect(result.current).toContain('ID')
  })
})

describe('#7, #13, #14: the countries table, 1:10m, holes kept, overseas apart', () => {
  it('islands and enclaves the 1:50m table missed or misfiled', () => {
    expect(countriesIn(shapes, 53.59, 6.67)).toContain('DE') // Borkum
    expect(countriesIn(shapes, 35.29, -2.94)).toContain('ES') // Melilla
    expect(countriesIn(shapes, 37.32, 26.55)).toEqual(['GR']) // Patmos
    expect(countriesIn(shapes, 38.47, 14.95)).toEqual(['IT']) // Lipari
    expect(countriesIn(shapes, 35.51, 12.6)).toEqual(['IT']) // Lampedusa
    expect(countriesIn(shapes, 54.18, 7.89)).toEqual(['DE']) // Helgoland
    expect(countriesIn(shapes, 49.92, -6.3)).toEqual(['GB']) // Scilly
    expect(countriesIn(shapes, 33.07, -16.34)).toEqual(['PT']) // Porto Santo
    expect(countriesIn(shapes, 40.9, 12.96)).toEqual(['IT']) // Ponza: not drawn, 35 km off Italy
  })

  it('the overseas departments are not metropolitan France (#13)', () => {
    expect(countriesIn(shapes, -20.88, 55.45)).toEqual(['RE']) // Saint-Denis
    expect(countriesIn(shapes, 14.61, -61.07)).toEqual(['MQ']) // Fort-de-France
    expect(countriesIn(shapes, 4.93, -52.33)).toEqual(['GF']) // Cayenne
    expect(countriesIn(shapes, -12.78, 45.23)).toEqual(['YT']) // Mamoudzou
    expect(countriesIn(shapes, 12.15, -68.27)).toEqual(['BQ']) // Kralendijk
  })

  it('central Lesotho is Lesotho only: the hole in South Africa is kept (#14)', () => {
    expect(countriesIn(shapes, -29.6, 28.3)).toEqual(['LS'])
  })

  it('no country found: the alerts without a position are counted as not matched', () => {
    const dodecanese = makeEvent({ id: 'ma:gr', lat: null, lon: null, country_code: 'GR', severity: 'moderate' })
    expect(countriesIn(shapes, 30, -40)).toEqual([]) // open Atlantic
    expect(splitByScope([dodecanese], { lat: 30, lon: -40 }, []).unmatched).toBe(1)
  })
})

describe('#8: the scoped feed does not vouch for the area while the link is down', () => {
  it('says the connection is lost above the nearest events', async () => {
    feed([makeEvent({ id: 'far', lat: 48.85, lon: 2.35, time: ago(5) })])
    useStore.getState().setPlace(TOKYO)
    useStore.setState({ connected: false })
    await app()
    expect(screen.getByText('Connection lost: the nearest events received before the cut:')).toBeInTheDocument()
    expect(screen.queryByText(/Nothing within 1,000 km/)).toBeNull()
  })
})

describe('#9: the filter dialog counts what the feed will show', () => {
  it('the nearest-events fallback: "Show 2 events", not 0', async () => {
    feed([
      makeEvent({ id: 'a', lat: 48.85, lon: 2.35, time: ago(5) }),
      makeEvent({ id: 'b', lat: 40.4, lon: -3.7, time: ago(6) }),
    ])
    useStore.getState().setPlace(TOKYO)
    useStore.setState({ connected: true })
    await app()
    fireEvent.click(screen.getByRole('button', { name: /^Filters/ }))
    expect(screen.getByRole('button', { name: 'Show 2 events' })).toBeInTheDocument()
  })
})

describe('#16: once the table is loaded, the country follows the reader without a gap', () => {
  it('a 1 km step returns the country in the SAME render', async () => {
    await countriesAt(51.9, 10.43) // the table is loaded
    const { result, rerender } = renderHook(({ c }) => useCountries(c), { initialProps: { c: { lat: 51.9, lon: 10.43 } } })
    await act(async () => {})
    expect(result.current).toEqual(['DE'])
    rerender({ c: { lat: 51.91, lon: 10.44 } })
    expect(result.current).toEqual(['DE'])
  })
})

describe('#17: pressing Near me again keeps the good fix', () => {
  it('a slow fresh fix does not flip the view to World', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const ok: PositionCallback[] = []
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { watchPosition: (cb: PositionCallback) => ok.push(cb), clearWatch: vi.fn() },
    })
    Object.defineProperty(navigator, 'permissions', { configurable: true, value: { query: async () => ({ state: 'granted' }) } })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    renderHook(() => useNearMe())
    await act(async () => {
      for (let i = 0; i < 5; i += 1) await Promise.resolve()
    })
    act(() => ok[0]({ coords: { latitude: 35.68, longitude: 139.69 } } as GeolocationPosition))
    expect(useStore.getState().locate).toBe('ok')
    act(() => useStore.getState().requestNearMe()) // re-centre
    await act(async () => {
      for (let i = 0; i < 5; i += 1) await Promise.resolve()
    })
    expect(useStore.getState().locate).toBe('ok')
    act(() => {
      vi.advanceTimersByTime(20_000) // no fresh fix comes
    })
    expect(useStore.getState().here).toEqual({ lat: 35.68, lon: 139.69 })
    expect(useStore.getState().locate).toBe('ok')
  })
})

describe('the source detail, link down', () => {
  it('says "not known" in its head too', () => {
    useStore.setState({
      received: true,
      connected: false,
      sources: [{ name: 'noa', connected: false, last_ok: ago(1), last_error: 'HTTP 503', events_seen: 1, errors: 1 }],
    })
    render(<SourcesBar now={Date.now()} />)
    fireEvent.click(screen.getByRole('button', { name: /NOA/ }))
    expect(document.querySelector('.source-detail-head')?.textContent).toContain('state not known while reconnecting')
  })
})
