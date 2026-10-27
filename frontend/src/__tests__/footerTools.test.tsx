/** The footer's mounts: the climate indicators (lane C2's panel), the
 * offline world map (lane G's service-worker contract) and the sources'
 * freshness fields (lane C2's /api/sources). */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ClimateButton, OfflineMapButton, STALL_MS, STATUS_DELAY_MS, type BasemapStatus } from '../components/FooterTools'
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

/** The page is ready (snapshot whole), then the 20 s the worker is given
 * before it is asked anything. */
async function ready(): Promise<void> {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  act(() => useStore.setState({ received: true, snapshotRemaining: 0, connected: true }))
  act(() => {
    vi.advanceTimersByTime(STATUS_DELAY_MS)
  })
  vi.useRealTimers()
}

describe('save the world map for offline use', () => {
  it('the worker is not asked before the snapshot is whole and 20 s have passed (#11)', async () => {
    const worker = fakeWorker(MISSING)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    render(<OfflineMapButton />)
    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    expect(worker.sent).toEqual([]) // no snapshot yet: nothing asked
    act(() => useStore.setState({ received: true, snapshotRemaining: 0 }))
    act(() => {
      vi.advanceTimersByTime(STATUS_DELAY_MS - 1)
    })
    expect(worker.sent).toEqual([])
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(worker.sent).toEqual([{ type: 'sosforge:basemap-status' }])
    vi.useRealTimers()
  })

  it('says the measured size as an "about", saves on ONE button that keeps the focus, then the result', async () => {
    const worker = fakeWorker(MISSING)
    render(<OfflineMapButton />)
    await ready()
    const button = await screen.findByRole('button', { name: 'Save the world map for offline use (~16 MB)' })
    button.focus()
    fireEvent.click(button)
    expect(worker.sent).toContainEqual({ type: 'sosforge:basemap-save' })
    worker.broadcast({ type: 'sosforge:basemap-progress', done: 3, failed: 0, total: 118, bytes: 400_000 })
    // the same button shows the progress, and still has the focus (#12)
    expect(button).toHaveTextContent('Saving the world map: 3/118 files')
    expect(document.activeElement).toBe(button)
    expect(button).toHaveAttribute('aria-disabled', 'true')
    // the status region was there before, and says the milestone
    expect(screen.getByRole('status')).toHaveTextContent('Saving the world map...')
    worker.broadcast({ type: 'sosforge:basemap-saved', files: 118, bytes: 15_662_270 })
    expect(button).toHaveTextContent('World map saved for offline use')
    expect(screen.getByRole('status')).toHaveTextContent('World map saved for offline use')
  })

  it('a failed save says how many files, and the button resumes', async () => {
    const worker = fakeWorker(MISSING)
    render(<OfflineMapButton />)
    await ready()
    fireEvent.click(await screen.findByRole('button', { name: /Save the world map/ }))
    worker.broadcast({ type: 'sosforge:basemap-failed', files: 110, failed: 8, bytes: 14_000_000 })
    expect(await screen.findByRole('button', { name: /Save the world map for offline use \(~16 MB\)/ })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('8 file(s) could not be saved: try again')
  })

  it('a worker that dies mid-save is asked how it stands, and the stop is said (#5)', async () => {
    let saving = false
    const events = new EventTarget()
    const sent: { type: string }[] = []
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: Object.assign(events, {
        controller: {
          postMessage: (message: { type: string }, ports?: MessagePort[]) => {
            sent.push(message)
            if (message.type === 'sosforge:basemap-save') saving = true
            if (message.type === 'sosforge:basemap-status') ports?.[0]?.postMessage({ ...MISSING, saving })
          },
        },
      }),
    })
    render(<OfflineMapButton />)
    await ready()
    const button = await screen.findByRole('button', { name: /Save the world map/ })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    fireEvent.click(button)
    act(() => {
      events.dispatchEvent(new MessageEvent('message', { data: { type: 'sosforge:basemap-progress', done: 63, failed: 0, total: 114 } }))
    })
    // the worker is killed: no more word; a restarted one is not saving
    saving = false
    act(() => {
      vi.advanceTimersByTime(STALL_MS * 2)
    })
    vi.useRealTimers()
    expect(sent.filter((m) => m.type === 'sosforge:basemap-status').length).toBeGreaterThan(1)
    // said in the status region, and shown beside the button
    await vi.waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('The download stopped (63/114 files): start it again'),
    )
    expect(document.querySelector('.footer-tool-note')).toHaveTextContent('The download stopped (63/114 files)')
    expect(screen.getByRole('button', { name: /Save the world map for offline use/ })).not.toHaveAttribute('aria-disabled', 'true')
  })

  it('offline, nothing could be sized: no size is stated', async () => {
    fakeWorker({ ...MISSING, missingBytes: 0, unmeasuredFiles: 118 })
    render(<OfflineMapButton />)
    await ready()
    expect(await screen.findByRole('button', { name: 'Save the world map for offline use' })).toBeInTheDocument()
  })

  it('many files unsized: "at least", never a cost under half the real one (#10)', async () => {
    fakeWorker({ ...MISSING, missingFiles: 84, unmeasuredFiles: 50, missingBytes: 6_100_000 })
    render(<OfflineMapButton />)
    await ready()
    expect(await screen.findByRole('button', { name: 'Save the world map for offline use (at least 6 MB)' })).toBeInTheDocument()
  })

  it('on mobile data, it says so', async () => {
    fakeWorker({ ...MISSING, unmetered: false })
    render(<OfflineMapButton />)
    await ready()
    expect(await screen.findByRole('button', { name: /~16 MB, mobile data/ })).toBeInTheDocument()
  })

  it('on Save-Data, nothing is asked by itself: the button asks first (#11)', async () => {
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } })
    const worker = fakeWorker(MISSING)
    render(<OfflineMapButton />)
    await ready()
    expect(worker.sent).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Save the world map for offline use' }))
    expect(await screen.findByRole('button', { name: /~16 MB/ })).toBeInTheDocument()
    expect(worker.sent).toEqual([{ type: 'sosforge:basemap-status' }])
    Object.defineProperty(navigator, 'connection', { configurable: true, value: undefined })
  })

  it('already saved, an error reply, or no worker: no button', async () => {
    fakeWorker({ ...MISSING, saved: true, missingFiles: 0, missingBytes: 0, unmeasuredFiles: 0 })
    const { unmount } = render(<OfflineMapButton />)
    await ready()
    await act(async () => new Promise((resolve) => setTimeout(resolve, 20)))
    expect(screen.queryByRole('button')).toBeNull()
    unmount()
    resetStore()
    fakeWorker({ type: 'sosforge:basemap-status', error: true })
    render(<OfflineMapButton />)
    await ready()
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
