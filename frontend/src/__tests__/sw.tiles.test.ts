// @vitest-environment jsdom
/** The basemap side of the REAL public/sw.js, and the two install-time
 * defects fixed with it.
 *
 * Same approach as sw.test.ts: a service-worker-shaped global, the shipped
 * file imported as is, its handlers driven by hand. The cache store here is
 * richer (per-cache keys/delete, insertion order), because trimming and
 * build-independent tile keys are exactly what is under test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const ORIGIN = 'https://sosforge.soclose.co'
const HOST = 'https://tiles.openfreemap.org'
const BUILD = '20261025_080012_pt'
const NEWER = '20261101_080000_pt'

// The project carries no Node typings, hence the untyped specifier (as in
// contrast.test.ts). vitest runs from the frontend root.
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as {
  readFileSync(path: string, encoding: string): string
}
const root = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
const swSource = fs.readFileSync(`${root}/public/sw.js`, 'utf8')

type Handler = (event: FakeEvent) => void
interface FakeEvent {
  request?: Request
  data?: unknown
  respondWith: (p: Promise<Response> | Response) => void
  waitUntil: (p: Promise<unknown>) => void
}

const handlers = new Map<string, Handler>()
const stores = new Map<string, Map<string, Response>>()
let connection: { saveData?: boolean; effectiveType?: string; type?: string } = {}
/** Host paths answered without a Content-Length. */
const unmeasured = new Set<string>()
/** The one open tab the worker can talk to. */
const tab = { postMessage: vi.fn() }
let network: ReturnType<typeof vi.fn>
let calls: string[] = []

const abs = (r: Request | string) => (typeof r === 'string' ? new URL(r, ORIGIN).href : r.url)

function cacheApi() {
  const open = async (name: string) => {
    if (!stores.has(name)) stores.set(name, new Map())
    const store = stores.get(name)!
    return {
      addAll: async (requests: Request[]) => {
        for (const r of requests) store.set(abs(r), new Response(`precached ${new URL(abs(r)).pathname}`))
      },
      put: async (r: Request | string, res: Response) => {
        store.delete(abs(r)) // a re-put moves to the end, like the real one
        store.set(abs(r), res)
      },
      match: async (r: Request | string) => store.get(abs(r))?.clone(),
      keys: async () => [...store.keys()].map((u) => new Request(u)),
      delete: async (r: Request | string) => store.delete(abs(r)),
    }
  }
  return {
    open,
    match: async (r: Request | string) => {
      for (const store of stores.values()) {
        const hit = store.get(abs(r))
        if (hit) return hit.clone()
      }
      return undefined
    },
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
  }
}

/** A network that answers like OpenFreeMap and like our origin. */
function answer(url: string): Response {
  const u = new URL(url)
  let res: Response
  if (url === `${HOST}/styles/dark`) {
    res = Response.json({
      sources: { openmaptiles: { type: 'vector', url: `${HOST}/planet` } },
      sprite: `${HOST}/sprites/ofm_f384/ofm`,
      glyphs: `${HOST}/fonts/{fontstack}/{range}.pbf`,
      layers: [{ id: 'place', layout: { 'text-font': ['Noto Sans Regular'] } }, { id: 'water' }],
    })
  } else if (url === `${HOST}/planet`) {
    res = Response.json({ tiles: [`${HOST}/planet/${BUILD}/{z}/{x}/{y}.pbf`] })
  } else if (u.origin === HOST) {
    // Content-Length as the host sends it (the compressed size, here the
    // body's), except for the files a test wants left unmeasured.
    const body = `bytes of ${u.pathname}`
    res = new Response(body, unmeasured.has(u.pathname) ? {} : { headers: { 'content-length': String(body.length) } })
  } else if (u.pathname === '/index.html') {
    res = new Response(
      '<script type="module" src="/assets/index-AAA.js"></script><link rel="stylesheet" href="/assets/index-BBB.css">',
      { headers: { 'content-type': 'text/html' } },
    )
  } else if (u.pathname === '/assets/index-AAA.js') {
    res = new Response(
      'const d=["assets/MapView-CCC.js","assets/MapView-DDD.css"];import("./MapView-CCC.js")',
    )
  } else if (u.pathname === '/assets/index-BBB.css') {
    res = new Response(
      '@font-face{src:url(/assets/plex-latin-400-EEE.woff2) format("woff2"),url(/assets/plex-latin-400-FFF.woff) format("woff")}',
    )
  } else if (u.pathname === '/assets/MapView-CCC.js') {
    res = new Response('import{a}from"./index-AAA.js"')
  } else {
    res = new Response(`origin ${u.pathname}`)
  }
  Object.defineProperty(res, 'url', { value: url })
  return res
}

