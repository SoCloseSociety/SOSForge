/** The chunked snapshot (contract with lane B, 2026-10).
 *
 * Why it exists, measured: the pinned snapshot is 118 KiB on the wire, ticks
 * queue behind it on the same socket, and the 15 s silence watchdog runs
 * while it downloads. Below ~64 kbit/s a fresh tab closed its own socket
 * mid-snapshot and reconnected forever (Chrome, Slow 3G: the first event
 * never arrived in 60 s). The server now sends `snapshot` with `more: N`,
 * then `snapshot_more` chunks counting down to `remaining: 0`.
 *
 * Client rules pinned here: chunk events are HISTORY (no halo, no sound);
 * every message, chunks included, feeds the watchdog; nothing on screen
 * claims a complete count, or "no event", while chunks are still coming;
 * an old server (no `more`) behaves exactly as before. */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { connectLive } from '../live'
import { emptyReason } from '../emptyReason'
import { useStore } from '../store'
import { NOW, makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage, SosEvent } from '../types'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

const snapshot = (events: SosEvent[], more?: number): ServerMessage =>
  ({
    type: 'snapshot',
    server_time: new Date(Date.now()).toISOString(),
    events,
    stats: makeStats(),
    sources: [],
    ...(more === undefined ? {} : { more }),
  }) as ServerMessage

const chunk = (events: SosEvent[], remaining: number): ServerMessage =>
  ({
    type: 'snapshot_more',
    server_time: new Date(Date.now()).toISOString(),
    events,
    remaining,
  }) as ServerMessage

const quakes = (from: number, n: number) =>
  Array.from({ length: n }, (_, i) =>
    makeEvent({ id: `emsc:${from + i}`, time: new Date(NOW - (from + i) * 60_000).toISOString() }),
  )

describe('the store', () => {
  beforeEach(() => resetStore())

  it('an old server (no `more`) is complete at once, as before', () => {
    useStore.getState().ingest(snapshot(quakes(0, 3)))
    expect(useStore.getState().snapshotRemaining).toBe(0)
    expect(useStore.getState().events).toHaveLength(3)
  })

  it('upserts chunks like snapshot events: history, no halo, no sound', () => {
    const { ingest } = useStore.getState()
    ingest(snapshot(quakes(0, 2), 4))
    expect(useStore.getState().snapshotRemaining).toBe(4)
    ingest(chunk(quakes(2, 2), 2))
    // the same id again replaces, never duplicates
    ingest(chunk([makeEvent({ id: 'emsc:0', time: new Date(NOW).toISOString(), magnitude: 6.1 }), ...quakes(4, 1)], 0))
    const state = useStore.getState()
    expect(state.snapshotRemaining).toBe(0)
    expect(state.events.map((e) => e.id)).toEqual(['emsc:0', 'emsc:1', 'emsc:2', 'emsc:3', 'emsc:4'])
    expect(state.events[0].magnitude).toBe(6.1)
    expect(state.fresh.size).toBe(0)
  })

  it('every chunk is a sign of life for the watchdog', () => {
    const { ingest } = useStore.getState()
    ingest(snapshot([], 10))
    useStore.setState({ lastMessageAt: 0 })
    ingest(chunk(quakes(0, 1), 9))
    expect(useStore.getState().lastMessageAt).toBeGreaterThan(0)
  })

  it('a message type it does not know is ignored, not read as an event', () => {
    const { ingest } = useStore.getState()
    ingest(snapshot(quakes(0, 1)))
    expect(() => ingest({ type: 'something_new' } as unknown as ServerMessage)).not.toThrow()
    expect(useStore.getState().events).toHaveLength(1)
  })
})

describe('the transport, on a link too slow for one big frame', () => {
  class FakeWebSocket {
    static instances: FakeWebSocket[] = []
    closeCalls = 0
    onopen: (() => void) | null = null
    onmessage: ((m: { data: string }) => void) | null = null
    onclose: (() => void) | null = null
    onerror: (() => void) | null = null
    constructor() {
      FakeWebSocket.instances.push(this)
    }
    close() {
      this.closeCalls += 1
      this.onclose?.()
    }
  }
  let stop: (() => void) | null = null

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    resetStore()
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })
  afterEach(() => {
    stop?.()
    stop = null
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  /* The defect, reproduced: one snapshot frame that needs 18 s to arrive
   * (873 KiB at Chrome's Slow 3G). The watchdog sees 15 s of silence and
   * closes the socket first: the snapshot is never delivered, and the next
   * socket starts the same download again. */
  it('ONE big snapshot frame never lands: the watchdog kills the download', () => {
    stop = connectLive()
    const socket = FakeWebSocket.instances[0]
    socket.onopen?.()
    vi.advanceTimersByTime(18_000) // the frame is still in flight
    expect(socket.closeCalls).toBe(1)
    expect(useStore.getState().events).toHaveLength(0)
  })

  /* The fix: the same 18 s of download, cut into chunks that each land
   * within a few seconds. Every chunk feeds the watchdog; the socket lives,
   * and the whole snapshot arrives. */
  it('the same download in chunks lands whole, and the socket survives it', () => {
    stop = connectLive()
    const socket = FakeWebSocket.instances[0]
    socket.onopen?.()
    vi.advanceTimersByTime(3_000)
    socket.onmessage?.({ data: JSON.stringify(snapshot(quakes(0, 50), 250)) })
    for (let sent = 50; sent < 300; sent += 50) {
      vi.advanceTimersByTime(3_000)
      socket.onmessage?.({ data: JSON.stringify(chunk(quakes(sent, 50), 300 - sent - 50)) })
    }
    expect(socket.closeCalls).toBe(0)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(useStore.getState().events).toHaveLength(300)
    expect(useStore.getState().snapshotRemaining).toBe(0)
  })
})

describe('nothing on screen claims a complete picture while chunks arrive', () => {
  beforeEach(() => resetStore())

  it('the empty state says "loading", never "no event", while chunks are due', () => {
    const filters = useStore.getState().filters
    expect(emptyReason({ events: [], visible: [], filters, remaining: 120 })).toBe('feed.loading')
    expect(emptyReason({ events: [], visible: [], filters, remaining: 0 })).toBe('filters.empty')
  })

  afterEach(() => vi.unstubAllGlobals())

  it('the app says how many are still coming, and the kind counts are marked partial', async () => {
    // the App opens the real socket: give it one that never talks
    vi.stubGlobal(
      'WebSocket',
      class {
        close() {}
      },
    )
    const { default: App } = await import('../App')
    useStore.getState().ingest(snapshot(quakes(0, 2), 120))
    render(<App />)
    expect(screen.getByText('Loading the feed: 120 more events to come.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Earthquakes/ }).textContent).toContain('2+')
    act(() => useStore.getState().ingest(chunk(quakes(2, 3), 0)))
    expect(screen.queryByText(/more events to come/)).toBeNull()
    expect(screen.getByRole('button', { name: /Earthquakes/ }).textContent).toMatch(/5$/)
  })
})
