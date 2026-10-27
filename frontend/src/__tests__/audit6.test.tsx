/** Sixth audit (system audit, 2026-10) -- the frontend findings, each one
 * reproduced here BEFORE it was fixed (lesson 25: a finding is a lead, not a
 * fact). Numbers are the audit's. */
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import { criticalEvents } from '../critical'
import { makeEvent, makeStats, resetStore, NOW } from './helpers'
import type { ServerMessage } from '../types'

// The App renders the map lazily and opens a websocket: neither belongs in a
// unit test. The map is replaced by nothing, the socket by a no-op.
vi.mock('../components/MapView', () => ({ MapView: () => null }))
vi.mock('../live', () => ({ connectLive: () => () => {} }))

/** A browser that matches the phone media query (or not). */
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

/** Lets the async notification path (it awaits the service worker first)
 * reach the system before anything is counted. */
const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

/** Notifications granted, every construction recorded. */
const shown: string[] = []
class FakeNotification {
  static permission = 'granted'
  static requestPermission = async () => 'granted'
  constructor(title: string) {
    shown.push(title)
  }
}

describe('#3 the location alarm must fire whatever panel is on screen', () => {
  beforeEach(() => {
    resetStore()
    shown.length = 0
    localStorage.clear()
    vi.stubGlobal('Notification', FakeNotification)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    // jsdom has no matchMedia: put it back the way the other tests expect
    delete (window as unknown as Record<string, unknown>).matchMedia
  })

  /* On a phone the filter sheet is collapsed by default, and the watch panel
   * lives inside it. The alarm effect lived in the watch panel: with the
   * sheet closed, an M6 forty kilometres away raised nothing at all. */
  it('notifies on a phone with the filter sheet closed', async () => {
    stubMatchMedia(true)
    const { default: App } = await import('../App')
    const quake = makeEvent({
      id: 'emsc:near',
      time: new Date(Date.now() - 60_000).toISOString(),
      severity: 'severe',
      magnitude: 6.1,
      lat: 35.7,
      lon: 139.7,
    })
    useStore.setState({
      watch: { lat: 35.68, lon: 139.69, name: 'Tokyo', kinds: ['earthquake', 'tsunami'] },
      events: [quake],
    })
    render(<App />)
    await flush()
    expect(document.querySelector('.filters-collapsed')).not.toBeNull()
    expect(shown).toHaveLength(1)
  })

  /* Opening and closing the sheet remounted the panel, and its private
   * "already alarmed" memory with it: the whole last hour alarmed again. */
  it('does not alarm twice for the same event across remounts', async () => {
    stubMatchMedia(false)
    const { default: App } = await import('../App')
    const quake = makeEvent({
      id: 'emsc:near-2',
      time: new Date(Date.now() - 60_000).toISOString(),
      severity: 'severe',
      magnitude: 6.1,
      lat: 35.7,
      lon: 139.7,
    })
    useStore.setState({
      watch: { lat: 35.68, lon: 139.69, name: 'Tokyo', kinds: ['earthquake', 'tsunami'] },
      events: [quake],
    })
    const first = render(<App />)
    await flush()
    expect(shown).toHaveLength(1)
    first.unmount()
    render(<App />)
    await flush()
    expect(shown).toHaveLength(1)
  })
})

describe('#4 an escalated update deserves the same alarm as a breaking one', () => {
  beforeEach(() => resetStore())

  it('halos an update whose severity rose inside the breaking window', () => {
    const event = makeEvent({ id: 'usgs:esc', severity: 'extreme' })
    useStore.setState({ events: [makeEvent({ id: 'usgs:esc', severity: 'moderate' })] })
    const message = {
      type: 'update',
      event,
      primary: true,
      breaking: false,
      escalated: true,
    } as ServerMessage
    act(() => useStore.getState().ingest(message))
    expect(useStore.getState().fresh.has('usgs:esc')).toBe(true)
  })
})

describe('#8 a tsunami advisory in force stays in the banner past 6 h', () => {
  it('keeps an ongoing advisory issued 8 h ago', () => {
    const advisory = makeEvent({
      id: 'tsunami:adv',
      kind: 'tsunami',
      tsunami: true,
      ongoing: true,
      time: new Date(NOW - 8 * 3600_000).toISOString(),
    })
    const oldQuake = makeEvent({
      id: 'usgs:old',
      severity: 'extreme',
      time: new Date(NOW - 8 * 3600_000).toISOString(),
    })
    const ids = criticalEvents([advisory, oldQuake], NOW).map((e) => e.id)
    expect(ids).toEqual(['tsunami:adv'])
  })
})

describe('#1 the buffer cut must not drop alerts that are still in force', () => {
  beforeEach(() => resetStore())

  it('keeps an old ongoing alert when 1000 newer events arrive', () => {
    const volcano = makeEvent({
      id: 'volcano:old',
      kind: 'volcano',
      ongoing: true,
      time: new Date(NOW - 30 * 86400_000).toISOString(),
    })
    const recent = Array.from({ length: 1000 }, (_, i) =>
      makeEvent({ id: `emsc:${i}`, time: new Date(NOW - i * 1000).toISOString() }),
    )
    useStore.setState({ events: [...recent, volcano], stats: makeStats() })
    const message = {
      type: 'event',
      event: makeEvent({ id: 'emsc:new', time: new Date(NOW + 1000).toISOString() }),
      primary: true,
      breaking: false,
    } as ServerMessage
    act(() => useStore.getState().ingest(message))
    const ids = useStore.getState().events.map((e) => e.id)
    expect(ids).toContain('volcano:old')
    expect(ids).toContain('emsc:new')
  })
})
