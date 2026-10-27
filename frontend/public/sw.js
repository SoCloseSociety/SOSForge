/**
 * SOSForge service worker -- hand-written, no build plugin (see CLAUDE.md:
 * this project keeps its dependency list short).
 *
 * The one rule that governs everything below, straight from the product's
 * own rule: "the feed must never lie about its own freshness". A cached
 * earthquake feed served while offline would be exactly that lie, dressed
 * up as live data. So this worker draws one hard line:
 *
 *   - the APP SHELL (JS, CSS, HTML, manifest, icons) is cached, so the app
 *     opens with no network at all;
 *   - `/api/*` and `/ws` are NEVER intercepted. Not cached, no offline
 *     fallback, nothing. If the network is down, the app boots from cache
 *     and its own watchdog (see src/live.ts) shows "RECONNECTING" -- the
 *     honest state -- instead of stale numbers pretending to be current.
 *
 * The BASEMAP (tiles.openfreemap.org) is cached as well, in caches of its
 * own: a map that still draws when the network drops in a disaster is part
 * of "opens with no network". A basemap tile says where the coast is, not
 * what happened there; every event on the map still comes from the live
 * feed, or does not show at all.
 *
 * Bump CACHE_VERSION on every release that changes what gets precached.
 * `activate` deletes every cache from a previous version, so nothing pinned
 * to an old build lingers forever. The basemap caches have their own
 * TILES_VERSION: a shell release must not throw away 35 MB of world map.
 */

// v2 (2026-10): the precached manifest changed colour with the Sentinel
// House restyle; without the bump a returning visitor kept the old one.
// v3 (2026-10-27): the basemap caches, the build's hashed bundles precached
// at install, /plate-boundaries.json precached, /download/ files left alone.
const CACHE_VERSION = 'v3'
const SHELL_CACHE = `sosforge-shell-${CACHE_VERSION}`
const RUNTIME_CACHE = `sosforge-runtime-${CACHE_VERSION}`

const TILES_VERSION = 't1'
// World zooms 0..3, the style, its TileJSON, sprites and glyphs: the map of
// the whole planet, offline. Never trimmed.
const TILES_BASE_CACHE = `sosforge-tiles-base-${TILES_VERSION}`
// Tiles the reader browsed deeper than that: kept, but capped.
const TILES_CACHE = `sosforge-tiles-${TILES_VERSION}`

const CURRENT_CACHES = new Set([SHELL_CACHE, RUNTIME_CACHE, TILES_BASE_CACHE, TILES_CACHE])

// Fixed URLs that exist before the app is even fetched once: the app shell
// itself, the manifest, and its icons. The hashed /assets/*.js and *.css
// bundles are NOT listed here -- their filenames change every build, so
// they are read out of the shell at install (see precacheBuild) and cached
// at runtime (see ASSET_PREFIX below). The plate boundaries are a static
// dataset the map draws: without them offline, the map loses its faults.
const PRECACHE_URLS = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/icon-192.png',
  '/icon-512.png',
  '/plate-boundaries.json',
]

// The subset of the above whose CONTENT changes at every build while its URL
// stays the same. These can never be served cache-first: see the fetch
// handler. The rest of PRECACHE_URLS are stable bytes at a stable URL.
const SHELL_PATHS = ['/', '/index.html']

// Vite's default build output directory. Filenames under it are content
// hashed, so a cached entry can never go stale: the same URL always means
// the same bytes, and a changed file is simply a different URL.
const ASSET_PREFIX = '/assets/'

// ------------------------------------------------------------------ basemap

const TILE_HOST = 'https://tiles.openfreemap.org'
// Must stay equal to BASEMAP_STYLE_URL in src/components/MapView.tsx
// (sw.tiles.test.ts reads both files and fails if they part).
const STYLE_URL = `${TILE_HOST}/styles/dark`

// `/planet/<build>/<z>/<x>/<y>.pbf`. The build is a dated, immutable path
// (`20261025_080012_pt`, cache-control max-age=315360000, measured
// 2026-10-27): the browser's HTTP cache already never re-downloads a tile it
// holds. What it cannot do is draw one with the network down.
const TILE_PATH = /^\/planet\/([^/]+)\/(\d+)\/(\d+)\/(\d+)\.pbf$/

