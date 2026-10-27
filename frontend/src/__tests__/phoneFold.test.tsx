/** The phone fold in Near me and a place (2026-10-27). With a granted
 * position, tools/responsive.py --near found NO event above the fold on
 * 280x653, 320x568, 360x640, 375x667 and 568x320: two pills beside the scope
 * switch made a three-row, 173 px bar, and the counters' long labels took
 * three lines in a 320 px card. The layout itself is measured by that tool
 * (jsdom has no layout engine); this pins what the components must render
 * for the stylesheet to do its part, and that no count lost its meaning. */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScopeBar } from '../components/ScopeBar'
import { useStore } from '../store'
import { makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage, SosEvent } from '../types'
import source from '../i18n.ts?raw'

// The App renders the map lazily and opens a websocket: neither belongs in a
// unit test.
vi.mock('../components/MapView', () => ({ MapView: () => null }))
vi.mock('../live', () => ({ connectLive: () => () => {} }))

const TOKYO = { lat: 35.68, lon: 139.69 }
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()

/** A browser that matches the phone media query (the audit6 stub). */
function stubMatchMedia(phone: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({
      matches: phone && query.includes('max-width'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  })
}

/** The reader's browser answers with a position (as in unplaced.test.tsx). */
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
    watchers.forEach((ok) =>
      ok({ coords: { latitude: lat, longitude: lon, accuracy: 3000 } } as GeolocationPosition),
    )
}

function snapshot(events: SosEvent[]) {
  useStore.getState().ingest({
    type: 'snapshot',
    server_time: new Date().toISOString(),
    events,
    stats: makeStats(),
    sources: [],
  } as ServerMessage)
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })
}

/** The App in Near me at a granted position (renderNearMe of unplaced.test.tsx). */
async function renderNearMe(events: SosEvent[], lat: number, lon: number) {
  const { default: App } = await import('../App')
  const fix = locateAt(lat, lon)
  snapshot(events)
  render(<App />)
  await settle()
  act(() => fix())
}

beforeEach(() => {
  resetStore()
  localStorage.clear()
  useStore.getState().setLang('en')
  vi.stubGlobal('WebSocket', class { close() {} })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, headers: { get: () => null } })))
})
afterEach(() => {
  vi.unstubAllGlobals()
  // jsdom has no matchMedia: put it back the way the other tests expect
  delete (window as unknown as Record<string, unknown>).matchMedia
})

describe('the scope bar: both counts in ONE control', () => {
  it('one control names both counts apart, and opens World', () => {
    useStore.setState({ scope: 'near', here: TOKYO, received: true, connected: true })
    render(<ScopeBar severeElsewhere={2} unlocated={1} />)
    expect(document.querySelectorAll('.scope-elsewhere')).toHaveLength(1)
    // heard as two counts, not one run-on
    const control = screen.getByRole('button', {
      name: /2 severe elsewhere, 1 severe without a location/,
    })
    expect(control).toHaveClass('scope-elsewhere')
    // both parts stay on screen, each with its label (the name holds them)
    expect(control.textContent).toContain('2 severe elsewhere')
    expect(control.textContent).toContain('1 severe without a location')
    fireEvent.click(control)
    expect(useStore.getState().scope).toBe('world')
  })

  it('the unlocated part keeps its "?" and its own label; "nothing elsewhere" is never amber', () => {
    useStore.setState({ scope: 'near', here: TOKYO, received: true, connected: true })
    render(<ScopeBar severeElsewhere={0} unlocated={2} />)
    const severe = document.querySelectorAll('.scope-count.has-severe')
    expect(severe).toHaveLength(1)
    expect(severe[0].textContent).toContain('2 severe without a location')
    expect(severe[0].querySelector('.state-glyph')?.textContent).toBe('?')
    // the permanent far-danger part, zero included, with its own glyph
    const counts = document.querySelectorAll('.scope-count')
    expect(counts).toHaveLength(2)
    expect(counts[0]).not.toHaveClass('has-severe')
    expect(counts[0].textContent).toContain('Nothing severe elsewhere')
    expect(counts[0].querySelector('.state-glyph')?.textContent).toBe('○')
  })

  it('with nothing unlocated, no "?" part, and the content names the control', () => {
    useStore.setState({ scope: 'near', here: TOKYO, received: true, connected: true })
    render(<ScopeBar severeElsewhere={3} unlocated={0} />)
    expect(document.querySelectorAll('.scope-count')).toHaveLength(1)
    const control = screen.getByRole('button', { name: /^3 severe elsewhere$/ })
    expect(control).not.toHaveAttribute('aria-label')
  })

  it('while the snapshot arrives, both counts are floors, in the name too', () => {
    useStore.setState({ scope: 'near', here: TOKYO, received: true, connected: true, snapshotRemaining: 2 })
    render(<ScopeBar severeElsewhere={2} unlocated={1} />)
    expect(
      screen.getByRole('button', { name: '2+ severe elsewhere, 1+ severe without a location' }),
    ).toBeInTheDocument()
  })

  it('in Japanese the separator is the ideographic comma', () => {
    useStore.getState().setLang('ja')
    useStore.setState({ scope: 'near', here: TOKYO, received: true, connected: true })
    render(<ScopeBar severeElsewhere={2} unlocated={1} />)
    expect(
      screen.getByRole('button', { name: 'ほかの地域で重大 2 件、位置不明の重大 1 件' }),
    ).toBeInTheDocument()
  })
})

