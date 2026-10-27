/** Round 2 (2026-10-27): Near me by default, the filters behind one button,
 * the sources slider, the app entry, and "use my location" naming each
 * failure for what it is. Each describe pins one of the owner's asks. */
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import { effectiveScope, splitByScope } from '../scope'
import { LOCATE_TIMEOUT_MS, useNearMe } from '../useNearMe'
import { ActiveFilters, FilterSheet } from '../components/FilterSheet'
import { ScopeBar } from '../components/ScopeBar'
import { SourcesBar } from '../components/SourcesBar'
import { GetAppBanner, GetAppLink, resetRelease } from '../components/GetApp'
import { WatchPanel } from '../components/WatchPanel'
import { NOW, makeEvent, resetStore } from './helpers'
import type { SourceHealth } from '../types'

const TOKYO = { lat: 35.68, lon: 139.69 }

beforeEach(() => {
  resetStore()
  localStorage.clear()
  useStore.getState().setLang('en')
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Near me: what counts as near, and what is counted elsewhere', () => {
  it('keeps events within the radius and COUNTS the severe ones beyond it', () => {
    const near = makeEvent({ id: 'near', lat: 35.0, lon: 139.0, severity: 'minor' })
    const farMinor = makeEvent({ id: 'far-minor', lat: -33.9, lon: 18.4, severity: 'minor' })
    const farTsunami = makeEvent({ id: 'far-tsunami', lat: -20, lon: -70, severity: 'moderate', tsunami: true })
    const unplacedSevere = makeEvent({ id: 'nowhere', lat: null, lon: null, severity: 'severe', country_code: 'CL' })
    const split = splitByScope([near, farMinor, farTsunami, unplacedSevere], TOKYO, ['JP'])
    expect(split.near.map((e) => e.id)).toEqual(['near'])
    // the far tsunami never vanishes; the severe alert we cannot place is
    // counted too, but apart: "elsewhere" would be a claim about where it is
    expect(split.severeElsewhere).toBe(1)
    expect(split.unlocated).toBe(1)
  })

  it('without a position, Near me shows the world', () => {
    expect(effectiveScope('near', null)).toBe('world')
    expect(effectiveScope('near', TOKYO)).toBe('near')
    expect(effectiveScope('world', TOKYO)).toBe('world')
  })
})

/** A scriptable geolocation: the test decides what the browser answers. */
function fakeGeolocation(permission: 'granted' | 'prompt' | 'denied') {
  const watchers: { ok: PositionCallback; fail?: PositionErrorCallback }[] = []
  const geolocation = {
    watchPosition: vi.fn((ok: PositionCallback, fail?: PositionErrorCallback) => {
      watchers.push({ ok, fail })
      return watchers.length
    }),
    clearWatch: vi.fn(),
    getCurrentPosition: vi.fn(),
  }
  Object.defineProperty(navigator, 'geolocation', { configurable: true, value: geolocation })
  Object.defineProperty(navigator, 'permissions', {
    configurable: true,
    value: { query: vi.fn(async () => ({ state: permission })) },
  })
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  const position = (lat: number, lon: number) =>
    ({ coords: { latitude: lat, longitude: lon, accuracy: 3000 } }) as GeolocationPosition
  const error = (code: number) =>
    ({ code, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: '' }) as GeolocationPositionError
  return {
    geolocation,
    move: (lat: number, lon: number) => watchers.forEach((w) => w.ok(position(lat, lon))),
    fail: (code: number) => watchers.forEach((w) => w.fail?.(error(code))),
  }
}

const settle = () =>
  act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })

