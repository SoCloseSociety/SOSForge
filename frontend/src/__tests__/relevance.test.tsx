/** What is happening now comes first (orchestrator review, 2026-10-27): a
 * Minor Meteoalarm warning whose onset was two days ahead ("Velebit channel
 * region ... in 2 d") sat at the TOP of the world feed. */
import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { byRelevance, relevanceTime } from '../relevance'
import { useStore } from '../store'
import { makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage } from '../types'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

const NOW = Date.parse('2026-10-27T10:00:00Z')
const iso = (offsetMs: number) => new Date(NOW + offsetMs).toISOString()
const MIN = 60_000
const DAY = 86_400_000

const upcoming = makeEvent({
  id: 'meteoalarm:velebit',
  kind: 'storm',
  severity: 'minor',
  place: 'Velebit channel region',
  time: iso(2 * DAY), // onset, the day after tomorrow
  received_at: iso(-90 * MIN), // reached us an hour and a half ago
  ongoing: true,
})
const quakeNow = makeEvent({ id: 'emsc:now', place: 'Just now quake', time: iso(-1 * MIN), received_at: iso(-1 * MIN) })
const quakeHourAgo = makeEvent({ id: 'emsc:hour', place: 'An hour ago quake', time: iso(-60 * MIN), received_at: iso(-59 * MIN) })
const quakeYesterday = makeEvent({ id: 'emsc:old', place: 'Yesterday quake', time: iso(-DAY), received_at: iso(-DAY) })

describe('relevance order', () => {
  it('an upcoming warning ranks by when it reached us, not by its onset', () => {
    expect(relevanceTime(upcoming, NOW)).toBe(NOW - 90 * MIN)
    const ids = byRelevance([upcoming, quakeYesterday, quakeHourAgo, quakeNow], NOW).map((e) => e.id)
    expect(ids).toEqual(['emsc:now', 'emsc:hour', 'meteoalarm:velebit', 'emsc:old'])
  })

  it('once it starts, it ranks by its onset', () => {
    const later = NOW + 2 * DAY + 5 * MIN
    expect(relevanceTime(upcoming, later)).toBe(NOW + 2 * DAY)
    expect(byRelevance([quakeNow, upcoming], later)[0].id).toBe('meteoalarm:velebit')
  })

  it('a past event keeps its own time', () => {
    expect(relevanceTime(quakeHourAgo, NOW)).toBe(NOW - 60 * MIN)
  })
})

describe('the world feed, rendered', () => {
  beforeEach(() => {
    resetStore()
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    vi.stubGlobal(
      'WebSocket',
      class {
        close() {}
      },
    )
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('shows the quake of a minute ago above the warning that starts in two days', async () => {
    const { default: App } = await import('../App')
    useStore.setState({ scope: 'world' })
    useStore.getState().ingest({
      type: 'snapshot',
      server_time: iso(0),
      events: [upcoming, quakeYesterday, quakeNow],
      stats: makeStats(),
      sources: [],
    } as ServerMessage)
    const { container } = render(<App />)
    const places = [...container.querySelectorAll('.item .place')].map((el) => el.textContent ?? '')
    expect(places[0]).toContain('Just now quake')
    expect(places.findIndex((p) => p.includes('Velebit'))).toBe(1)
    // and it still says when it starts
    expect(container.querySelector('.item:nth-child(2) .age')?.textContent).toBe('in 2 d')
  })
})
