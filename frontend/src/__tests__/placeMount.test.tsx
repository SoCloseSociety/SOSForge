/** Where the place report is mounted: one button over the map for a place
 * the reader searched or clicked, never for the device position, only when
 * the API says it serves it, and nothing fetched until it is opened. */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ClimateButton, PlaceReportButton } from '../components/FooterTools'
import { resetGuidanceCache } from '../guidance'
import { useStore } from '../store'
import { RETRY_FIRST_MS } from '../useApiFeature'
import { resetStore } from './helpers'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

/** `GET /api/version` verbatim from the API built from 5eed58d's tree
 * (http://127.0.0.1:8341, 2026-10-27 18:25:31 GMT, application/json). */
const VERSION = {
  api: 2,
  features: [
    'ws_zones',
    'ws_out_of_scope',
    'ws_error',
    'snapshot_pinned',
    'events_zone',
    'events_cursor',
    'events_etag',
    'guidance',
    'escalated',
    'snapshot_chunked',
    'tick_lite',
    'climate',
    'place',
  ],
  app: '1.0.0',
}
/** The same API with `SOS_ENABLE_PLACE=false` (backend/app/main.py: the
 * flag drops "place" from the list and nothing else). */
const VERSION_NO_PLACE = { ...VERSION, features: VERSION.features.filter((f) => f !== 'place') }

/** What /api/version answers: a response, or a dropped request. */
let version: { status?: number; body: unknown } | 'dropped' = { body: VERSION }
const fetchMock = vi.fn(async (url: string) => {
  if (url === '/api/version') {
    if (version === 'dropped') throw new TypeError('Failed to fetch')
    const { status = 200, body } = version
    return { ok: status < 400, status, json: async () => body, headers: { get: () => 'application/json' } }
  }
  return { ok: false, status: 404, json: async () => ({}), headers: { get: () => null } }
})
const placeCalls = () =>
  fetchMock.mock.calls.filter((call) => String((call as unknown[])[0]).includes('/api/place')).length
const versionCalls = () => fetchMock.mock.calls.filter((call) => (call as unknown[])[0] === '/api/version').length

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
  resetGuidanceCache()
  version = { body: VERSION }
  fetchMock.mockClear()
  vi.stubGlobal('WebSocket', class { close() {} })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('the place report in the app', () => {
  it('a searched place gets one button, and nothing is fetched before it is opened', async () => {
    useStore.getState().setPlace({ lat: 9.51, lon: 100.01, name: 'Ko Samui', country: 'TH' })
    await app()
    const buttons = [...document.querySelectorAll<HTMLButtonElement>('.map-report-open')]
    expect(buttons).toHaveLength(1)
    expect(buttons[0].getAttribute('aria-haspopup')).toBe('dialog')
    // a short label on the map, the whole description as its name
    expect(buttons[0].textContent?.trim()).toBe('☁ Weather, sea, air, risks')
    expect(screen.getByRole('button', { name: 'Weather, sea, air, risks and history: Ko Samui' })).toBe(buttons[0])
    // the first thing in .body: Tab reaches it before the search and the feed
    const holder = buttons[0].parentElement
    expect(holder?.classList.contains('map-report')).toBe(true)
    expect(holder?.parentElement?.classList.contains('body')).toBe(true)
    expect(holder?.parentElement?.firstElementChild).toBe(holder)
    expect(placeCalls()).toBe(0)
  })

  it('Near me, on the device position, has no place report at all', async () => {
    // a real fix through the app's own Near me hook: set by hand, `here` is
    // cleared on mount (no geolocation in jsdom) and the view is World
    const fixes: PositionCallback[] = []
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { watchPosition: (cb: PositionCallback) => fixes.push(cb), clearWatch: vi.fn() },
    })
    Object.defineProperty(navigator, 'permissions', { configurable: true, value: { query: async () => ({ state: 'granted' }) } })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    useStore.getState().setScope('near')
    await app()
    act(() => fixes[0]({ coords: { latitude: 48.85, longitude: 2.35 } } as GeolocationPosition))
    expect(useStore.getState().locate).toBe('ok')
    expect(useStore.getState().here).toEqual({ lat: 48.85, lon: 2.35 })
    expect(useStore.getState().scope).toBe('near')
    expect(document.querySelector('.map-report')).toBeNull()
    expect(placeCalls()).toBe(0)
  })

  it('the world view has none either', async () => {
    useStore.getState().setScope('world')
    await app()
    expect(document.querySelector('.map-report')).toBeNull()
  })

  it('an API that does not list "place" gets no button, and is never asked for a report', async () => {
    version = { body: VERSION_NO_PLACE }
    useStore.getState().setPlace({ lat: 9.51, lon: 100.01, name: 'Ko Samui', country: 'TH' })
    await app()
    expect(versionCalls()).toBeGreaterThan(0)
    expect(document.querySelector('.map-report')).toBeNull()
    expect(screen.queryByRole('button', { name: /Weather, sea, air, risks/ })).toBeNull()
    expect(placeCalls()).toBe(0)
  })

  it('an older API without /api/version gets no button either', async () => {
    // FastAPI's own 404 for a route it does not have
    version = { status: 404, body: { detail: 'Not Found' } }
    useStore.getState().setPlace({ lat: 9.51, lon: 100.01, name: 'Ko Samui', country: 'TH' })
    await app()
    expect(document.querySelector('.map-report')).toBeNull()
  })

  it('a dropped version check does not hide the button for good: it is asked again', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    version = 'dropped'
    useStore.getState().setPlace({ lat: 9.51, lon: 100.01, name: 'Ko Samui', country: 'TH' })
    await app()
    expect(document.querySelector('.map-report')).toBeNull()
    const asked = versionCalls()
    expect(asked).toBeGreaterThan(0)

    version = { body: VERSION }
    await act(async () => {
      vi.advanceTimersByTime(RETRY_FIRST_MS)
      for (let i = 0; i < 10; i += 1) await Promise.resolve()
    })
    expect(versionCalls()).toBeGreaterThan(asked)
    expect(document.querySelectorAll('.map-report-open')).toHaveLength(1)
    expect(placeCalls()).toBe(0)
  })
})

