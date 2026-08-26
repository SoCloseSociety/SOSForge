/** Switching from one event to the next must not make the panel lie.
 *
 * The nearby-views request is aborted when the selection changes. An abort
 * rejects the promise, and that rejection lands on the NEXT microtask --
 * after the new effect has already set `loading`. The old request's
 * `catch`/`finally` then wrote their result over the new one's state, and the
 * panel fell into its "no Windy key configured" branch: a permanent-sounding
 * statement about the deployment, shown for the whole duration of a request
 * that was running perfectly well.
 */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LivePanel } from '../components/LivePanel'
import { translate } from '../i18n'
import { useStore } from '../store'
import { NOW, makeEvent, resetStore } from './helpers'

const LOADING = translate('en', 'live.loading')
const NO_KEY = translate('en', 'live.nokey')
const NO_CAMERA = translate('en', 'live.nocamera')

/** Each request stays pending until the test resolves it by event id. */
let pending: Record<string, (payload: unknown) => void>

function stubFetch() {
  pending = {}
  vi.stubGlobal('fetch', (url: string, init?: { signal?: AbortSignal }) => {
    const id = decodeURIComponent(url.split('/api/events/')[1].split('/nearby')[0])
    return new Promise((resolve, reject) => {
      pending[id] = (payload: unknown) =>
        resolve({ json: () => Promise.resolve(payload) } as Response)
      init?.signal?.addEventListener('abort', () =>
        // what the real fetch does on abort, and the whole point of this test
        reject(new DOMException('The user aborted a request.', 'AbortError')),
      )
    })
  })
}

/** Lets the aborted promise's rejection reach its handlers. */
async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  resetStore()
  useStore.getState().setLang('en')
  stubFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('nearby views while the selection moves', () => {
  it('does not flash "no key configured" while the new request is in flight', async () => {
    const first = makeEvent({ id: 'emsc:first' })
    const second = makeEvent({ id: 'emsc:second', lat: 38.1, lon: 141.2 })

    const { rerender } = render(<LivePanel event={first} now={NOW} />)
    rerender(<LivePanel event={second} now={NOW} />)
    await flush()

    expect(screen.queryByText(NO_KEY)).toBeNull()
    expect(screen.getByText(LOADING)).toBeInTheDocument()
  })

  it('shows the previous event nothing of its own while the next one loads', async () => {
    /* The links of the event that was just closed have no business being
     * attributed to the one being opened. */
    const first = makeEvent({ id: 'emsc:first' })
    const second = makeEvent({ id: 'emsc:second', lat: 38.1, lon: 141.2 })

    const { rerender } = render(<LivePanel event={first} now={NOW} />)
    pending['emsc:first']({
      found: true,
      links: [{ id: 'l1', label: 'Windy radar', detail: 'live', url: 'https://example.invalid' }],
      cameras: [],
      cameras_configured: true,
    })
    await flush()
    expect(screen.getByText('Windy radar')).toBeInTheDocument()

    rerender(<LivePanel event={second} now={NOW} />)
    await flush()

    expect(screen.queryByText('Windy radar')).toBeNull()
    expect(screen.getByText(LOADING)).toBeInTheDocument()
  })

  it('still reports a configured deployment with no camera nearby', async () => {
    const event = makeEvent({ id: 'emsc:only' })
    render(<LivePanel event={event} now={NOW} />)
    pending['emsc:only']({ found: true, links: [], cameras: [], cameras_configured: true })
    await flush()

    expect(screen.getByText(NO_CAMERA)).toBeInTheDocument()
    expect(screen.queryByText(NO_KEY)).toBeNull()
  })

  it('still reports a missing key once the answer actually says so', async () => {
    const event = makeEvent({ id: 'emsc:only' })
    render(<LivePanel event={event} now={NOW} />)
    pending['emsc:only']({ found: true, links: [], cameras: [], cameras_configured: false })
    await flush()

    expect(screen.getByText(NO_KEY)).toBeInTheDocument()
  })
})