// The world, zooms 0 to 3: 85 tiles, 33.5 MB (measured 2026-10-27 on build
// 20261025_080012_pt: z0 0.08, z1 0.81, z2 14.37, z3 18.24 MB).
const PRECACHE_MAX_ZOOM = 3

// Browsed tiles beyond the precached world. Insertion order (the Cache API
// keeps it), oldest out first.
const MAX_BROWSED_TILES = 400

// The glyph ranges the labels of zooms 0..3 need: every character of every
// name in those 85 tiles (name:latin, name:nonlatin, name_en, name, ref),
// minus the ideographs, kana and hangul that MapLibre draws with a local
// font. Measured 2026-10-27, 23 ranges, 1.77 MB of "Noto Sans Regular".
const GLYPH_RANGES = [
  '0-255', '256-511', '512-767', '768-1023', '1024-1279', '1280-1535', '1536-1791', '1792-2047',
  '2304-2559', '2816-3071', '3328-3583', '3584-3839', '3840-4095', '4096-4351', '4608-4863',
  '4864-5119', '5120-5375', '5376-5631', '5888-6143', '6144-6399', '7680-7935', '8192-8447',
  '11520-11775',
]

// The style and the TileJSON are served from the cache at once and refreshed
// behind (stale-while-revalidate), but only while the cached copy is younger
// than this. Older, the network is asked first: a TileJSON weeks old names a
// tile build the host may have deleted since.
const META_FRESH_MS = 7 * 24 * 3600 * 1000
const META_TIMEOUT_MS = 6000

// The world precache waits this long after the map starts, then fetches one
// file at a time: it must never compete with the opening snapshot of the
// live feed, which is what the reader came for.
const PRECACHE_DELAY_MS = 20000

const TILE_FETCH = { mode: 'cors', credentials: 'omit' }

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS.map((url) => new Request(url, { cache: 'reload' }))))
      .then(() => precacheBuild())
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((name) => !CURRENT_CACHES.has(name)).map((name) => caches.delete(name))))
      .then(() => self.clients.claim())
      .then(() => {
        // Not awaited, on purpose: activation must not wait for 35 MB. A
        // first visit loaded its map before this worker took control, so
        // without this kick it would precache nothing until the next visit.
        precacheBasemap()
      }),
  )
})

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'sosforge:precache-basemap') event.waitUntil(precacheBasemap())
})

// Lazy chunks and fonts named inside a bundle: `"assets/MapView-x.js"` (Vite's
// preload map), `url(/assets/font.woff2)`, and `import("./MapView-x.js")` or
// `from "./index-x.js"` between chunks. Only woff2: every browser that runs
// this worker reads it, so the .woff twins would be dead weight.
const ASSET_REF = /["'(]\/?assets\/([\w.-]+\.(?:js|css|woff2))|(?:from|import\()\s*["']\.\/([\w.-]+\.(?:js|css))["']/g

/** The hashed bundles of the build being installed, cached before it
 * activates. Without this, a FIRST visit was useless offline: that visit ran
 * before the worker controlled the page, so nothing it loaded went through
 * the cache, and an offline reload found the shell but not one line of its
 * JavaScript -- a blank page. Measured 2026-10-27 (Playwright: one visit,
 * network cut, reload: #root empty, /assets/index-*.js and .css failed).
 *
 * Read from the shell just precached: its tags name the entry files, and the
 * entries name the lazy ones (the map). Best effort: a failure here must not
 * fail the install -- the worker still serves the shell. */
async function precacheBuild() {
  try {
    const shell = await caches.match('/index.html')
    if (!shell) return
    const html = await shell.clone().text()
    const queue = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1])
    const seen = new Set(queue)
    const runtime = await caches.open(RUNTIME_CACHE)
    while (queue.length) {
      const path = queue.shift()
      let response = await runtime.match(path, { ignoreVary: true })
      if (!response) {
        response = await fetch(path)
        if (!response.ok) continue
        await runtime.put(path, response.clone())
      }
      if (!/\.(js|css)$/.test(path)) continue
      for (const m of (await response.text()).matchAll(ASSET_REF)) {
        const ref = ASSET_PREFIX + (m[1] || m[2])
        if (!seen.has(ref)) {
          seen.add(ref)
          queue.push(ref)
        }
      }
    }
  } catch {
    // Offline support is a bonus; the install goes on without it.
  }
}

