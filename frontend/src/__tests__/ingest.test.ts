/** `ingest` is the single entry point for everything arriving from the
 * websocket. Each test protects a behaviour that has a history (see README:
 * revisions, GDACS, server clock). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import type { ServerMessage, SourceHealth } from '../types'
import { NOW, makeEvent, makeStats, minutesAgo, resetStore } from './helpers'

const SOURCES: SourceHealth[] = [
  { name: 'emsc', connected: true, last_ok: null, last_error: null, events_seen: 3, errors: 0 },
]

function eventMessage(
  event = makeEvent(),
  { breaking = false, type = 'event' as 'event' | 'update' } = {},
): ServerMessage {
  return { type, event, primary: true, breaking }
}

beforeEach(() => {
  // Frozen clock: clockSkew and the 30 s halo can be tested without waiting.
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  resetStore()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('snapshot', () => {
  it('replaces the list entirely instead of merging', () => {
    useStore.setState({ events: [makeEvent({ id: 'stale-1' }), makeEvent({ id: 'stale-2' })] })
    const fresh = [makeEvent({ id: 'snap-1' }), makeEvent({ id: 'snap-2' })]
    useStore.getState().ingest({
      type: 'snapshot',
      server_time: new Date(NOW).toISOString(),
      events: fresh,
      stats: makeStats(),
      sources: SOURCES,
    })
    expect(useStore.getState().events.map((e) => e.id)).toEqual(['snap-1', 'snap-2'])
    expect(useStore.getState().connected).toBe(true)
  })

  it('pins clockSkew to the server clock, not the browser one', () => {
    // The server is 5 s "in the future" of the browser: every displayed age
    // must be computed with this offset (product rule: never the browser
    // clock).
    useStore.getState().ingest({
      type: 'snapshot',
      server_time: new Date(NOW + 5000).toISOString(),
      events: [],
      stats: makeStats(),
      sources: SOURCES,
    })
    expect(useStore.getState().clockSkew).toBe(5000)
  })
})

describe('tick', () => {
  it('refreshes stats, sources, clients and the clockSkew on every beat', () => {
    useStore.getState().ingest({
      type: 'tick',
      server_time: new Date(NOW - 2000).toISOString(),
      stats: makeStats({ last_hour: 7 }),
      sources: SOURCES,
      clients: 4,
    })
    const state = useStore.getState()
    expect(state.stats?.last_hour).toBe(7)
    expect(state.clients).toBe(4)
    expect(state.clockSkew).toBe(-2000)
    expect(state.lastMessageAt).toBe(NOW)
  })
})

describe('events and updates', () => {
  it('a revision of a known event updates it without duplicating it', () => {
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'q1', magnitude: 5.8 })))
    useStore
      .getState()
      .ingest(eventMessage(makeEvent({ id: 'q1', magnitude: 6.1, revision: 1 }), { type: 'update' }))
    const events = useStore.getState().events
    expect(events).toHaveLength(1)
    expect(events[0].magnitude).toBe(6.1)
    expect(events[0].revision).toBe(1)
  })

  it('the feed stays sorted by descending event time, whatever the arrival order', () => {
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'mid', time: minutesAgo(30) })))
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'old', time: minutesAgo(90) })))
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'new', time: minutesAgo(1) })))
    expect(useStore.getState().events.map((e) => e.id)).toEqual(['new', 'mid', 'old'])
  })

  it('breaking: false does NOT mark the event as fresh (96 GDACS alerts do not blink)', () => {
    // The original incident: on the first GDACS cycle, ~96 alerts several
    // days old arrived as `event` messages and all blinked as breaking news.
    // Only the server can tell "just happened" from "just entered the
    // buffer": the client must respect its verdict.
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'gdacs-old' }), { breaking: false }))
    expect(useStore.getState().fresh.has('gdacs-old')).toBe(false)
  })

  it('breaking: true marks the event fresh, and the halo dies out on its own after 30 s', () => {
    useStore.getState().ingest(eventMessage(makeEvent({ id: 'hot' }), { breaking: true }))
    expect(useStore.getState().fresh.has('hot')).toBe(true)
    vi.advanceTimersByTime(30_000)
    expect(useStore.getState().fresh.has('hot')).toBe(false)
  })
})

describe('cross-source duplicates', () => {
  it('ignores a secondary solution: the server has already picked a representative', () => {
    const store = useStore.getState()
    store.ingest({
      type: 'event',
      event: makeEvent({ id: 'emsc:1', time: minutesAgo(1) }),
      primary: true,
      breaking: true,
    })
    store.ingest({
      type: 'event',
      // same quake seen by another agency: the server marked it non-primary
      event: makeEvent({ id: 'usgs:1', time: minutesAgo(1) }),
      primary: false,
      breaking: true,
    })
    expect(useStore.getState().events.map((e) => e.id)).toEqual(['emsc:1'])
  })
})

describe('purge -- the message that lets things disappear', () => {
  /* Until phase 1 the protocol could add and update, never remove. A tab open
   * since the morning kept every lifted warning and every dissipated cyclone
   * on screen, because the only way the server had to say "this is over" was
   * to stop talking about it -- which is indistinguishable from "nothing new".
   */
  const snapshot = (events = [] as ReturnType<typeof makeEvent>[]): ServerMessage => ({
    type: 'snapshot',
    server_time: new Date(NOW).toISOString(),
    events,
    stats: makeStats(),
    sources: SOURCES,
  })

  it('removes the purged events and leaves the others alone', () => {
    const { ingest } = useStore.getState()
    ingest(snapshot([makeEvent({ id: 'a' }), makeEvent({ id: 'b' }), makeEvent({ id: 'c' })]))

    ingest({ type: 'purge', ids: ['a', 'c'], reason: 'stale' })

    expect(useStore.getState().events.map((e) => e.id)).toEqual(['b'])
  })

  it('closes the detail panel when the selected event is the one that goes', () => {
    const { ingest } = useStore.getState()
    ingest(snapshot([makeEvent({ id: 'cancelled-eew' })]))
    useStore.setState({ selected: 'cancelled-eew' })

    ingest({ type: 'purge', ids: ['cancelled-eew'], reason: 'cancelled' })

    expect(useStore.getState().selected).toBeNull()
    expect(useStore.getState().events).toEqual([])
  })

  it('keeps the selection when something else is purged', () => {
    const { ingest } = useStore.getState()
    ingest(snapshot([makeEvent({ id: 'keep' }), makeEvent({ id: 'drop' })]))
    useStore.setState({ selected: 'keep' })

    ingest({ type: 'purge', ids: ['drop'], reason: 'stale' })

    expect(useStore.getState().selected).toBe('keep')
  })

  it('drops the purged id from the halo set', () => {
    const { ingest } = useStore.getState()
    ingest(snapshot([]))
    ingest({ type: 'event', event: makeEvent({ id: 'flash' }), primary: true, breaking: true })
    expect(useStore.getState().fresh.has('flash')).toBe(true)

    ingest({ type: 'purge', ids: ['flash'], reason: 'lifted' })

    expect(useStore.getState().fresh.has('flash')).toBe(false)
  })

  it('counts as live traffic: a purge is a sign of life, not silence', () => {
    const { ingest } = useStore.getState()
    useStore.setState({ lastMessageAt: 0 })
    ingest({ type: 'purge', ids: [], reason: 'stale' })
    expect(useStore.getState().lastMessageAt).toBeGreaterThan(0)
  })
})
