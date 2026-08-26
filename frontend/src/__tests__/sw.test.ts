// @vitest-environment jsdom
// jsdom gives `new Request('/')` a base URL to resolve against, which is what
// a real service worker gets from its own origin. In bare node it throws.

/** Tests the REAL public/sw.js, by giving it a service-worker-shaped global
 * and driving the handlers it registers.
 *
 * The worker is hand-written and served verbatim -- no bundler touches it --
 * so a test that re-implements its logic would prove nothing. This one
 * imports the shipped file.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

type Handler = (event: FakeEvent) => void

interface FakeEvent {
  request: Request
  respondWith: (p: Promise<Response> | Response) => void
  waitUntil: (p: Promise<unknown>) => void
}

const handlers = new Map<string, Handler>()

/** A cache store that behaves like the parts of the CacheStorage API the
 * worker actually uses. */
function makeCaches() {
  const stores = new Map<string, Map<string, Response>>()
  const key = (r: Request | string) => (typeof r === 'string' ? r : new URL(r.url).pathname)
  return {
    stores,
    api: {
      open: async (name: string) => {
        if (!stores.has(name)) stores.set(name, new Map())
        const store = stores.get(name)!
        return {
          addAll: async (requests: Request[]) => {
            for (const r of requests) store.set(key(r), new Response('precached shell'))
          },
          put: async (r: Request, res: Response) => store.set(key(r), res),
          match: async (r: Request) => store.get(key(r)),
        }
      },
      match: async (r: Request | string) => {
        for (const store of stores.values()) {
          const hit = store.get(key(r))
          if (hit) return hit
        }
        return undefined
      },
      keys: async () => [...stores.keys()],
      delete: async (name: string) => stores.delete(name),
    },
  }
}

let cacheMock: ReturnType<typeof makeCaches>

async function loadWorker() {
  handlers.clear()
  cacheMock = makeCaches()
  vi.stubGlobal('self', {
    addEventListener: (type: string, fn: Handler) => handlers.set(type, fn),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn() },
    location: { origin: 'https://sosforge.soclose.co' },
  })
  vi.stubGlobal('caches', cacheMock.api)
  // In a real worker a relative URL resolves against the worker's own scope.
  // Under vitest, `Request` is undici's and rejects anything relative, so the
  // worker's own precache list would throw before we could test anything.
  const NativeRequest = globalThis.Request
  class ScopedRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(
        typeof input === 'string' && input.startsWith('/')
          ? `https://sosforge.soclose.co${input}`
          : input,
        init,
      )
    }
  }
  vi.stubGlobal('Request', ScopedRequest)
  vi.resetModules()
  // @ts-expect-error -- a plain worker script, not a typed module
  await import('../../public/sw.js')
}

/** Drives one fetch event and returns what the worker answered, or the string
 * 'passthrough' when it declined to intercept (the browser handles it). */
async function fetchThrough(path: string, mode = 'navigate') {
  // A plain request-shaped object, not a real `Request`: undici refuses to
  // construct one with mode 'navigate', which is precisely the mode a page
  // load has and therefore the one that matters here. The worker only reads
  // .method, .url and .mode, and hands the object to fetch/caches, both mocked.
  const request = { method: 'GET', url: `https://sosforge.soclose.co${path}`, mode } as Request
  let answered: Promise<Response> | Response | null = null
  const event: FakeEvent = {
    request,
    respondWith: (p) => {
      answered = p
    },
    waitUntil: () => {},
  }
  handlers.get('fetch')!(event)
  if (answered === null) return 'passthrough'
  return await answered
}

beforeEach(async () => {
  await loadWorker()
})

describe('the app shell must never be served stale to an online user', () => {
  it('asks the network first for a navigation, even after precaching', async () => {
    // Precache first, exactly as `install` does.
    await handlersInstall()

    const network = vi.fn(async () => new Response('FRESH shell from the network'))
    vi.stubGlobal('fetch', network)

    const response = await fetchThrough('/')

    expect(response).not.toBe('passthrough')
    expect(await (response as unknown as Response).text()).toBe('FRESH shell from the network')
    expect(network).toHaveBeenCalled()
  })

  it('serves the cached shell when the network is gone', async () => {
    await handlersInstall()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline')
      }),
    )

    const response = await fetchThrough('/')

    expect(await (response as unknown as Response).text()).toBe('precached shell')
  })

  it('still serves hashed assets from cache without touching the network', async () => {
    // Content-hashed URLs: a hit can never be stale, and this is what makes
    // the app open instantly on a bad network.
    const cache = await cacheMock.api.open('sosforge-runtime-v1')
    await cache.put(
      new Request('https://sosforge.soclose.co/assets/index-abc123.js'),
      new Response('cached bundle'),
    )
    const network = vi.fn(async () => new Response('should not be called'))
    vi.stubGlobal('fetch', network)

    const response = await fetchThrough('/assets/index-abc123.js', 'no-cors')

    expect(await (response as unknown as Response).text()).toBe('cached bundle')
    expect(network).not.toHaveBeenCalled()
  })

  it('never intercepts the live feed', async () => {
    for (const path of ['/api/events', '/api/sources', '/ws', '/healthz']) {
      expect(await fetchThrough(path, 'cors')).toBe('passthrough')
    }
  })
})

async function handlersInstall() {
  const waits: Promise<unknown>[] = []
  handlers.get('install')!({
    request: new Request('https://x/'),
    respondWith: () => {},
    waitUntil: (p) => waits.push(p),
  })
  await Promise.all(waits)
}