describe('the counters on a phone', () => {
  it('short labels on a phone', async () => {
    stubMatchMedia(true)
    const { default: App } = await import('../App')
    render(<App />)
    await settle()
    const kpis = document.querySelector('.kpis')?.textContent ?? ''
    expect(kpis).toContain('Quakes (1 h)')
    expect(kpis).toContain('Max mag. (1 h)')
    expect(kpis).toContain('Tsunami alerts')
    expect(kpis).not.toContain('Earthquakes')
  })

  it('the full labels everywhere else', async () => {
    const { default: App } = await import('../App')
    render(<App />)
    await settle()
    const kpis = document.querySelector('.kpis')?.textContent ?? ''
    expect(kpis).toContain('Earthquakes (1 h)')
    expect(kpis).toContain('Max magnitude (1 h)')
  })
})

describe('the scope the stylesheet reads', () => {
  it('Near me: the panel is scoped and the app says so', async () => {
    await renderNearMe([makeEvent({ id: 'emsc:near', time: ago(5) })], TOKYO.lat, TOKYO.lon)
    expect(useStore.getState().here).toEqual(TOKYO)
    expect(document.querySelector('section.panel.panel-scoped')).not.toBeNull()
    expect(document.querySelector('.app')).toHaveAttribute('data-scope', 'near')
  })

  it('World: neither', async () => {
    useStore.setState({ scope: 'world' })
    snapshot([makeEvent({ id: 'emsc:world', time: ago(5) })])
    const { default: App } = await import('../App')
    render(<App />)
    await settle()
    expect(document.querySelector('section.panel')).not.toBeNull()
    expect(document.querySelector('section.panel.panel-scoped')).toBeNull()
    expect(document.querySelector('.app')).toHaveAttribute('data-scope', 'world')
  })
})

describe('the new strings exist in all five languages', () => {
  const KEYS = ['kpi.quakes.short', 'kpi.maxmag.short', 'kpi.tsunami.short', 'scope.counts.sep']
  it.each(['fr', 'en', 'es', 'ja', 'id'])('%s', (name) => {
    const block = source.match(new RegExp(`const ${name}: Dict = \\{([\\s\\S]*?)\\n\\}`))?.[1] ?? ''
    for (const key of KEYS) {
      const value = block.match(new RegExp(`^\\s*"${key.replace(/\./g, '\\.')}": "([^"]+)",$`, 'm'))
      expect(value, `${name} ${key}`).not.toBeNull()
    }
  })
})
