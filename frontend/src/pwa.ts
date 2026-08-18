/**
 * Registers the service worker (public/sw.js) that lets SOSForge open with
 * no network -- see sw.js for what is cached and, more importantly, what
 * never is (the live feed itself).
 *
 * Kept out of main.tsx on purpose: this module has no React dependency and
 * can be exercised by a unit test without mounting the app.
 */

const SW_URL = '/sw.js'

export function registerServiceWorker(): void {
  if (!('serviceWorker' in navigator)) return

  // A new worker just took control mid-session (sw.js calls skipWaiting +
  // clients.claim on activate): the page already loaded is running JS/CSS
  // from the OLD build while a new worker is now serving requests. On a
  // live tracker, pinning an old build is a trap -- reload once to land on
  // the new one. `reloading` guards against a loop if the event fires twice.
  let reloading = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading) return
    reloading = true
    window.location.reload()
  })

  // Registered after `load` so it never competes with the page's own
  // critical resources -- the websocket connection matters far more than
  // the offline cache warming up a few hundred milliseconds sooner.
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register(SW_URL, {
        scope: '/',
        // Bypasses the HTTP cache for sw.js itself on every check, browser
        // update interval or not: a service worker is only as good as its
        // updates actually landing, and the deploy config for this product
        // is out of this module's scope to also fix.
        updateViaCache: 'none',
      })
      .catch(() => {
        // Offline support is a bonus, not the product. A registration
        // failure (unsupported browser, an extension blocking it, ...)
        // must never stop the app itself from rendering -- the fetch that
        // actually matters is the websocket, handled entirely in live.ts.
      })
  })
}
