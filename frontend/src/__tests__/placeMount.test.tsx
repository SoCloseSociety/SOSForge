/** Where the place report is mounted: one button over the map for a place
 * the reader searched or clicked, never for the device position, and
 * nothing fetched until it is opened. */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ClimateButton, PlaceReportButton } from '../components/FooterTools'
import { useStore } from '../store'
import { resetStore } from './helpers'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

const fetchMock = vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}), headers: { get: () => null } }))
const placeCalls = () =>
  fetchMock.mock.calls.filter((call) => String((call as unknown[])[0]).includes('/api/place')).length

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
  fetchMock.mockClear()
  vi.stubGlobal('WebSocket', class { close() {} })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
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
