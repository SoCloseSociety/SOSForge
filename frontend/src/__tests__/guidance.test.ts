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

const VERSION = { api: 2, features: ['events_cursor', 'guidance'], app: '1.1.0' }
const LINE = (text: string, url = 'https://www.jma.go.jp/jma/en/Activities/earthquake.html') => ({
  text,
  source_url: url,
  source: 'JMA',
})

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

describe('reading the catalogue', () => {
  it('reads kinds keyed by name, with the language in the query', async () => {
    const calls = serve({
      '/api/version': { body: VERSION },
      '/api/guidance': {
        body: {
          lang: 'ja',
          kinds: {
            earthquake: {
              before: [LINE('Fix furniture to the walls.')],
              during: [LINE('Drop, cover, hold on.')],
              after: [LINE('Expect aftershocks.')],
            },
          },
        },
      },
    })
    const found = await guidanceFor('ja', 'earthquake')
    expect(found?.during[0]).toEqual({
      text: 'Drop, cover, hold on.',
      source_url: 'https://www.jma.go.jp/jma/en/Activities/earthquake.html',
      source: 'JMA',
    })
    expect(calls).toContain('/api/guidance?lang=ja')
  })

  it('reads kinds as a list, phases under `phases`, and a nested source object', async () => {
    serve({
      '/api/version': { body: VERSION },
      '/api/guidance': {
        body: {
          kinds: [
            {
              kind: 'tsunami',
              phases: {
                during: [
                  { text: 'Go to high ground now.', source: { name: 'NOAA', url: 'https://www.tsunami.gov/' } },
                ],
              },
            },
          ],
        },
      },
    })
    const found = await guidanceFor('en', 'tsunami')
    expect(found?.during).toEqual([
      { text: 'Go to high ground now.', source_url: 'https://www.tsunami.gov/', source: 'NOAA' },
    ])
  })

  it('drops a line with no official URL rather than showing it as sourced', async () => {
    serve({
      '/api/version': { body: VERSION },
      '/api/guidance': {
        body: {
          kinds: {
            flood: {
              before: [{ text: 'Move valuables upstairs.' }, LINE('Know your evacuation route.', 'https://www.ready.gov/floods')],
              during: [{ text: 'Never drive through water.', source_url: 'javascript:alert(1)' }],
            },
          },
        },
      },
    })
    const found = await guidanceFor('en', 'flood')
    expect(found?.before.map((l) => l.text)).toEqual(['Know your evacuation route.'])
    expect(found?.during).toEqual([])
  })

  it('a kind with nothing sourced is absent, so the panel shows what it did before', async () => {
    serve({
      '/api/version': { body: VERSION },
      '/api/guidance': { body: { kinds: { heat: { before: [{ text: 'Drink water.' }] } } } },
    })
    expect(await guidanceFor('en', 'heat')).toBeNull()
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
