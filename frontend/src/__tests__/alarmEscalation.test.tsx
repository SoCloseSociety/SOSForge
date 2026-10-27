/** Lane N's browser drill (2026-10-27) found two defects in the web alarm,
 * reproduced here first:
 *  1. an ESCALATION never rang again: the alarm memory was keyed by id, so
 *     M6.4 -> M7.3 and an EEW 5- -> 6- gave one notification each;
 *  2. a cancelled or relocated-out event left the alarm list but its system
 *     notification stayed in the tray -- a cancelled "take cover" EEW still
 *     showing. */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import { resetClosedTags, useWatchAlarms } from '../useWatchAlarms'
import { makeEvent, resetStore } from './helpers'
import type { SosEvent } from '../types'

const shown: string[] = []
const closed: string[] = []
class FakeNotification {
  static permission = 'granted'
  tag: string
  constructor(_title: string, options: { tag: string }) {
    this.tag = options.tag
    shown.push(options.tag)
  }
  close() {
    closed.push(this.tag)
  }
}

const TOKYO = { lat: 35.68, lon: 139.69, name: 'Tokyo', kinds: ['earthquake' as const, 'tsunami' as const] }
const quake = (severity: SosEvent['severity'], magnitude: number, lat = 35.3) =>
  makeEvent({ id: 'usgs:q1', severity, magnitude, lat, lon: 139.4, time: new Date(Date.now() - 60_000).toISOString() })

const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })

beforeEach(() => {
  resetStore()
  resetClosedTags()
  localStorage.clear()
  shown.length = 0
  closed.length = 0
  vi.stubGlobal('Notification', FakeNotification)
  useStore.setState({ watch: TOKYO, received: true, snapshotRemaining: 0 })
})
afterEach(() => vi.unstubAllGlobals())

describe('an escalation rings once more, a revision at the same rank stays silent', () => {
  it('M6.4 severe, then M6.5 severe, then M7.3 extreme: two notifications', async () => {
    const { rerender } = renderHook(({ events }) => useWatchAlarms(events, Date.now()), {
      initialProps: { events: [quake('severe', 6.4)] },
    })
    await flush()
    expect(shown).toEqual(['usgs:q1'])
    rerender({ events: [quake('severe', 6.5)] })
    await flush()
    expect(shown).toHaveLength(1)
    rerender({ events: [quake('extreme', 7.3)] })
    await flush()
    expect(shown).toEqual(['usgs:q1', 'usgs:q1'])
    expect(useStore.getState().alarmed.get('usgs:q1')).toBe(4)
  })

  it('a downgrade does not ring, and the memory keeps the highest rank', async () => {
    useStore.setState({ alarmed: new Map([['usgs:q1', 4]]) })
    renderHook(() => useWatchAlarms([quake('severe', 6.4)], Date.now()))
    await flush()
    expect(shown).toHaveLength(0)
    expect(useStore.getState().alarmed.get('usgs:q1')).toBe(4)
  })

  it('a memory saved before ranks were kept does not ring again on load', async () => {
    localStorage.setItem('sosforge.alarmed', JSON.stringify(['usgs:q1']))
    // what loadAlarmed reads at startup, replayed
    useStore.setState({ alarmed: new Map([['usgs:q1', null]]) })
    renderHook(() => useWatchAlarms([quake('severe', 6.4)], Date.now()))
    await flush()
    expect(shown).toHaveLength(0)
    expect(useStore.getState().alarmed.get('usgs:q1')).toBe(3)
  })
})

describe('what no longer concerns the place leaves the tray', () => {
  it('a purged (cancelled) event has its notification closed', async () => {
    const { rerender } = renderHook(({ events }) => useWatchAlarms(events, Date.now()), {
      initialProps: { events: [quake('extreme', 7.3)] },
    })
    await flush()
    expect(shown).toEqual(['usgs:q1'])
    rerender({ events: [] }) // the purge took it out of the feed
    await flush()
    expect(closed).toEqual(['usgs:q1'])
    // and only once, not on every message after
    rerender({ events: [makeEvent({ id: 'other', lat: -40, lon: 170 })] })
    await flush()
    expect(closed).toEqual(['usgs:q1'])
  })

  it('a relocation out of reach closes it too', async () => {
    const { rerender } = renderHook(({ events }) => useWatchAlarms(events, Date.now()), {
      initialProps: { events: [quake('severe', 6.4)] },
    })
    await flush()
    rerender({ events: [quake('severe', 6.4, 46.0)] }) // relocated 1100 km north
    await flush()
    expect(closed).toEqual(['usgs:q1'])
  })

  it('nothing is closed before the snapshot is whole: "not in the list" then means "not received yet"', async () => {
    useStore.setState({ alarmed: new Map([['usgs:q1', 4]]), snapshotRemaining: 300 })
    renderHook(() => useWatchAlarms([], Date.now()))
    await flush()
    expect(closed).toHaveLength(0)
  })

  it('an event that only grew OLD keeps its notification: the reader may not have seen it', async () => {
    const old = makeEvent({
      id: 'usgs:old',
      severity: 'extreme',
      lat: 35.3,
      lon: 139.4,
      time: new Date(Date.now() - 3 * 3600_000).toISOString(),
    })
    useStore.setState({ alarmed: new Map([['usgs:old', 4]]) })
    renderHook(() => useWatchAlarms([old], Date.now()))
    await flush()
    expect(closed).toHaveLength(0)
  })
})