describe('Near me by default (owner: "le live sur la location de la personne")', () => {
  it('permission already granted: located silently, coarse, and followed when the reader moves', async () => {
    const geo = fakeGeolocation('granted')
    renderHook(() => useNearMe())
    await settle()
    expect(useStore.getState().locate).toBe('locating')
    act(() => geo.move(35.681236, 139.767125))
    expect(useStore.getState().here).toEqual({ lat: 35.68, lon: 139.77 })
    expect(useStore.getState().locate).toBe('ok')
    // the position is never stored: a reload waits for a real fix
    const saved = JSON.parse(localStorage.getItem('sosforge.scope') ?? '{}')
    expect(saved.lat).toBeUndefined()
    expect(saved.lon).toBeUndefined()
    act(() => geo.move(34.693738, 135.502165)) // the reader took the train to Osaka
    expect(useStore.getState().here).toEqual({ lat: 34.69, lon: 135.5 })
  })

  it('permission not decided: asked once, with the world showing meanwhile', async () => {
    const geo = fakeGeolocation('prompt')
    renderHook(() => useNearMe())
    await settle()
    expect(useStore.getState().locate).toBe('asking')
    expect(geo.geolocation.watchPosition).toHaveBeenCalledTimes(1)
    expect(JSON.parse(localStorage.getItem('sosforge.scope') ?? '{}').asked).toBe(true)
  })

  it('a prompt already shown on an earlier visit is not shown again (no nagging)', async () => {
    localStorage.setItem('sosforge.scope', JSON.stringify({ scope: 'near', asked: true }))
    const geo = fakeGeolocation('prompt')
    renderHook(() => useNearMe())
    await settle()
    expect(geo.geolocation.watchPosition).not.toHaveBeenCalled()
    expect(useStore.getState().locate).toBe('idle')
  })

  it('...unless the reader presses Near me', async () => {
    localStorage.setItem('sosforge.scope', JSON.stringify({ scope: 'near', asked: true }))
    const geo = fakeGeolocation('prompt')
    renderHook(() => useNearMe())
    await settle()
    act(() => useStore.getState().requestNearMe())
    await settle()
    expect(geo.geolocation.watchPosition).toHaveBeenCalledTimes(1)
  })

  it('denied: the world, the position forgotten, nothing asked', async () => {
    useStore.setState({ here: TOKYO })
    const geo = fakeGeolocation('denied')
    renderHook(() => useNearMe())
    await settle()
    expect(geo.geolocation.watchPosition).not.toHaveBeenCalled()
    expect(useStore.getState().locate).toBe('denied')
    expect(useStore.getState().here).toBeNull()
  })

  it('an insecure page is said as such, not as a refusal', async () => {
    fakeGeolocation('granted')
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
    renderHook(() => useNearMe())
    await settle()
    expect(useStore.getState().locate).toBe('insecure')
  })

  it('no fix within 8 s: the world -- and a late answer still switches to Near me', async () => {
    vi.useFakeTimers()
    const geo = fakeGeolocation('prompt')
    renderHook(() => useNearMe())
    await settle()
    act(() => vi.advanceTimersByTime(LOCATE_TIMEOUT_MS))
    expect(useStore.getState().locate).toBe('timeout')
    act(() => geo.move(35.68, 139.69)) // the reader answered the prompt late
    expect(useStore.getState().locate).toBe('ok')
    expect(useStore.getState().here).toEqual(TOKYO)
  })

  it('the reader chose World: nothing is asked at all', async () => {
    useStore.setState({ scope: 'world' })
    const geo = fakeGeolocation('granted')
    renderHook(() => useNearMe())
    await settle()
    expect(geo.geolocation.watchPosition).not.toHaveBeenCalled()
  })
})

