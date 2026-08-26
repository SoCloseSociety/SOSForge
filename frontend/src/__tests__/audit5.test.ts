/** Fifth audit -- frontend defects, each proven before it was fixed. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyReason } from '../emptyReason'
import { makeEvent, makeStats, resetStore, NOW } from './helpers'
import type { ServerMessage, SourceHealth } from '../types'

const SOURCES: SourceHealth[] = [
  { name: 'emsc', connected: true, last_ok: null, last_error: null, events_seen: 1, errors: 0 },
]

describe('the watchdog must not kill the socket it just opened', () => {
  /* `const reference = lastMessageAt || openedAt`.
   *
   * On the FIRST connection lastMessageAt is 0, so openedAt wins and the new
   * socket gets its grace period -- which is what the existing test covers,
   * and why this went unnoticed. On every RECONNECT lastMessageAt is a real
   * but stale timestamp, so it wins over the fresh openedAt: the watchdog
   * beats two seconds later, measures silence from before the outage, and
   * closes the brand-new socket before its snapshot can arrive.
   *
   * On a slow link -- the saturated network this product exists for -- that
   * loop never converges and the tab never recovers without a reload.
   */
  class FakeSocket {
    static last: FakeSocket | null = null
    onopen: (() => void) | null = null
    onmessage: ((e: { data: string }) => void) | null = null
    onclose: (() => void) | null = null
    onerror: (() => void) | null = null
    closed = 0
    constructor(public url: string) {
      FakeSocket.last = this
    }
    close() {
      this.closed++
      this.onclose?.()
    }
  }

  let stop: (() => void) | null = null

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    resetStore()
    FakeSocket.last = null
    vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  })

  afterEach(() => {
    stop?.()
    stop = null
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('gives a RECONNECTED socket the same grace as the first one', async () => {
    const { connectLive } = await import('../live')
    stop = connectLive()

    // first connection, healthy: one message arrives
    const first = FakeSocket.last!
    first.onopen?.()
    first.onmessage?.({
      data: JSON.stringify({
        type: 'snapshot',
        server_time: new Date(NOW).toISOString(),
        events: [],
        stats: makeStats(),
        sources: SOURCES,
      } satisfies ServerMessage),
    })

    // the network dies: no messages for 20 s, the watchdog closes it, and the
    // backoff opens a replacement (so `FakeSocket.last` is no longer `first`)
    vi.advanceTimersByTime(20_000)
    expect(first.closed).toBeGreaterThan(0)

    const reconnected = FakeSocket.last!
    expect(reconnected).not.toBe(first)
    reconnected.onopen?.()
    const closesAtOpen = reconnected.closed

    // one watchdog beat later, the fresh socket must still be alive: its
    // snapshot is in flight
    vi.advanceTimersByTime(4_000)

    expect(reconnected.closed).toBe(closesAtOpen)
  })
})

describe('the first visit must not be interrupted by a reload', () => {
  /* sw.js calls skipWaiting() + clients.claim() on activate, so on a FIRST
   * visit the page starts uncontrolled and the just-installed worker claims
   * it seconds later. `controllerchange` then fired and reloaded the page --
   * mid-read, re-fetching everything, on exactly the emergency visit this
   * product exists for. The `reloading` guard only prevented a loop, not the
   * reload itself.
   */
  let listeners: Record<string, () => void> = {}

  beforeEach(() => {
    listeners = {}
    vi.resetModules()
  })

  afterEach(() => vi.unstubAllGlobals())

  function stubServiceWorker(hasController: boolean) {
    const reload = vi.fn()
    vi.stubGlobal('navigator', {
      serviceWorker: {
        controller: hasController ? {} : null,
        register: vi.fn(async () => ({})),
        addEventListener: (type: string, fn: () => void) => {
          listeners[type] = fn
        },
      },
    })
    vi.stubGlobal('location', { reload })
    return reload
  }

  it('does not reload when the worker takes control for the first time', async () => {
    const reload = stubServiceWorker(false)
    const { registerServiceWorker } = await import('../pwa')
    registerServiceWorker()

    listeners['controllerchange']?.()

    expect(reload).not.toHaveBeenCalled()
  })

  it('still reloads when a NEW worker replaces one already in control', async () => {
    const reload = stubServiceWorker(true)
    const { registerServiceWorker } = await import('../pwa')
    registerServiceWorker()

    listeners['controllerchange']?.()

    expect(reload).toHaveBeenCalledTimes(1)
  })
})

describe('the empty feed must say what actually emptied it', () => {
  /* With events present and every kind chip switched off, the message read
   * "Nothing in this window. Widen the period." -- an instruction that cannot
   * possibly work, because the window is not what emptied the feed.
   */
  beforeEach(() => resetStore())

  it('blames the kinds when the kinds are what filtered everything out', () => {
    expect(
      emptyReason({
        events: [makeEvent()],
        visible: [],
        filters: { query: '', kinds: new Set(), minMagnitude: 0, windowMinutes: 1440 },
      }),
    ).toBe('filters.empty.kinds')
  })

  it('blames the magnitude slider when that is what did it', () => {
    expect(
      emptyReason({
        events: [makeEvent({ magnitude: 3 })],
        visible: [],
        filters: {
          query: '',
          kinds: new Set(['earthquake']),
          minMagnitude: 6,
          windowMinutes: 1440,
        },
      }),
    ).toBe('filters.empty.magnitude')
  })

  it('blames the window only when the window is the cause', () => {
    expect(
      emptyReason({
        events: [makeEvent()],
        visible: [],
        filters: {
          query: '',
          kinds: new Set(['earthquake']),
          minMagnitude: 0,
          windowMinutes: 15,
        },
      }),
    ).toBe('filters.empty.window')
  })

  it('blames the search when there is a search', () => {
    expect(
      emptyReason({
        events: [makeEvent()],
        visible: [],
        filters: { query: 'kraken', kinds: new Set(['earthquake']), minMagnitude: 0, windowMinutes: 1440 },
      }),
    ).toBe('filters.empty.search')
  })

  it('says nothing arrived at all when nothing arrived at all', () => {
    expect(
      emptyReason({
        events: [],
        visible: [],
        filters: { query: '', kinds: new Set(['earthquake']), minMagnitude: 0, windowMinutes: 1440 },
      }),
    ).toBe('filters.empty')
  })
})
