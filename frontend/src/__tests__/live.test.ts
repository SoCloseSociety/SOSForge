/** `connectLive` is what holds the product rule: "the feed must never lie
 * about its own freshness". Three mechanisms, each protected here by the
 * tests bearing its name: the exponential reconnect (1 s, 2 s, 4 s... capped
 * at 15 s), the 15 s watchdog that closes an "open" but dead socket, and the
 * cleanup that stops everything without triggering another reconnect. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { connectLive } from '../live'
import { useStore } from '../store'
import { NOW, makeStats, resetStore } from './helpers'

/** Fake WebSocket: the real network has no place in a unit test. `close()`
 * triggers `onclose` (like a browser, but synchronously), and the `open` /
 * `message` / `error` helpers play the server's part. */
class FakeWebSocket {
  /** every instance created, in order: counts the reconnections */
  static instances: FakeWebSocket[] = []

  url: string
  closeCalls = 0
  onopen: (() => void) | null = null
  onmessage: ((message: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close(): void {
    this.closeCalls += 1
    this.onclose?.()
  }

  /* -- server side: the events the test triggers -- */
  open(): void {
    this.onopen?.()
  }

  message(data: string): void {
    this.onmessage?.({ data })
  }

  error(): void {
    this.onerror?.()
  }
}

/** The last socket created: after a reconnection, it is the live one. */
function lastSocket(): FakeWebSocket {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
}

/** A valid server tick, timestamped on the current (fake) clock. */
function tick(): string {
  return JSON.stringify({
    type: 'tick',
    server_time: new Date(Date.now()).toISOString(),
    stats: makeStats(),
    sources: [],
    clients: 1,
  })
}

/** Checks that a reconnection fires exactly after `ms`: nothing at ms - 1,
 * a new socket at ms. This is the heart of the backoff test. */
function expectReconnectAfter(ms: number): void {
  const before = FakeWebSocket.instances.length
  vi.advanceTimersByTime(ms - 1)
  expect(FakeWebSocket.instances.length).toBe(before)
  vi.advanceTimersByTime(1)
  expect(FakeWebSocket.instances.length).toBe(before + 1)
}

let stop: (() => void) | null = null

beforeEach(() => {
  // Frozen clock: the backoff delays and the watchdog's 15 s are tested
  // without waiting, and Date.now() is deterministic.
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  resetStore()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  // Always clean up the connection BEFORE restoring real timers, otherwise
  // the watchdog interval leaks into the next test.
  stop?.()
  stop = null
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('connectLive: URL', () => {
  it('builds the URL on the current host, with ws: when the page is on http:', () => {
    stop = connectLive()
    // jsdom serves the page on http://localhost:3000: the socket must target
    // the same host (the /ws proxy), never a hard-coded one.
    expect(lastSocket().url).toBe(`ws://${location.host}/ws`)
    expect(lastSocket().url).toBe('ws://localhost:3000/ws')
  })
})

describe('connectLive: messages', () => {
  it('an unreadable message (broken JSON) does NOT kill the connection', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()

    expect(() => socket.message('{not json')).not.toThrow()

    // The connection is still there, and the next message is ingested.
    expect(useStore.getState().connected).toBe(true)
    expect(socket.closeCalls).toBe(0)
    socket.message(tick())
    expect(useStore.getState().lastMessageAt).toBe(Date.now())
  })
})

describe('connectLive: exponential reconnect', () => {
  it('onopen sets connected to true', () => {
    stop = connectLive()
    expect(useStore.getState().connected).toBe(false)
    lastSocket().open()
    expect(useStore.getState().connected).toBe(true)
  })

  it('a close reconnects after 1 s, then 2 s, 4 s, 8 s, capped at 15 s', () => {
    stop = connectLive()
    // Each failure doubles the delay; the 15 s cap is checked twice to prove
    // it holds (and does not go on to 16 s, 32 s...).
    for (const delay of [1000, 2000, 4000, 8000, 15000, 15000]) {
      lastSocket().close()
      expect(useStore.getState().connected).toBe(false)
      expectReconnectAfter(delay)
    }
  })

  it('only a received MESSAGE resets the backoff counter', () => {
    stop = connectLive()
    // Two failures: we have climbed to a 2 s delay.
    lastSocket().close()
    expectReconnectAfter(1000)
    lastSocket().close()
    expectReconnectAfter(2000)

    // A socket that opens proves nothing: a proxy can accept the TCP without
    // relaying anything. The backoff must keep climbing.
    lastSocket().open()
    expect(useStore.getState().connected).toBe(true)
    lastSocket().close()
    expectReconnectAfter(4000)

    // A message, on the other hand, proves the link carries data.
    lastSocket().open()
    lastSocket().message(tick())
    lastSocket().close()
    expectReconnectAfter(1000)
  })

  it('an error closes the socket, which starts the reconnect cycle', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.error()
    expect(socket.closeCalls).toBe(1)
    expect(useStore.getState().connected).toBe(false)
    expectReconnectAfter(1000)
  })
})

describe('connectLive: 15 s watchdog', () => {
  it('15 s without a message while believing itself connected: the socket is CLOSED to force a reconnect', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.message(tick())

    // Total silence afterwards (wifi down, proxy swallowing packets): the
    // socket stays "open" but the link is dead. The watchdog checks every
    // 2 s, so detection lands between 15 and 17 s -- not up to 20 s as with
    // the old 5 s step.
    vi.advanceTimersByTime(15_000)
    expect(socket.closeCalls).toBe(0) // 15 s sharp: not yet past
    const before = FakeWebSocket.instances.length

    vi.advanceTimersByTime(2_000)
    expect(socket.closeCalls).toBe(1)
    expect(useStore.getState().connected).toBe(false)

    // And the forced close does trigger a reconnect, at the first backoff
    // step since this link had received at least one message.
    vi.advanceTimersByTime(1_000)
    expect(FakeWebSocket.instances.length).toBe(before + 1)
  })

  it('regular ticks prevent any close: a healthy connection never gets cut', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.message(tick())

    // One minute of nominal life: a tick every 5 s. The watchdog runs twelve
    // times and must never touch the socket.
    for (let i = 0; i < 12; i += 1) {
      vi.advanceTimersByTime(5_000)
      socket.message(tick())
    }
    expect(socket.closeCalls).toBe(0)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(useStore.getState().connected).toBe(true)
  })

  it('gives the snapshot time to arrive before judging', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()

    // A connection that just opened has not received its snapshot yet:
    // closing it would cut a connection that is starting up.
    vi.advanceTimersByTime(10_000)
    expect(socket.closeCalls).toBe(0)
  })