async function load() {
  handlers.clear()
  stores.clear()
  calls = []
  connection = {}
  unmeasured.clear()
  tab.postMessage.mockClear()
  vi.stubGlobal('self', {
    addEventListener: (type: string, fn: Handler) => handlers.set(type, fn),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn(async () => {}), matchAll: vi.fn(async () => [tab]) },
    location: { origin: ORIGIN },
    navigator: {
      get connection() {
        return connection
      },
    },
  })
  vi.stubGlobal('caches', cacheApi())
  network = vi.fn(async (input: Request | string) => {
    const url = abs(input)
    calls.push(url)
    return answer(url)
  })
  vi.stubGlobal('fetch', network)
  const NativeRequest = globalThis.Request
  class ScopedRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' && input.startsWith('/') ? `${ORIGIN}${input}` : input, init)
    }
  }
  vi.stubGlobal('Request', ScopedRequest)
  vi.resetModules()
  // @ts-expect-error -- a plain worker script, not a typed module
  await import('../../public/sw.js')
}

/** One fetch event. Returns the answer (or 'passthrough') and the promises
 * the worker asked to be kept alive for. */
async function fetchThrough(
  url: string,
  mode = 'cors',
): Promise<{ response: Response | 'passthrough'; kept: Promise<unknown>[] }> {
  const request = { method: 'GET', url, mode } as Request
  let answered = null as Promise<Response> | Response | null
  const kept: Promise<unknown>[] = []
  handlers.get('fetch')!({
    request,
    respondWith: (p) => {
      answered = p
    },
    waitUntil: (p) => kept.push(p),
  })
  if (answered === null) return { response: 'passthrough', kept }
  return { response: await answered, kept }
}

function res(r: Response | 'passthrough'): Response {
  if (r === 'passthrough') throw new Error('the worker did not answer')
  return r
}

const tile = (z: number, x: number, y: number, build = BUILD) => `${HOST}/planet/${build}/${z}/${x}/${y}.pbf`
const store = (prefix: string) => [...stores.entries()].find(([name]) => name.startsWith(prefix))?.[1]

