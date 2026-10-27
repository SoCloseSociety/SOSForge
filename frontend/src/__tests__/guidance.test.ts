/** Prevention guidance is FEATURE-DETECTED: a newer frontend in front of an
 * older API must degrade to today's display, never break, and a line without
 * an official source must never be shown as if it were sourced. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentPhase, guidanceFor, hasGuidance, resetGuidanceCache } from '../guidance'
import { NOW, makeEvent } from './helpers'

type Route = { status?: number; type?: string; body?: unknown; fail?: boolean }

/** A fake server: each path answers what the test says, and every call is
 * counted. */
function serve(routes: Record<string, Route>) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const path = url.split('?')[0]
      const route = routes[path] ?? { status: 404, type: 'application/json', body: { detail: 'Not Found' } }
      if (route.fail) throw new TypeError('network down')
      return {
        ok: (route.status ?? 200) < 400,
        status: route.status ?? 200,
        headers: { get: () => route.type ?? 'application/json' },
        json: async () => route.body,
      }
    }),
  )
  return calls
}

/** `/api/version` of the merged v2 API, verbatim feature list. */
const VERSION = {
  api: 2,
  features: [
    'ws_zones',
    'ws_out_of_scope',
    'ws_error',
    'snapshot_pinned',
    'events_zone',
    'events_cursor',
    'events_etag',
    'guidance',
    'escalated',
  ],
}

/** One line exactly as `GET /api/guidance` serves it (backend/app/guidance.py,
 * `Catalogue._build`). */
const LINE = (text: string, needsReview = false, url = 'https://www.jma.go.jp/jma/en/Activities/earthquake.html') => ({
  text,
  source: { name: 'JMA', url, lang: 'en' },
  needs_review: needsReview,
})

/** The whole body, exactly the merged contract: `{lang, version, phases,
 * lines, needs_review, kinds: {<kind>: {before, during, after}}}`. */
const BODY = {
  lang: 'ja',
  version: 3,
  phases: ['before', 'during', 'after'],
  lines: 3,
  needs_review: 1,
  kinds: {
    earthquake: {
      before: [LINE('Fix furniture to the walls.')],
      during: [LINE('Drop, cover, hold on.')],
      after: [LINE('Expect aftershocks.', true)],
    },
  },
}

beforeEach(() => resetGuidanceCache())
afterEach(() => vi.unstubAllGlobals())

describe('feature detection', () => {
  it('an API without /api/version has no guidance, and is never asked for it', async () => {
    const calls = serve({})
    expect(await guidanceFor('en', 'earthquake')).toBeNull()
    expect(calls).toEqual(['/api/version'])
  })

  it('the SPA fallback (200 with HTML) is not mistaken for an answer', async () => {
    serve({ '/api/version': { type: 'text/html', body: '<!doctype html>' } })
    expect(await hasGuidance()).toBe(false)
  })

  it('a version that does not list the feature is not asked for guidance', async () => {
    const calls = serve({ '/api/version': { body: { api: 2, features: ['events_cursor'] } } })
    expect(await guidanceFor('en', 'earthquake')).toBeNull()
    expect(calls).toEqual(['/api/version'])
  })

  it('a network failure is retried on the next ask, a 404 is not', async () => {
    const calls = serve({ '/api/version': { fail: true } })
    expect(await guidanceFor('en', 'earthquake')).toBeNull()
    expect(await guidanceFor('en', 'earthquake')).toBeNull()
    expect(calls.filter((c) => c === '/api/version')).toHaveLength(2)

    resetGuidanceCache()
    const again = serve({})
    await guidanceFor('en', 'earthquake')
    await guidanceFor('en', 'tsunami')
    expect(again.filter((c) => c === '/api/version')).toHaveLength(1)
  })
})