  it('closes a first connection that opens and stays MUTE', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()

    // The trap this test protects against: a proxy that accepts the TCP
    // without relaying anything. `lastMessageAt` stays at zero, `connected`
    // is true, and without a fallback on the opening time the watchdog NEVER
    // closed -- the interface showed a frozen feed while announcing "LIVE",
    // indefinitely.
    vi.advanceTimersByTime(18_000)
    expect(socket.closeCalls).toBe(1)
  })
})

describe('connectLive: cleanup', () => {
  it('stops the socket, the watchdog and the backoff, without another reconnect', () => {
    stop = connectLive()
    const socket = lastSocket()
    socket.open()
    socket.message(tick())

    stop()
    expect(socket.closeCalls).toBe(1)
    // No timer left alive: neither watchdog nor pending reconnect.
    expect(vi.getTimerCount()).toBe(0)

    // And passing time recreates nothing.
    vi.advanceTimersByTime(120_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    stop = null
  })

  it('also cancels an already scheduled reconnect', () => {
    stop = connectLive()
    // The socket drops: a reconnect is scheduled for 1 s from now...
    lastSocket().close()
    expect(useStore.getState().connected).toBe(false)

    // ...but the cleanup comes first. It must never fire.
    stop()
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(120_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    stop = null
  })
})