/** Network-first: an online user always gets the current shell, whose
 * hashed asset references must match what is actually in the cache. Offline,
 * fall back to whatever shell was last cached -- that's the "opens with no
 * network" promise. Only an HTML answer is kept: this also answers every
 * other navigation, and a navigation is not always a page. */
async function networkFirstShell(request) {
  try {
    const response = await fetch(request)
    if (response && response.ok && (response.headers.get('content-type') || '').includes('text/html')) {
      const cache = await caches.open(SHELL_CACHE)
      cache.put(request, response.clone())
    }
    return response
  } catch (err) {
    const cached =
      (await caches.match(request, { ignoreVary: true })) || (await caches.match('/index.html', { ignoreVary: true }))
    if (cached) return cached
    throw err
  }
}

/** Cache-first: safe only for content-hashed or explicitly precached URLs,
 * where a cache hit is by construction never stale.
 *
 * ignoreVary: these URLs name their bytes, so no request header can change
 * them. Without it the offline reload after a first visit was STILL a blank
 * page with the bundles in the cache: the server answered `Vary: Origin`
 * (vite preview does; a CDN or another proxy may), the page's module script
 * and stylesheet requests carry an Origin header, the copies stored at
 * install were fetched without one -- and a Vary-respecting match missed
 * every one of them. Measured 2026-10-27, Playwright offline reload. */
async function cacheFirst(request) {
  const cached = await caches.match(request, { ignoreVary: true })
  if (cached) return cached
  const response = await fetch(request)
  if (response && response.ok) {
    const cache = await caches.open(RUNTIME_CACHE)
    cache.put(request, response.clone())
  }
  return response
}

// ---------------------------------------------------------- basemap caching

/** A copy to store, stamped with when and from where it was fetched (the
 * response's own headers are CORS-filtered and say neither). The URL is
 * passed in rather than read from the response: a tile retried under a newer
 * build must record the build it really came from. */
function stamp(response, url) {
  const headers = new Headers(response.headers)
  headers.set('x-sos-fetched', String(Date.now()))
  headers.set('x-sos-url', response.url || url)
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers })
}

function fetchedAt(response) {
  return Number(response.headers.get('x-sos-fetched')) || 0
}

/** The tile build a stored tile came from ('' if unknown). */
function buildOf(response) {
  const m = TILE_PATH.exec(new URL(response.headers.get('x-sos-url') || TILE_HOST).pathname)
  return m ? m[1] : ''
}

/** One cache key per tile, whatever the build: OpenFreeMap publishes a new
 * dated build every few days, and keying on it would orphan the whole
 * offline world (and re-download 33.5 MB) at each one. A tile from last
 * week's build draws the same coastline. */
function tileKey(z, x, y) {
  return `${TILE_HOST}/planet/${z}/${x}/${y}.pbf`
}

function fetchWithTimeout(url, ms) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  return fetch(url, { ...TILE_FETCH, signal: controller.signal }).finally(() => clearTimeout(timer))
}

async function refreshMeta(url, cache, timeout = META_TIMEOUT_MS) {
  const response = await fetchWithTimeout(url, timeout)
  if (response.ok) await cache.put(url, stamp(response.clone(), url))
  return response
}

/** Style and TileJSON: stale-while-revalidate within META_FRESH_MS, then
 * network first with the cached copy as the offline answer. */
async function getMeta(url, waitUntil) {
  const cache = await caches.open(TILES_BASE_CACHE)
  const cached = await cache.match(url)
  if (cached && Date.now() - fetchedAt(cached) < META_FRESH_MS) {
    waitUntil(refreshMeta(url, cache).catch(() => {}))
    return cached
  }
  try {
    return await refreshMeta(url, cache)
  } catch (err) {
    if (cached) return cached
    throw err
  }
}

/** Glyphs and sprites: versioned or append-only paths, cache-first. */
async function cacheFirstBasemap(url) {
  const cache = await caches.open(TILES_BASE_CACHE)
  const cached = await cache.match(url)
  if (cached) return cached
  const response = await fetch(url, TILE_FETCH)
  if (response.ok) await cache.put(url, stamp(response.clone(), url))
  return response
}

let trimming = Promise.resolve()
function trimBrowsed(cache) {
  trimming = trimming
    .then(async () => {
      const keys = await cache.keys()
      for (let i = 0; i < keys.length - MAX_BROWSED_TILES; i++) await cache.delete(keys[i])
    })
    .catch(() => {})
  return trimming
}