describe('reading the catalogue (contract v2, exact structure)', () => {
  it('reads every phase, the publisher and the review flag, with the language in the query', async () => {
    const calls = serve({ '/api/version': { body: VERSION }, '/api/guidance': { body: BODY } })
    const found = await guidanceFor('ja', 'earthquake')
    expect(found).toEqual({
      kind: 'earthquake',
      before: [
        {
          text: 'Fix furniture to the walls.',
          source_url: 'https://www.jma.go.jp/jma/en/Activities/earthquake.html',
          source: 'JMA',
          needs_review: false,
        },
      ],
      during: [
        {
          text: 'Drop, cover, hold on.',
          source_url: 'https://www.jma.go.jp/jma/en/Activities/earthquake.html',
          source: 'JMA',
          needs_review: false,
        },
      ],
      after: [
        {
          text: 'Expect aftershocks.',
          source_url: 'https://www.jma.go.jp/jma/en/Activities/earthquake.html',
          source: 'JMA',
          needs_review: true,
        },
      ],
    })
    expect(calls).toContain('/api/guidance?lang=ja')
  })

  it('drops a line with no https source rather than showing it as sourced', async () => {
    serve({
      '/api/version': { body: VERSION },
      '/api/guidance': {
        body: {
          ...BODY,
          kinds: {
            flood: {
              before: [
                { text: 'Move valuables upstairs.', source: null, needs_review: false },
                LINE('Know your evacuation route.', false, 'https://www.ready.gov/floods'),
              ],
              during: [LINE('Never drive through water.', false, 'javascript:alert(1)')],
              after: [LINE('Boil water until told otherwise.', false, 'http://example.org/')],
            },
          },
        },
      },
    })
    const found = await guidanceFor('en', 'flood')
    expect(found?.before.map((l) => l.text)).toEqual(['Know your evacuation route.'])
    expect(found?.during).toEqual([])
    expect(found?.after).toEqual([])
  })

  it('a kind with nothing sourced is absent, so the panel shows what it did before', async () => {
    serve({
      '/api/version': { body: VERSION },
      '/api/guidance': { body: { ...BODY, kinds: { heat: { before: [{ text: 'Drink water.' }] } } } },
    })
    expect(await guidanceFor('en', 'heat')).toBeNull()
  })

  it('a broken catalogue (503) leaves the CAP instruction alone, and is asked again later', async () => {
    const calls = serve({ '/api/version': { body: VERSION }, '/api/guidance': { status: 503, body: {} } })
    expect(await guidanceFor('en', 'earthquake')).toBeNull()
    expect(await guidanceFor('en', 'earthquake')).toBeNull()
    expect(calls.filter((c) => c.startsWith('/api/guidance'))).toHaveLength(2)
  })

  it('an unknown language (422) is an answer: no guidance, and not asked again', async () => {
    const calls = serve({ '/api/version': { body: VERSION }, '/api/guidance': { status: 422, body: {} } })
    expect(await guidanceFor('xx', 'earthquake')).toBeNull()
    expect(await guidanceFor('xx', 'earthquake')).toBeNull()
    expect(calls.filter((c) => c.startsWith('/api/guidance'))).toHaveLength(1)
  })
})

describe('the phase that matters now', () => {
  it('a warning ahead of its onset opens on BEFORE', () => {
    expect(currentPhase(makeEvent({ time: new Date(NOW + 2 * 3600_000).toISOString() }), NOW)).toBe('before')
  })

  it('a running alert or a fresh event opens on DURING', () => {
    expect(currentPhase(makeEvent({ time: new Date(NOW - 10 * 60_000).toISOString() }), NOW)).toBe('during')
    expect(
      currentPhase(makeEvent({ ongoing: true, time: new Date(NOW - 3 * 86400_000).toISOString() }), NOW),
    ).toBe('during')
  })

  it('an old point event opens on AFTER', () => {
    expect(currentPhase(makeEvent({ time: new Date(NOW - 5 * 3600_000).toISOString() }), NOW)).toBe('after')
  })
})
