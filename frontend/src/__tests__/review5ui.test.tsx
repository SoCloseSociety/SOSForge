/** Review of PR #5 (adversarial, 2026-10-27): the component findings,
 * each reproduced first. Numbers are the review's. */
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ActiveFilters, FilterSheet } from '../components/FilterSheet'
import { ScopeBar } from '../components/ScopeBar'
import { SearchBar } from '../components/SearchBar'
import { SourcesBar } from '../components/SourcesBar'
import { WatchCompact, WatchPanel } from '../components/WatchPanel'
import { emptyReason } from '../emptyReason'
import { useStore } from '../store'
import { useNearMe } from '../useNearMe'
import { makeEvent, resetStore } from './helpers'
import type { SourceHealth } from '../types'

const TOKYO = { lat: 35.68, lon: 139.69 }

beforeEach(() => {
  resetStore()
  localStorage.clear()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const source = (name: string, extra: Partial<SourceHealth> = {}): SourceHealth => ({
  name,
  connected: true,
  last_ok: new Date(Date.now() - 30_000).toISOString(),
  last_error: null,
  events_seen: 10,
  errors: 0,
  ...extra,
})

describe('#8, #9: the filter dialog never states a count it does not have', () => {
  it('before the feed has spoken: no "Show 0 events", no chip claiming "none"', () => {
    render(<FilterSheet events={[]} resultCount={0} phone={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    expect(screen.getByRole('button', { name: 'Show the events' })).toBeInTheDocument()
    expect(screen.queryByText(/Show 0 events/)).toBeNull()
    expect(document.querySelectorAll('.filter-kinds .chip.empty')).toHaveLength(0)
  })

  it('while the snapshot arrives: a floor, "37+"', () => {
    useStore.setState({ received: true, snapshotRemaining: 200 })
    render(<FilterSheet events={[]} resultCount={37} phone={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    expect(screen.getByRole('button', { name: 'Show 37+ events' })).toBeInTheDocument()
    expect(document.querySelectorAll('.filter-kinds .chip.empty')).toHaveLength(0)
  })
})

describe('#24: Esc closes the dialog from anywhere', () => {
  it('after "Show all" unmounted itself under the focus', () => {
    useStore.getState().toggleKind('tsunami')
    render(<FilterSheet events={[]} resultCount={3} phone={false} />)
    fireEvent.click(screen.getByRole('button', { name: /^Filters/ }))
    const all = screen.getByRole('button', { name: 'Show all' })
    all.focus()
    fireEvent.click(all)
    // the focus stayed inside the dialog
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
    // and Esc pressed on the document still closes it
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('#25: a control that removes itself hands the focus on', () => {
  it('removing the last active-filter chip focuses the Filters button', () => {
    useStore.getState().setMinSeverity('severe')
    render(
      <>
        <FilterSheet events={[]} resultCount={3} phone={false} />
        <ActiveFilters phone={false} />
      </>,
    )
    const chip = screen.getByRole('button', { name: /Remove/ })
    chip.focus()
    fireEvent.click(chip)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /Filters/ }))
  })

  it('"Hide" in a source detail gives the focus back to that source', () => {
    useStore.setState({
      received: true,
      connected: true,
      sources: [source('noa', { connected: false, last_error: 'HTTP 503 from https://www.gein.noa.gr/' })],
    })
    render(<SourcesBar now={Date.now()} />)
    const button = screen.getByRole('button', { name: /NOA \(Greece\), offline/ })
    fireEvent.click(button)
    const hide = screen.getByRole('button', { name: 'Show less' })
    hide.focus()
    fireEvent.click(hide)
    expect(document.activeElement).toBe(button)
  })
})

describe('#10: failed feeds are read in the server shape', () => {
  it('names the feed and its error, never "[object Object]"', () => {
    useStore.setState({
      received: true,
      connected: true,
      // verbatim shape of sources/base.py: {"feed": ..., "error": ...}
      sources: [source('tsunami', { degraded: true, failed_feeds: [{ feed: 'PAAQ', error: 'TimeoutError: ' }] })],
    })
    render(<SourcesBar now={Date.now()} />)
    fireEvent.click(screen.getByRole('button', { name: /NOAA tsunami/ }))
    const detail = document.querySelector('.source-detail')?.textContent ?? ''
    expect(detail).not.toContain('[object Object]')
    expect(detail).toContain('PAAQ')
    expect(detail).toContain('TimeoutError:')
  })
})

describe('#26: a drag released off the strip is over', () => {
  it('a hovering mouse neither scrolls the row nor swallows the next click', () => {
    useStore.setState({ received: true, connected: true, sources: [source('emsc'), source('usgs')] })
    render(<SourcesBar now={Date.now()} />)
    const strip = document.querySelector('.sources-strip') as HTMLDivElement
    fireEvent.pointerDown(strip, { pointerType: 'mouse', button: 0, clientX: 100, pointerId: 1 })
    // released elsewhere: no pointerup reaches the strip; later, a hover
    fireEvent.pointerMove(strip, { pointerType: 'mouse', buttons: 0, clientX: 160, pointerId: 1 })
    expect(strip.scrollLeft).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: /EMSC/ }))
    expect(screen.getByRole('button', { name: /EMSC/ })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('#12, #13: no all-clear while the link is down', () => {
  it('the watch line, the elsewhere count and the footer say "reconnecting"', () => {
    useStore.setState({
      received: true,
      connected: false,
      snapshotRemaining: 0,
      scope: 'near',
      here: TOKYO,
      watch: { ...TOKYO, name: 'Tokyo', kinds: ['earthquake'] },
      sources: [source('emsc'), source('usgs')],
      clients: 4,
    })
    render(
      <>
        <WatchCompact alarms={[]} />
        <ScopeBar severeElsewhere={0} />
        <SourcesBar now={Date.now()} />
      </>,
    )
    expect(screen.queryByText('Nothing concerns this place')).toBeNull()
    expect(screen.getByText('Not checked: reconnecting')).toBeInTheDocument()
    expect(screen.queryByText(/Nothing severe elsewhere/)).toBeNull()
    expect(screen.getByText('Elsewhere: reconnecting')).toBeInTheDocument()
    expect(screen.getByText('Reconnecting -- last known: 2/2 online')).toBeInTheDocument()
  })
})

describe('#14, #15: an empty feed blames the severity floor when it is the cause', () => {
  it('only minor and moderate events, floor Severe', () => {
    const events = [makeEvent({ severity: 'minor' }), makeEvent({ id: 'b', severity: 'moderate' })]
    const filters = { ...useStore.getState().filters, minSeverity: 'severe' as const }
    expect(emptyReason({ events, visible: [], filters, received: true })).toBe('filters.empty.severity')
    // a severe one older than the window: widening the period is the fix
    const older = [...events, makeEvent({ id: 'c', severity: 'severe' })]
    expect(emptyReason({ events: older, visible: [], filters, received: true })).toBe('filters.empty.window')
  })
})

describe('#11: World forgets the fix it no longer follows', () => {
  it('"Use my location" from World asks for a fresh position', () => {
    const ask = vi.fn()
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { getCurrentPosition: ask, watchPosition: vi.fn(), clearWatch: vi.fn() },
    })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    useStore.setState({ scope: 'near', locate: 'ok', here: TOKYO })
    useStore.getState().setScope('world')
    expect(useStore.getState().here).toBeNull()
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /Use my location/ }))
    expect(ask).toHaveBeenCalledTimes(1)
  })
})

describe('#20: one transient "position unavailable" keeps the fix', () => {
  it('Near me stays near', async () => {
    const watchers: { ok: PositionCallback; fail?: PositionErrorCallback }[] = []
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        watchPosition: (ok: PositionCallback, fail?: PositionErrorCallback) => watchers.push({ ok, fail }),
        clearWatch: vi.fn(),
      },
    })
    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: { query: async () => ({ state: 'granted' }) },
    })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    renderHook(() => useNearMe())
    await act(async () => {
      for (let i = 0; i < 5; i += 1) await Promise.resolve()
    })
    act(() => watchers.forEach((w) => w.ok({ coords: { latitude: 35.68, longitude: 139.69 } } as GeolocationPosition)))
    act(() =>
      watchers.forEach((w) =>
        w.fail?.({ code: 2, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: '' } as GeolocationPositionError),
      ),
    )
    expect(useStore.getState().here).toEqual(TOKYO)
    expect(useStore.getState().locate).toBe('ok')
  })
})

