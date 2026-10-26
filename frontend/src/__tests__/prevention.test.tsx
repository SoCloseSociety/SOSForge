/** The prevention block in the event panel, against the merged v2 contract:
 * shown when the API serves it, under the CAP instruction (never instead of
 * it), opened on the phase that matters now, and honest about wording that
 * is still under review. Without the feature the panel is what it was. */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LivePanel } from '../components/LivePanel'
import { resetGuidanceCache } from '../guidance'
import { useStore } from '../store'
import { NOW, makeEvent, resetStore } from './helpers'

const JSON_TYPE = { get: () => 'application/json' }
const line = (text: string, needs_review = false) => ({
  text,
  source: { name: 'Ready.gov', url: 'https://www.ready.gov/earthquakes', lang: 'en' },
  needs_review,
})

function serve(withGuidance: boolean) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/version') {
        return withGuidance
          ? { ok: true, status: 200, headers: JSON_TYPE, json: async () => ({ api: 2, features: ['guidance', 'escalated'] }) }
          : { ok: false, status: 404, headers: JSON_TYPE, json: async () => ({}) }
      }
      if (url.startsWith('/api/guidance')) {
        return {
          ok: true,
          status: 200,
          headers: JSON_TYPE,
          json: async () => ({
            lang: 'en',
            version: 1,
            phases: ['before', 'during', 'after'],
            lines: 3,
            needs_review: 1,
            kinds: {
              earthquake: {
                before: [line('Secure heavy furniture.')],
                during: [line('Drop, cover, and hold on.'), line('Stay inside until it stops.')],
                after: [line('Expect aftershocks.', true)],
              },
            },
          }),
        }
      }
      return { ok: true, status: 200, headers: JSON_TYPE, json: async () => ({ links: [] }) }
    }),
  )
}

const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })

beforeEach(() => {
  resetStore()
  resetGuidanceCache()
  useStore.getState().setLang('en')
})
afterEach(() => vi.unstubAllGlobals())

describe('prevention in the event panel', () => {
  const quake = makeEvent({
    time: new Date(NOW - 10 * 60_000).toISOString(),
    instruction: 'Stay away from damaged buildings.',
  })

  it('opens on DURING for a quake ten minutes old, and keeps the CAP instruction above it', async () => {
    serve(true)
    render(<LivePanel event={quake} now={NOW} />)
    await flush()
    expect(screen.getByText('Stay away from damaged buildings.')).toBeInTheDocument()
    expect(screen.getByText('Prevention')).toBeInTheDocument()
    expect(screen.getByText('Drop, cover, and hold on.')).toBeInTheDocument()
    expect(screen.queryByText('Secure heavy furniture.')).toBeNull()
    // two lines from the same page: ONE link, on the last of them
    const links = screen.getAllByRole('link', { name: /Ready\.gov/ })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', 'https://www.ready.gov/earthquakes')
    expect(links[0].closest('li')?.textContent).toContain('Stay inside until it stops.')
  })

  it('says a line is under review instead of presenting it as official wording', async () => {
    serve(true)
    render(<LivePanel event={quake} now={NOW} />)
    await flush()
    fireEvent.click(screen.getAllByRole('button', { name: 'Show more' })[0])
    expect(screen.getByText('Expect aftershocks.')).toBeInTheDocument()
    expect(screen.getAllByText('Wording under review')).toHaveLength(1)
  })

  it('without the feature, the panel is what it was: the CAP instruction, no prevention', async () => {
    serve(false)
    render(<LivePanel event={quake} now={NOW} />)
    await flush()
    expect(screen.getByText('Stay away from damaged buildings.')).toBeInTheDocument()
    expect(screen.queryByText('Prevention')).toBeNull()
  })
})

describe('purge reasons of the v2 contract', () => {
  it('an EXPIRED alert leaves like a lifted one, selection and halo included', () => {
    const { ingest } = useStore.getState()
    ingest({
      type: 'snapshot',
      server_time: new Date(NOW).toISOString(),
      events: [makeEvent({ id: 'nws:old' }), makeEvent({ id: 'nws:keep' })],
      stats: useStore.getState().stats ?? {
        total_buffered: 0,
        last_hour: 0,
        earthquakes_last_hour: 0,
        max_magnitude_last_hour: null,
        tsunami_active: 0,
        by_source: {},
        server_time: new Date(NOW).toISOString(),
      },
      sources: [],
    })
    useStore.setState({ selected: 'nws:old', fresh: new Set(['nws:old']) })
    ingest({ type: 'purge', ids: ['nws:old'], reason: 'expired' })
    const state = useStore.getState()
    expect(state.events.map((e) => e.id)).toEqual(['nws:keep'])
    expect(state.selected).toBeNull()
    expect(state.fresh.has('nws:old')).toBe(false)
  })
})