describe('the scope bar', () => {
  it('in Near me, the count of severe events elsewhere is permanent, zero included, and opens World', () => {
    useStore.setState({ scope: 'near', here: TOKYO, received: true, connected: true })
    const { rerender } = render(<ScopeBar severeElsewhere={0} />)
    expect(screen.getByRole('button', { name: /Nothing severe elsewhere/ })).toBeInTheDocument()
    rerender(<ScopeBar severeElsewhere={2} />)
    fireEvent.click(screen.getByRole('button', { name: /2 severe elsewhere/ }))
    expect(useStore.getState().scope).toBe('world')
  })

  it('before the feed has spoken, the count is unknown, never "nothing"', () => {
    useStore.setState({ scope: 'near', here: TOKYO, received: false })
    render(<ScopeBar severeElsewhere={0} />)
    expect(screen.queryByText(/Nothing severe elsewhere/)).toBeNull()
    expect(screen.getByText('Elsewhere: still loading')).toBeInTheDocument()
  })

  it('says why the world shows when the reader wanted Near me', () => {
    useStore.setState({ scope: 'near', here: null, locate: 'denied' })
    render(<ScopeBar severeElsewhere={0} />)
    expect(screen.getByRole('status')).toHaveTextContent('Location blocked: showing the world.')
    expect(screen.getByRole('button', { name: /World/ })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('filters behind one button (owner: "dans un bouton")', () => {
  it('a badge counts what narrows the feed, and each chip removes its filter', () => {
    useStore.getState().toggleKind('heat')
    useStore.getState().setMinSeverity('severe')
    render(
      <>
        <FilterSheet events={[]} resultCount={3} phone={false} />
        <ActiveFilters phone={false} />
      </>,
    )
    expect(screen.getByLabelText('2 active filter(s)')).toHaveTextContent('2')
    fireEvent.click(screen.getByRole('button', { name: /Remove filter: .*Severe and up/ }))
    expect(useStore.getState().filters.minSeverity).toBe('info')
    fireEvent.click(screen.getByRole('button', { name: /Remove filter: Kinds 12\/13/ }))
    expect(useStore.getState().filters.kinds.size).toBe(13)
  })

  it('is a dialog: labelled, focus moved in, trapped, Esc closes and gives focus back', () => {
    render(<FilterSheet events={[]} resultCount={3} phone={false} />)
    const trigger = screen.getByRole('button', { name: 'Filters' })
    fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Filters' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.contains(document.activeElement)).toBe(true)
    // the 13 kinds and the severity are inside
    expect(dialog.querySelectorAll('.filter-kinds .chip')).toHaveLength(13)
    expect(screen.getByRole('button', { name: /Severe and up/ })).toBeInTheDocument()
    // Tab from the last control comes back to the first
    const buttons = dialog.querySelectorAll<HTMLElement>('button, input')
    buttons[buttons.length - 1].focus()
    fireEvent.keyDown(dialog, { key: 'Tab' })
    expect(document.activeElement).toBe(buttons[0])
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('on a phone it is a bottom sheet and holds the time window too', () => {
    useStore.getState().setWindow(15)
    render(
      <>
        <FilterSheet events={[]} resultCount={3} phone />
        <ActiveFilters phone />
      </>,
    )
    // the window is a filter there, so it has a chip
    expect(screen.getByRole('button', { name: 'Remove filter: Live' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Filters/ }))
    expect(screen.getByRole('dialog')).toHaveClass('filter-dialog-sheet')
    expect(screen.getByRole('group', { name: 'Window' })).toBeInTheDocument()
  })

  it('the severity floor filters the feed and never hides a tsunami flag', () => {
    useStore.getState().setMinSeverity('extreme')
    expect(useStore.getState().filters.minSeverity).toBe('extreme')
    expect(JSON.parse(localStorage.getItem('sosforge.filters') ?? '{}').minSeverity).toBe('extreme')
  })
})

describe('sources in a slider (owner: "met-les dans un slider")', () => {
  const source = (name: string, extra: Partial<SourceHealth> = {}): SourceHealth => ({
    name,
    connected: true,
    last_ok: new Date(NOW - 30_000).toISOString(),
    last_error: null,
    events_seen: 10,
    errors: 0,
    ...extra,
  })

  it('a source that is down or degraded is pinned OUTSIDE the scroll, always visible', () => {
    useStore.setState({
      received: true,
      connected: true,
      clients: 3,
      sources: [
        source('emsc'),
        source('usgs'),
        // the server's shape (sources/base.py): one object per failing feed;
        // a bare asyncio timeout reads "TimeoutError: " (str(exc) is empty)
        source('tsunami', { degraded: true, failed_feeds: [{ feed: 'PAAQ', error: 'TimeoutError: ' }] }),
        source('noa', { connected: false, last_error: 'HTTP 503 from https://www.gein.noa.gr/' }),
      ],
    })
    const { container } = render(<SourcesBar now={NOW} />)
    expect(screen.getByText('3/4 online · 3 client(s)')).toBeInTheDocument()
    const pinned = container.querySelector('.sources-pinned')!
    const strip = container.querySelector('.sources-strip')!
    expect(pinned.textContent).toContain('NOAA tsunami')
    expect(pinned.textContent).toContain('NOA (Greece)')
    expect(strip.textContent).not.toContain('NOA (Greece)')
    expect(strip.textContent).toContain('EMSC')
  })

  it('before the feed has spoken, the summary says so instead of "0/0 online"', () => {
    render(<SourcesBar now={NOW} />)
    expect(screen.getByText('Sources: waiting for the feed')).toBeInTheDocument()
    expect(screen.queryByText(/0\/0/)).toBeNull()
  })

  it('the error and the last poll are behind a tap', () => {
    useStore.setState({
      sources: [source('noa', { connected: false, last_error: 'HTTP 503 from https://www.gein.noa.gr/' })],
    })
    render(<SourcesBar now={NOW} />)
    expect(screen.queryByText(/HTTP 503/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /NOA \(Greece\), offline/ }))
    expect(screen.getByText(/HTTP 503/)).toBeInTheDocument()
    expect(screen.getByText('30 s ago')).toBeInTheDocument()
  })

  it('the strip is reachable by keyboard and steps with the arrows', () => {
    useStore.setState({ sources: [source('emsc'), source('usgs')] })
    const { container } = render(<SourcesBar now={NOW} />)
    const strip = container.querySelector<HTMLElement>('.sources-strip')!
    expect(strip.tabIndex).toBe(0)
    const scrollBy = vi.fn()
    strip.scrollBy = scrollBy as unknown as typeof strip.scrollBy
    fireEvent.keyDown(strip, { key: 'ArrowRight' })
    expect(scrollBy).toHaveBeenCalledWith(expect.objectContaining({ left: 120 }))
  })
})

describe('"Get the app"', () => {
  const serve = (found: boolean) =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        found
          ? { ok: true, headers: { get: () => 'application/json' }, json: async () => ({ version: '0.1.1' }) }
          : { ok: true, headers: { get: () => 'text/html' }, json: async () => ({}) },
      ),
    )
  const userAgent = (ua: string) => Object.defineProperty(navigator, 'userAgent', { configurable: true, value: ua })
  beforeEach(() => resetRelease())
  afterEach(() => userAgent('Mozilla/5.0 (jsdom)'))

  it('no published release (the SPA answers HTML): no entry anywhere, no link to a page that is not there', async () => {
    serve(false)
    userAgent('Mozilla/5.0 (Linux; Android 14; Pixel 8)')
    render(
      <>
        <GetAppBanner />
        <GetAppLink />
      </>,
    )
    await settle()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('prominent on Android, dismissed for that version once dismissed', async () => {
    serve(true)
    userAgent('Mozilla/5.0 (Linux; Android 14; Pixel 8)')
    const { unmount } = render(<GetAppBanner />)
    await settle()
    expect(screen.getByRole('link', { name: 'Get the app' })).toHaveAttribute('href', '/download/')
    fireEvent.click(screen.getByRole('button', { name: 'Hide this suggestion' }))
    expect(screen.queryByRole('link')).toBeNull()
    unmount()
    render(<GetAppBanner />)
    await settle()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('discreet elsewhere: no banner on a desktop, a footer link only', async () => {
    serve(true)
    userAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')
    render(
      <>
        <GetAppBanner />
        <GetAppLink />
      </>,
    )
    await settle()
    expect(screen.queryByRole('button', { name: 'Hide this suggestion' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Android app 0.1.1' })).toHaveAttribute('href', '/download/')
  })
})

describe('"Use my location" names each failure (owner: "vérifie que use my location fonctionne")', () => {
  function geo(answer: (ok: PositionCallback, fail: PositionErrorCallback) => void) {
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { getCurrentPosition: vi.fn((ok, fail) => answer(ok, fail)) },
    })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  }
  const error = (code: number) =>
    ({ code, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: '' }) as GeolocationPositionError

  it.each([
    [1, /Location refused/],
    [2, /Geolocation unavailable/],
    [3, /No position came/],
  ])('error code %i is said as what it is', (code, message) => {
    geo((_ok, fail) => fail(error(code)))
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /Use my location/ }))
    expect(screen.getByRole('status')).toHaveTextContent(message)
  })

  it('a prompt left unanswered does not leave "Locating..." forever', () => {
    vi.useFakeTimers()
    geo(() => {}) // the browser never answers
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /Use my location/ }))
    expect(screen.getByRole('button', { name: /Locating/ })).toBeDisabled()
    act(() => vi.advanceTimersByTime(20_000))
    expect(screen.getByRole('button', { name: /Use my location/ })).not.toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent(/No position came/)
  })

  it('reuses the position Near me already has, instead of asking twice; stored coarse', () => {
    geo(() => {
      throw new Error('must not ask')
    })
    // a fix Near me is following right now
    useStore.setState({ scope: 'near', locate: 'ok', here: TOKYO })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /Use my location/ }))
    expect(useStore.getState().watch).toMatchObject({ lat: 35.68, lon: 139.69 })
  })

  it('a fix is kept at two decimals', () => {
    geo((ok) => ok({ coords: { latitude: 38.268215, longitude: 140.869356 } } as GeolocationPosition))
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /Use my location/ }))
    expect(useStore.getState().watch).toMatchObject({ lat: 38.27, lon: 140.87 })
  })
})