/** The build the freshest cached TileJSON names, to retry a tile the host
 * no longer serves under an older build. */
async function newestBuild() {
  const tilejson = await caches.match(`${TILE_HOST}/planet`)
  if (!tilejson) return ''
  const template = ((await tilejson.json()).tiles || [])[0] || ''
  const m = /\/planet\/([^/]+)\//.exec(template)
  return m ? m[1] : ''
}

async function storeTile(url, build, z, x, y, cache, waitUntil) {
  let fetched = url
  let response = await fetch(url, TILE_FETCH)
  if (response.status === 404) {
    const newest = await newestBuild()
    if (newest && newest !== build) {
      fetched = url.replace(`/planet/${build}/`, `/planet/${newest}/`)
      response = await fetch(fetched, TILE_FETCH)
    }
  }
  if (response.ok) {
    await cache.put(tileKey(z, x, y), stamp(response.clone(), fetched))
    if (Number(z) > PRECACHE_MAX_ZOOM) waitUntil(trimBrowsed(cache))
  }
  return response
}

/** Tiles: cache-first. A hit from an OLDER build is still served at once
 * (offline it is the only map there is) and refreshed behind, which costs
 * what the page would have fetched without this worker anyway. */
async function serveTile(request, match, waitUntil) {
  const [, build, z, x, y] = match
  const cache = await caches.open(Number(z) <= PRECACHE_MAX_ZOOM ? TILES_BASE_CACHE : TILES_CACHE)
  const cached = await cache.match(tileKey(z, x, y))
  if (cached) {
    if (buildOf(cached) < build) waitUntil(storeTile(request.url, build, z, x, y, cache, waitUntil).catch(() => {}))
    return cached
  }
  return storeTile(request.url, build, z, x, y, cache, waitUntil)
}

/** Answers a request to the tile host, or returns false to leave it alone. */
function routeBasemap(event, url) {
  const waitUntil = (p) => {
    try {
      event.waitUntil(p)
    } catch {
      // The event already settled (a late background refresh): the work
      // still runs, the worker just is not kept alive for it.
    }
  }
  let answer = null
  if (url.pathname.startsWith('/styles/') || url.pathname === '/planet') {
    answer = getMeta(url.href, waitUntil)
  } else if (url.pathname.startsWith('/fonts/') || url.pathname.startsWith('/sprites/')) {
    answer = cacheFirstBasemap(url.href)
  } else {
    const match = TILE_PATH.exec(url.pathname)
    if (match) answer = serveTile(event.request, match, waitUntil)
  }
  if (!answer) return false
  event.respondWith(answer)
  // The map is in use: a good moment to make sure the whole world is there.
  waitUntil(precacheBasemap())
  return true
}

// --------------------------------------------------------- basemap precache

let precaching = null
let basemapComplete = false

/** Save-Data is the reader asking for less; 2G cannot carry 35 MB without
 * starving the live feed (a fresh tab needs its snapshot first). */
