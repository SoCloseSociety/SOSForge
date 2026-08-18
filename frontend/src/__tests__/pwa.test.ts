/** `registerServiceWorker` wires the app shell's offline cache and the
 * update path, but the actual caching policy lives in public/sw.js (not
 * unit-testable outside a browser). What belongs here: registration never
 * throws when the browser lacks support or the registration itself fails,
 * and a new worker taking control triggers exactly one reload -- never a
 * loop, and never silence. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { registerServiceWorker } from '../pwa'

/** Minimal fake of ServiceWorkerContainer: enough to drive `register()` and
 * the `controllerchange` event without a real browser. */
function makeContainer(registerImpl: () => Promise<unknown> = () => Promise.resolve({})) {
  const listeners = new Map<string, Array<() => void>>()
  return {
    register: vi.fn(registerImpl),
    addEventListener: vi.fn((type: string, handler: () => void) => {
      const list = listeners.get(type) ?? []
      list.push(handler)
      listeners.set(type, list)
    }),
    fire(type: string): void {
      for (const handler of listeners.get(type) ?? []) handler()
    },
  }
}

function defineServiceWorker(container: ReturnType<typeof makeContainer> | undefined): void {
  Object.defineProperty(navigator, 'serviceWorker', {
    value: container,
    configurable: true,
  })
}

/** Fires the `load` listener(s) registered on `window` by the module under
 * test, without waiting for jsdom's real page load. */
function fireWindowLoad(): void {
  window.dispatchEvent(new Event('load'))
}

let reload: ReturnType<typeof vi.fn>
const originalLocation = window.location

beforeEach(() => {
  reload = vi.fn()
  // jsdom's window.location.reload is not a configurable own property, so
  // vi.spyOn cannot patch it directly: replace the whole `location` global
  // with a stand-in that keeps everything else (href, host, ...) but a
  // spy-able `reload`.
  Object.defineProperty(window, 'location', {
    value: { ...originalLocation, reload },
    configurable: true,
    writable: true,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  Object.defineProperty(window, 'location', { value: originalLocation, configurable: true, writable: true })
  Object.defineProperty(navigator, 'serviceWorker', { value: undefined, configurable: true })
})

describe('unsupported browser', () => {
  it('does nothing, and never throws, when the browser has no serviceWorker support', () => {
    defineServiceWorker(undefined)
    expect(() => registerServiceWorker()).not.toThrow()
    expect(() => fireWindowLoad()).not.toThrow()
  })
})

describe('registration', () => {
  it('registers /sw.js at the root scope once the page has loaded', async () => {
    const container = makeContainer()
    defineServiceWorker(container)

    registerServiceWorker()
    expect(container.register).not.toHaveBeenCalled() // not before `load`

    fireWindowLoad()
    await Promise.resolve()

    expect(container.register).toHaveBeenCalledTimes(1)
    expect(container.register).toHaveBeenCalledWith('/sw.js', { scope: '/', updateViaCache: 'none' })
  })

  it('swallows a registration failure instead of throwing or rejecting unhandled', async () => {
    const container = makeContainer(() => Promise.reject(new Error('blocked by extension')))
    defineServiceWorker(container)

    registerServiceWorker()
    expect(() => fireWindowLoad()).not.toThrow()
    // Let the rejected promise's .catch() run; a failure here would surface
    // as an unhandled rejection in the test run, not as a thrown error.
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
})

describe('update flow', () => {
  it('reloads the page once a new worker takes control', () => {
    const container = makeContainer()
    defineServiceWorker(container)

    registerServiceWorker()
    container.fire('controllerchange')

    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('never reloads twice, even if the event fires more than once', () => {
    const container = makeContainer()
    defineServiceWorker(container)

    registerServiceWorker()
    container.fire('controllerchange')
    container.fire('controllerchange')
    container.fire('controllerchange')

    expect(reload).toHaveBeenCalledTimes(1)
  })
})