beforeEach(async () => {
  await load()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the basemap stays in step with the map', () => {
  it('requests the same style the map component loads', async () => {
    const mapView = fs.readFileSync(`${root}/src/components/MapView.tsx`, 'utf8')
    const inMap = /const BASEMAP_STYLE_URL = '([^']+)'/.exec(mapView)?.[1]
    const inWorker = /const STYLE_URL = `\$\{TILE_HOST\}([^`]+)`/.exec(swSource)?.[1]
    expect(inMap).toBe(`${HOST}${inWorker}`)
  })
})

// The precache is exercised on its own below; here it must not start (it
// would wait 20 s, then fetch the world). Save-Data makes it a no-op.
const quietPrecache = () => {
  connection = { saveData: true }
}

describe('tiles', () => {
  beforeEach(quietPrecache)

  it('stores a tile once, under a key that survives the next build, and serves it offline', async () => {
    const first = await fetchThrough(tile(2, 1, 1))
    expect(await res(first.response).text()).toBe(`bytes of /planet/${BUILD}/2/1/1.pbf`)
    expect(store('sosforge-tiles-base')!.has(`${HOST}/planet/2/1/1.pbf`)).toBe(true)

    network.mockImplementation(async () => {
      throw new TypeError('offline')
    })
    const offline = await fetchThrough(tile(2, 1, 1))
    expect(await res(offline.response).text()).toBe(`bytes of /planet/${BUILD}/2/1/1.pbf`)
  })

  it('serves an older build at once and refreshes it behind', async () => {
    await fetchThrough(tile(5, 10, 12))
    calls = []
    const later = await fetchThrough(tile(5, 10, 12, NEWER))
    expect(await res(later.response).text()).toBe(`bytes of /planet/${BUILD}/5/10/12.pbf`)
    await Promise.all(later.kept)
    expect(calls).toContain(tile(5, 10, 12, NEWER))
    const refreshed = await store('sosforge-tiles-t')!.get(`${HOST}/planet/5/10/12.pbf`)!.clone().text()
    expect(refreshed).toBe(`bytes of /planet/${NEWER}/5/10/12.pbf`)
  })

  it('never refetches a tile of the same build', async () => {
    await fetchThrough(tile(6, 1, 2))
    calls = []
    const again = await fetchThrough(tile(6, 1, 2))
    await Promise.all(again.kept)
    expect(calls.filter((c) => c.includes('/planet/') && c.endsWith('.pbf'))).toEqual([])
  })

  it('caps the browsed tiles, oldest first, and never trims the world', async () => {
    const browsed = 403
    for (let i = 0; i < browsed; i++) {
      const { kept } = await fetchThrough(tile(8, i, 0))
      await Promise.all(kept)
    }
    await fetchThrough(tile(1, 0, 0))
    const deep = store('sosforge-tiles-t1')!
    expect(deep.size).toBe(400)
    expect(deep.has(`${HOST}/planet/8/0/0.pbf`)).toBe(false)
    expect(deep.has(`${HOST}/planet/8/402/0.pbf`)).toBe(true)
    expect(store('sosforge-tiles-base')!.has(`${HOST}/planet/1/0/0.pbf`)).toBe(true)
  })
})

describe('style and TileJSON', () => {
  beforeEach(quietPrecache)

  it('answer from the cache when the network is gone', async () => {
    await fetchThrough(`${HOST}/styles/dark`)
    network.mockImplementation(async () => {
      throw new TypeError('offline')
    })
    const { response } = await fetchThrough(`${HOST}/styles/dark`)
    expect((await res(response).json()).glyphs).toContain('{fontstack}')
  })

  it('answer from the cache at once while fresh, and refresh behind', async () => {
    await fetchThrough(`${HOST}/planet`)
    calls = []
    const { response, kept } = await fetchThrough(`${HOST}/planet`)
    expect((await res(response).json()).tiles[0]).toContain(BUILD)
    await Promise.all(kept)
    expect(calls).toEqual([`${HOST}/planet`])
  })
})

/** The automatic run, as the map starting triggers it: through a request to
 * the tile host, 20 s of (fake) idle time later. */
async function autoRun() {
  vi.useFakeTimers()
  const { kept } = await fetchThrough(`${HOST}/styles/dark`)
  await vi.advanceTimersByTimeAsync(20_000)
  return await kept[0]
}

/** A page message; resolves with what the worker kept itself alive for, and
 * collects what it replied on the port. */
async function message(type: string) {
  const replies: unknown[] = []
  let kept: Promise<unknown> | undefined
  handlers.get('message')!({
    data: { type },
    ports: [{ postMessage: (m: unknown) => replies.push(m) }],
    respondWith: () => {},
    waitUntil: (p: Promise<unknown>) => {
      kept = p
    },
  } as unknown as FakeEvent)
  return { result: await kept, replies }
}

const broadcasts = () => tab.postMessage.mock.calls.map((c) => c[0] as { type: string; [k: string]: unknown })

describe('what the worker downloads on its own', () => {
  it('is the small set only: world zooms 0..1, its glyphs, the sprites', async () => {
    expect(await autoRun()).toBe('auto fetched 28') // 5 tiles + 4 sprite files + 19 glyph ranges
    const base = store('sosforge-tiles-base')!
    expect(base.has(`${HOST}/planet/1/1/1.pbf`)).toBe(true)
    expect(base.has(`${HOST}/planet/2/0/0.pbf`)).toBe(false)
    expect(base.has(`${HOST}/sprites/ofm_f384/ofm@2x.png`)).toBe(true)
    // encoded exactly as the page's own request is
    expect(base.has(`${HOST}/fonts/Noto%20Sans%20Regular/0-255.pbf`)).toBe(true)
    expect(base.has(`${HOST}/fonts/Noto%20Sans%20Regular/512-767.pbf`)).toBe(false)
    expect(broadcasts()).toEqual([]) // nothing to report: nobody asked
  })

  it('stays small when the connection type is cellular or not told at all', async () => {
    connection = { type: 'cellular' }
    expect(await autoRun()).toBe('auto fetched 28')
    await load()
    connection = {} // desktop Chrome, Safari, Firefox: no `type`, never assumed
    expect(await autoRun()).toBe('auto fetched 28')
  })

  it('takes the whole world only on a connection that says it is unmetered', async () => {
    connection = { type: 'wifi' }
    expect(await autoRun()).toBe('world fetched 112') // 85 tiles + 4 sprites + 23 glyph ranges
    await load()
    connection = { type: 'wifi', saveData: true }
    expect(await autoRun()).toBe('skipped')
  })

  /** Switches the connection after `after` fetches of world tiles above the
   * small set (zooms 2..3), and counts those fetches. */
  function leaveWifiAfter(after: number, to: typeof connection) {
    let worldTiles = 0
    network.mockImplementation(async (input: Request | string) => {
      const url = abs(input)
      calls.push(url)
      if (/\/planet\/[^/]+\/[23]\//.test(url) && ++worldTiles === after) connection = to
      return answer(url)
    })
    return () => worldTiles
  }

  for (const [left, to] of [
    ['for mobile data', { type: 'cellular' }],
    ['for a link that no longer says', {}],
    ['into Save-Data', { type: 'wifi', saveData: true }],
  ] as const) {
    it(`stops the world the moment the phone leaves the Wi-Fi ${left}, small set complete`, async () => {
      connection = { type: 'wifi' }
      const fetched = leaveWifiAfter(5, to)
      expect(await autoRun()).toBe('world stopped after 33') // 28 small + 5 world tiles
      expect(fetched()).toBe(5)
      const base = store('sosforge-tiles-base')!
      for (const key of [
        `${HOST}/planet/1/1/1.pbf`,
        `${HOST}/sprites/ofm_f384/ofm@2x.png`,
        `${HOST}/fonts/Noto%20Sans%20Regular/11520-11775.pbf`,
      ]) {
        expect(base.has(key), key).toBe(true)
      }
      expect(broadcasts()).toEqual([]) // nothing was asked, nothing is announced
    })
  }

  it('lets the reader finish over mobile data what the Wi-Fi run left', async () => {
    connection = { type: 'wifi' }
    leaveWifiAfter(5, { type: 'cellular' })
    await autoRun()
    vi.useRealTimers()
    expect((await message('sosforge:basemap-save')).result).toBe('world fetched 79')
  })

  it('does nothing at all on Save-Data or 2G', async () => {
    connection = { saveData: true }
    expect(await autoRun()).toBe('skipped')
    connection = { effectiveType: 'slow-2g' }
    await load()
    connection = { effectiveType: 'slow-2g' }
    expect(await autoRun()).toBe('skipped')
    expect(calls.filter((c) => c.includes('/planet/'))).toEqual([])
  })
})

describe('the world map, on the reader\'s request', () => {
  it('states the real size of what is missing before anything is downloaded', async () => {
    await autoRun()
    vi.useRealTimers()
    calls = []
    const { replies } = await message('sosforge:basemap-status')
    const status = replies[0] as Record<string, unknown>
    // the 84 files the world adds: 80 tiles at zooms 2..3 and 4 glyph ranges
    const expected = [] as number[]
    for (let z = 2; z <= 3; z++) for (let x = 0; x < 2 ** z; x++) for (let y = 0; y < 2 ** z; y++) {
      expected.push(`bytes of /planet/${BUILD}/${z}/${x}/${y}.pbf`.length)
    }
    for (const r of ['512-767', '1792-2047', '3840-4095', '6144-6399']) {
      expected.push(`bytes of /fonts/Noto%20Sans%20Regular/${r}.pbf`.length)
    }
    expect(status).toMatchObject({
      type: 'sosforge:basemap-status',
      saved: false,
      saving: false,
      files: 112,
      missingFiles: 84,
      missingBytes: expected.reduce((a, b) => a + b, 0),
      unmeasuredFiles: 0,
      unmetered: null,
    })
    // measured by HEAD: nothing was downloaded to find out
    expect(store('sosforge-tiles-base')!.has(`${HOST}/planet/2/0/0.pbf`)).toBe(false)
  })

  it('counts the files the host does not size, and never guesses them', async () => {
    // as the real host does (2026-10-27): glyph files carry no Content-Length
    const glyphs = ['512-767', '1792-2047', '3840-4095', '6144-6399']
    for (const r of glyphs) unmeasured.add(`/fonts/Noto%20Sans%20Regular/${r}.pbf`)
    await autoRun()
    vi.useRealTimers()
    const { replies } = await message('sosforge:basemap-status')
    let tiles = 0
    for (let z = 2; z <= 3; z++) for (let x = 0; x < 2 ** z; x++) for (let y = 0; y < 2 ** z; y++) {
      tiles += `bytes of /planet/${BUILD}/${z}/${x}/${y}.pbf`.length
    }
    expect(replies[0]).toMatchObject({ missingFiles: 84, missingBytes: tiles, unmeasuredFiles: 4 })
  })

  it('offline, measures nothing and says so', async () => {
    await autoRun()
    vi.useRealTimers()
    network.mockImplementation(async () => {
      throw new TypeError('offline')
    })
    const { replies } = await message('sosforge:basemap-status')
    expect(replies[0]).toMatchObject({ missingFiles: 84, missingBytes: 0, unmeasuredFiles: 84 })
  })

  it('downloads it on request, whatever the connection, and tells every tab how far it got', async () => {
    connection = { saveData: true } // the reader asked: their call, not the worker's
    const { result } = await message('sosforge:basemap-save')
    expect(result).toBe('world fetched 112')
    const sent = broadcasts()
    const progress = sent.filter((m) => m.type === 'sosforge:basemap-progress')
    expect(progress).toHaveLength(112)
    expect(progress.at(-1)).toMatchObject({ done: 112, failed: 0, total: 112 })
    expect(sent.at(-1)).toMatchObject({ type: 'sosforge:basemap-saved', files: 112 })

    const { replies } = await message('sosforge:basemap-status')
    expect(replies[0]).toMatchObject({ saved: true, missingFiles: 0, missingBytes: 0, unmeasuredFiles: 0 })
  })

  it('reports a failed save, and a second request fetches only what is missing', async () => {
    let refuse = true
    network.mockImplementation(async (input: Request | string) => {
      const url = abs(input)
      calls.push(url)
      // every zoom-3 tile fails (the zoom is the segment right after the build)
      if (refuse && /\/planet\/[^/]+\/3\//.test(url)) throw new TypeError('network gone')
      return answer(url)
    })
    await message('sosforge:basemap-save')
    expect(broadcasts().at(-1)).toMatchObject({ type: 'sosforge:basemap-failed', failed: 64 })
    refuse = false
    tab.postMessage.mockClear()
    calls = []
    expect((await message('sosforge:basemap-save')).result).toBe('world fetched 64')
    expect(broadcasts().at(-1)).toMatchObject({ type: 'sosforge:basemap-saved', files: 64 })
  })

  it('can be given back: forget leaves the small set only', async () => {
    await message('sosforge:basemap-save')
    const { replies } = await message('sosforge:basemap-forget')
    expect(replies[0]).toMatchObject({ saved: false, missingFiles: 84 })
    const base = store('sosforge-tiles-base')!
    expect(base.has(`${HOST}/planet/3/0/0.pbf`)).toBe(false)
    expect(base.has(`${HOST}/planet/1/0/0.pbf`)).toBe(true)
    expect(base.has(`${HOST}/fonts/Noto%20Sans%20Regular/512-767.pbf`)).toBe(false)
    expect(base.has(`${HOST}/fonts/Noto%20Sans%20Regular/0-255.pbf`)).toBe(true)
  })
})

describe('what the worker must leave alone', () => {
  beforeEach(quietPrecache)

  it('never answers the live feed, from any cache', async () => {
    await runInstall()
    for (const url of [`${ORIGIN}/api/events`, `${ORIGIN}/api/sources`, `${ORIGIN}/ws`]) {
      expect((await fetchThrough(url)).response).toBe('passthrough')
    }
    const cached = [...stores.values()].flatMap((s) => [...s.keys()])
    expect(cached.filter((u) => u.includes('/api/') || u.endsWith('/ws'))).toEqual([])
  })

  it('leaves the APK, its checksum and latest.json to the server', async () => {
    for (const path of ['/download/sosforge-v0.1.1.apk', '/download/sosforge-latest.apk', '/download/latest.json', '/download/sosforge-v0.1.1.apk.sha256']) {
      expect((await fetchThrough(`${ORIGIN}${path}`, 'navigate')).response).toBe('passthrough')
    }
    expect((await fetchThrough(`${ORIGIN}/download/`, 'navigate')).response).not.toBe('passthrough')
  })

  it('asks the server, not the HTTP cache, for every shell', async () => {
    for (const path of ['/', '/fr/', '/ja/', '/download/']) {
      network.mockClear()
      await fetchThrough(`${ORIGIN}${path}`, 'navigate')
      expect(network.mock.calls[0][1], path).toMatchObject({ cache: 'no-cache' })
    }
  })

  it('does not keep a navigation that is not a page', async () => {
    network.mockImplementation(async (input: Request | string) =>
      Object.defineProperty(new Response('%PDF', { headers: { 'content-type': 'application/pdf' } }), 'url', {
        value: abs(input),
      }),
    )
    await fetchThrough(`${ORIGIN}/fr/`, 'navigate')
    expect(store('sosforge-shell')?.has(`${ORIGIN}/fr/`) ?? false).toBe(false)
  })
})

async function runInstall() {
  const waits: Promise<unknown>[] = []
  handlers.get('install')!({ respondWith: () => {}, waitUntil: (p) => waits.push(p) })
  await Promise.all(waits)
}

/** A cache store whose addAll stores what the network really serves, as the
 * browser's does, so install can read the shell it just precached. */
function realShellCaches() {
  const api = cacheApi()
  return {
    ...api,
    open: async (name: string) => {
      const cache = await api.open(name)
      return {
        ...cache,
        addAll: async (requests: Request[]) => {
          for (const r of requests) await cache.put(r, answer(abs(r)))
        },
      }
    },
  }
}

describe('install', () => {
  it('caches the build it installs, the lazy map chunk included, so a first visit works offline', async () => {
    vi.stubGlobal('caches', realShellCaches())
    await runInstall()
    const runtime = store('sosforge-runtime')!
    for (const path of ['index-AAA.js', 'index-BBB.css', 'MapView-CCC.js', 'MapView-DDD.css', 'plex-latin-400-EEE.woff2']) {
      expect(runtime.has(`${ORIGIN}/assets/${path}`), path).toBe(true)
    }
    expect(runtime.has(`${ORIGIN}/assets/plex-latin-400-FFF.woff`)).toBe(false)
    expect(store('sosforge-shell')!.has(`${ORIGIN}/plate-boundaries.json`)).toBe(true)
  })

  it('still installs, and takes over, when the bundles cannot be fetched', async () => {
    vi.stubGlobal('caches', realShellCaches())
    network.mockImplementation(async () => {
      throw new TypeError('offline')
    })
    await runInstall()
    expect((self as unknown as { skipWaiting: ReturnType<typeof vi.fn> }).skipWaiting).toHaveBeenCalled()
  })
})