function constrained() {
  const connection = self.navigator && self.navigator.connection
  return !!connection && (connection.saveData === true || /(^|-)2g$/.test(connection.effectiveType || ''))
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** What the offline world map needs: [url to fetch, cache key] pairs. */
async function basemapManifest(waitUntil) {
  const style = await (await getMeta(STYLE_URL, waitUntil)).json()
  const wanted = []
  for (const source of Object.values(style.sources || {})) {
    if (source.type !== 'vector' || !source.url) continue
    const tilejson = await (await getMeta(source.url, waitUntil)).json()
    const template = (tilejson.tiles || [])[0] || ''
    if (!TILE_PATH.test(template.replace('{z}/{x}/{y}', '0/0/0').replace(TILE_HOST, ''))) continue
    for (let z = 0; z <= PRECACHE_MAX_ZOOM; z++) {
      for (let x = 0; x < 2 ** z; x++) {
        for (let y = 0; y < 2 ** z; y++) {
          wanted.push([template.replace('{z}', z).replace('{x}', x).replace('{y}', y), tileKey(z, x, y)])
        }
      }
    }
  }
  const sprites = typeof style.sprite === 'string' ? [style.sprite] : (style.sprite || []).map((s) => s.url)
  for (const sprite of sprites) {
    for (const suffix of ['.json', '.png', '@2x.json', '@2x.png']) wanted.push([sprite + suffix, sprite + suffix])
  }
  const stacks = new Set()
  for (const layer of style.layers || []) {
    const font = layer.layout && layer.layout['text-font']
    if (Array.isArray(font) && font.every((f) => typeof f === 'string')) stacks.add(font.join(','))
  }
  if (style.glyphs) {
    for (const stack of stacks) {
      for (const range of GLYPH_RANGES) {
        // new URL() encodes the space exactly as the page's own request does,
        // so the key matches what MapLibre asks for.
        const url = new URL(style.glyphs.replace('{fontstack}', stack).replace('{range}', range)).href
        wanted.push([url, url])
      }
    }
  }
  return wanted
}

async function runPrecache() {
  if (basemapComplete || constrained()) return 'skipped'
  await sleep(PRECACHE_DELAY_MS)
  if (constrained()) return 'skipped'
  const cache = await caches.open(TILES_BASE_CACHE)
  const pending = []
  const wanted = await basemapManifest((p) => pending.push(p))
  let missing = 0
  for (const [url, key] of wanted) {
    if (constrained()) return 'stopped'
    if (await cache.match(key)) continue
    missing++
    const response = await fetch(url, TILE_FETCH)
    if (response.ok) await cache.put(key, stamp(response, url))
  }
  await Promise.all(pending)
  if (missing === 0) basemapComplete = true
  return `fetched ${missing}`
}

/** One run at a time, resumable: each run fetches only what is missing, so
 * a worker stopped half-way simply carries on at the next visit. */
function precacheBasemap() {
  if (!precaching) {
    precaching = runPrecache()
      .catch(() => 'failed')
      .finally(() => {
        precaching = null
      })
  }
  return precaching
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin === TILE_HOST) {
    routeBasemap(event, url)
    return
  }
  if (url.origin !== self.location.origin) return // fonts, other hosts: browser handles it directly

  // The live feed, verbatim: network-only, always. No respondWith at all --
  // this falls straight through to the browser's normal fetch, untouched.
  if (url.pathname.startsWith('/api/') || url.pathname === '/ws' || url.pathname.startsWith('/healthz')) {
    return
  }

  // The download page is a page; the files next to it are not. An APK is a
  // navigation too (a tapped link): answered from here it would be stored
  // whole in the shell cache, and the Range requests that let a phone resume
  // a 39 MB download would go through a worker that does not honour them.
  // latest.json must always be the server's word, never a cached copy.
  if (url.pathname.startsWith('/download/') && url.pathname !== '/download/' && url.pathname !== '/download/index.html') {
    return
  }

  // ORDER MATTERS, and it was wrong here for a while. A page load has
  // mode 'navigate' and pathname '/', which is also in PRECACHE_URLS -- so
  // when the precache branch came first, every navigation was answered
  // cache-first and `networkFirstShell` below was unreachable code that
  // described, in its own comment, a behaviour the worker did not have.
  //
  // The consequence was the product's cardinal rule broken at its root: a
  // returning visitor kept the shell cached on their first visit FOREVER,
  // online, because a new deploy does not change these bytes and so never
  // fires an update. They would keep an old bundle, an old event schema and
  // every bug already fixed -- on a page announcing itself as live. Worse,
  // an old shell references hashed asset URLs that a later deploy has
  // removed: the map 404s and never comes back.
  //
  // So the shell is network-first, always, and falls back to the cache only
  // when the network is genuinely gone.
  if (request.mode === 'navigate' || SHELL_PATHS.includes(url.pathname)) {
    event.respondWith(networkFirstShell(request))
    return
  }

  // Cache-first is reserved for URLs where a hit CANNOT be stale: the
  // content-hashed bundles, and the fixed icons/manifest.
  if (url.pathname.startsWith(ASSET_PREFIX) || PRECACHE_URLS.includes(url.pathname)) {
    event.respondWith(cacheFirst(request))
    return
  }

  // Anything else (robots.txt, sitemap.xml, a dev-server module graph
  // request, ...): left alone. No caching, no interception.
})

// A watch alarm shown through this worker (src/notify.ts: Chrome on Android
// accepts no other way). Without this handler a tap on it did nothing at all:
// the reader saw "Earthquake -- My location", tapped, and stayed where they
// were. Focus the tab that is already open, or open one, on the event.
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
          if ('navigate' in client && target !== '/') client.navigate(target).catch(() => {})
          return client.focus()
        }
      }
      return self.clients.openWindow(target)
    }),
  )
})
