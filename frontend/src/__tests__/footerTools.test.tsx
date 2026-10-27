/** The footer's mounts: the climate indicators (lane C2's panel), the
 * offline world map (lane G's service-worker contract) and the sources'
 * freshness fields (lane C2's /api/sources). */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ClimateButton, OfflineMapButton, type BasemapStatus } from '../components/FooterTools'
import { SourcesBar } from '../components/SourcesBar'
import { useStore } from '../store'
import { resetStore } from './helpers'
import type { SourceHealth } from '../types'

beforeEach(() => {
  resetStore()
  localStorage.clear()
})
afterEach(() => vi.unstubAllGlobals())

describe('the climate indicators', () => {
  it('open in a dialog, loaded on demand, and Esc gives the focus back', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}), headers: { get: () => null } })),
    )
    render(<ClimateButton />)
    const button = screen.getByRole('button', { name: /Climate indicators/ })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fetch).not.toHaveBeenCalled() // nothing polled for a reader who never looks
    fireEvent.click(button)
    expect(screen.getByRole('dialog', { name: 'Climate indicators' })).toBeInTheDocument()
    // the panel itself arrives (lazy chunk), and asks the API
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/climate', expect.anything()), { timeout: 3000 })
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(button)
  })
})

/** A service worker answering the page the way public/sw.js does. */
function fakeWorker(status: Partial<BasemapStatus> | { type: string; error: true }) {
  const events = new EventTarget()
  const sent: unknown[] = []
  const worker = Object.assign(events, {
    controller: {
      postMessage: (message: { type: string }, ports?: MessagePort[]) => {
        sent.push(message)
        if (message.type === 'sosforge:basemap-status') ports?.[0]?.postMessage(status)
      },
    },
  })
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: worker })
  const broadcast = (data: unknown) =>
    act(() => {
      events.dispatchEvent(new MessageEvent('message', { data }))
    })
  return { sent, broadcast }
}

// today's measure (lane G): 118 files missing, 15,662,270 B over the sized
// ones, 4 glyph files unsized
const MISSING: BasemapStatus = {
  type: 'sosforge:basemap-status',
  saved: false,
  saving: false,
  files: 132,
  missingFiles: 118,
  missingBytes: 15_662_270,
  unmeasuredFiles: 4,
  unmetered: null,
}

describe('save the world map for offline use', () => {
  it('says the measured size as an "about", saves, shows progress, then the result', async () => {
    const worker = fakeWorker(MISSING)
    render(<OfflineMapButton />)
    const button = await screen.findByRole('button', { name: 'Save the world map for offline use (~16 MB)' })
    fireEvent.click(button)
    expect(worker.sent).toContainEqual({ type: 'sosforge:basemap-save' })
    worker.broadcast({ type: 'sosforge:basemap-progress', done: 3, failed: 0, total: 118, bytes: 400_000 })
    expect(screen.getByText('Saving the world map: 3/118 files')).toBeInTheDocument()
    worker.broadcast({ type: 'sosforge:basemap-saved', files: 118, bytes: 15_662_270 })
    expect(screen.getByText('World map saved for offline use')).toBeInTheDocument()
  })

  it('a failed save says how many files, and the button resumes', async () => {
    const worker = fakeWorker(MISSING)
    render(<OfflineMapButton />)
    fireEvent.click(await screen.findByRole('button', { name: /Save the world map/ }))
    worker.broadcast({ type: 'sosforge:basemap-failed', files: 110, failed: 8, bytes: 14_000_000 })
    expect(await screen.findByText('8 file(s) could not be saved: try again')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Save the world map/ })).toBeInTheDocument()
  })

  it('offline, nothing could be sized: no size is stated', async () => {
    fakeWorker({ ...MISSING, missingBytes: 0, unmeasuredFiles: 118 })
    render(<OfflineMapButton />)
    expect(await screen.findByRole('button', { name: 'Save the world map for offline use' })).toBeInTheDocument()
  })

  it('on mobile data, it says so', async () => {
    fakeWorker({ ...MISSING, unmetered: false })
    render(<OfflineMapButton />)
    expect(await screen.findByRole('button', { name: /~16 MB, mobile data/ })).toBeInTheDocument()
  })

  it('already saved, an error reply, or no worker: no button', async () => {
    fakeWorker({ ...MISSING, saved: true, missingFiles: 0, missingBytes: 0, unmeasuredFiles: 0 })
    const { unmount } = render(<OfflineMapButton />)
    await act(async () => new Promise((resolve) => setTimeout(resolve, 20)))
    expect(screen.queryByRole('button')).toBeNull()
    unmount()
    fakeWorker({ type: 'sosforge:basemap-status', error: true })
    render(<OfflineMapButton />)
    await act(async () => new Promise((resolve) => setTimeout(resolve, 20)))
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('sources: the honest freshness fields', () => {
  const now = Date.parse('2026-10-27T12:00:00Z')
  const source = (extra: Partial<SourceHealth>): SourceHealth => ({
    name: 'bmkg',
    connected: false,
    last_ok: new Date(now - 40_000).toISOString(),
    last_error: 'HTTP 503',
    events_seen: 15,
    errors: 1,
    ...extra,
  })

  it('a re-listed old event is not "last event 40 s ago": last NEW event and newest event apart', () => {
    useStore.setState({
      received: true,
      connected: true,
      sources: [
        source({
          last_event_at: null, // nothing NEW since the server started
          newest_event_time: new Date(now - 29 * 3600_000).toISOString(),
          last_delivery_at: new Date(now - 40_000).toISOString(),
        }),
      ],
    })
    render(<SourcesBar now={now} />)
    fireEvent.click(screen.getByRole('button', { name: /BMKG/ }))
    const value = (label: string) =>
      [...document.querySelectorAll('.source-detail dt')].find((dt) => dt.textContent === label)
        ?.nextElementSibling?.textContent
    expect(value('Last new event')).toBe('none since the server started')
    expect(value('Newest event')).toBe('1 d ago')
    // the poll 40 s ago is said as a poll, never as an event
    expect(value('Last good poll')).toBe('40 s ago')
    expect(document.querySelector('.source-detail')?.textContent).not.toMatch(/Last event/)
  })

  it('a source this client has no label for is named by the server', () => {
    useStore.setState({
      received: true,
      connected: true,
      sources: [source({ name: 'usdm', label: 'US Drought Monitor', connected: true, last_error: null })],
    })
    render(<SourcesBar now={now} />)
    expect(screen.getByText('US Drought Monitor')).toBeInTheDocument()
  })
})
