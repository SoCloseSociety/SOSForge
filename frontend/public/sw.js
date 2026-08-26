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
 * Bump CACHE_VERSION on every release that changes what gets precached.
 * `activate` deletes every cache from a previous version, so nothing pinned
 * to an old build lingers forever.
 */

const CACHE_VERSION = 'v1'
const SHELL_CACHE = `sosforge-shell-${CACHE_VERSION}`
const RUNTIME_CACHE = `sosforge-runtime-${CACHE_VERSION}`
const CURRENT_CACHES = new Set([SHELL_CACHE, RUNTIME_CACHE])

// Fixed URLs that exist before the app is even fetched once: the app shell
// itself, the manifest, and its icons. The hashed /assets/*.js and *.css
// bundles are NOT listed here -- their filenames change every build, so
// they are cached at runtime instead (see ASSET_PREFIX below).
const PRECACHE_URLS = ['/', '/index.html', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png']

// The subset of the above whose CONTENT changes at every build while its URL
// stays the same. These can never be served cache-first: see the fetch
// handler. The rest of PRECACHE_URLS are stable bytes at a stable URL.
const SHELL_PATHS = ['/', '/index.html']

// Vite's default build output directory. Filenames under it are content
// hashed, so a cached entry can never go stale: the same URL always means
// the same bytes, and a changed file is simply a different URL.
const ASSET_PREFIX = '/assets/'

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS.map((url) => new Request(url, { cache: 'reload' }))))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((name) => !CURRENT_CACHES.has(name)).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  )
})

/** Network-first: an online user always gets the current shell, whose
 * hashed asset references must match what is actually in the cache. Offline,
 * fall back to whatever shell was last cached -- that's the "opens with no
 * network" promise. */
async function networkFirstShell(request) {
  try {
    const response = await fetch(request)
    if (response && response.ok) {
      const cache = await caches.open(SHELL_CACHE)
      cache.put(request, response.clone())
    }
    return response
  } catch (err) {
    const cached = (await caches.match(request)) || (await caches.match('/index.html'))
    if (cached) return cached
    throw err
  }
}

/** Cache-first: safe only for content-hashed or explicitly precached URLs,
 * where a cache hit is by construction never stale. */
async function cacheFirst(request) {
  const cached = await caches.match(request)
  if (cached) return cached
  const response = await fetch(request)
  if (response && response.ok) {
    const cache = await caches.open(RUNTIME_CACHE)
    cache.put(request, response.clone())
  }
  return response
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return // map tiles, fonts, etc.: browser handles it directly

  // The live feed, verbatim: network-only, always. No respondWith at all --
  // this falls straight through to the browser's normal fetch, untouched.
  if (url.pathname.startsWith('/api/') || url.pathname === '/ws' || url.pathname.startsWith('/healthz')) {
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