describe('PlaceReportButton', () => {
  it('opens the panel in a dialog with the place it was given, and closes on Escape', async () => {
    const seen: unknown[] = []
    const Panel = (props: { lat: number; lon: number; name: string }) => {
      seen.push(props)
      return <p>panel for {props.name}</p>
    }
    const load = vi.fn(async () => ({ default: Panel }))
    render(<PlaceReportButton lat={-20.88} lon={55.45} name="Saint-Denis" load={load as never} />)
    expect(load).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Weather, sea, air, risks and history: Saint-Denis' }))
    expect(await screen.findByText('panel for Saint-Denis')).toBeTruthy()
    expect(seen.at(-1)).toEqual({ lat: -20.88, lon: 55.45, name: 'Saint-Denis' })
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Place report')
    expect(screen.getByRole('button', { name: 'Close the place report' })).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows the icon alone where its label would wrap, and the label again where it fits', () => {
    // jsdom has no layout: the label's line height from a sheet, its height
    // from the number of lines it would take
    const sheet = document.createElement('style')
    sheet.textContent = 'button > span { line-height: 18px }'
    document.head.append(sheet)
    let lines = 2
    const real = HTMLElement.prototype.getBoundingClientRect
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const label = this.tagName === 'SPAN' && !this.getAttribute('aria-hidden')
      const height = lines * 18
      return label ? ({ x: 0, y: 0, top: 0, left: 0, right: 100, bottom: height, width: 100, height } as DOMRect) : real.call(this)
    })
    try {
      render(<PlaceReportButton lat={9.51} lon={100.01} name="Ko Samui" />)
      const name = 'Weather, sea, air, risks and history: Ko Samui'
      const button = screen.getByRole('button', { name })
      expect(button.classList.contains('icon-only')).toBe(true)
      const label = [...button.querySelectorAll('span')].find((el) => el.textContent === 'Weather, sea, air, risks')
      expect(label?.hidden).toBe(true)
      // the whole description stays its name, and is its tooltip
      expect(button.getAttribute('title')).toBe(name)

      // turned to landscape: room for one line
      lines = 1
      act(() => {
        window.dispatchEvent(new Event('resize'))
      })
      expect(button.classList.contains('icon-only')).toBe(false)
      expect(label?.hidden).toBe(false)
      expect(button.getAttribute('title')).toBeNull()

      // the footer's tools wrap on purpose (.footer-tool): never icon-only
      lines = 2
      render(<ClimateButton />)
      expect(screen.getByRole('button', { name: /Climate indicators/ }).classList.contains('icon-only')).toBe(false)
    } finally {
      sheet.remove()
    }
  })

  it('a chunk that fails says so in a sentence, with reload and retry', async () => {
    const load = vi.fn(async () => {
      throw new Error('https://sosforge.soclose.co/assets/PlaceReport-old.js 404')
    })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<PlaceReportButton lat={1} lon={2} name="X" load={load as never} />)
    fireEvent.click(screen.getByRole('button', { name: 'Weather, sea, air, risks and history: X' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('The place report could not be loaded.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})