describe('#27: a late position does not overwrite a watch armed since', () => {
  it('Lyon, armed after the timeout, stays', () => {
    vi.useFakeTimers()
    let answer: PositionCallback = () => undefined
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { getCurrentPosition: (ok: PositionCallback) => (answer = ok), watchPosition: vi.fn(), clearWatch: vi.fn() },
    })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /Use my location/ }))
    act(() => vi.advanceTimersByTime(20_000)) // our own guard gives up
    const lyon = { lat: 45.76, lon: 4.84, name: 'Lyon', kinds: ['flood' as const] }
    act(() => useStore.getState().setWatch(lyon))
    act(() => answer({ coords: { latitude: 48.85, longitude: 2.35 } } as GeolocationPosition))
    expect(useStore.getState().watch).toEqual(lyon)
  })
})

describe('#30: a search does not move an armed watch', () => {
  it('Hilo stays watched while the reader looks Tokyo up', async () => {
    const hilo = { lat: 19.72, lon: -155.08, name: 'Hilo', kinds: ['tsunami' as const] }
    useStore.setState({ watch: hilo })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ results: [{ name: 'Tokyo, Japan', lat: 35.68, lon: 139.76, bbox: [35.5, 35.9, 139.5, 139.9] }] }),
      })),
    )
    render(<SearchBar />)
    const box = screen.getByRole('combobox')
    fireEvent.change(box, { target: { value: 'tokyo' } })
    await screen.findByRole('listbox', {}, { timeout: 3000 })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(useStore.getState().watch).toEqual(hilo)
    expect(useStore.getState().place).toMatchObject({ name: 'Tokyo' })
  })
})
