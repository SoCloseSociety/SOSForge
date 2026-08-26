/** The map, tested through a fake MapLibre.
 *
 * Every defect below was observed in the running app and none of them is
 * visible to `tsc` or to a build: they live in the ORDER things happen (a
 * deep link selects before the lazily loaded map exists), in what a popup
 * stops doing once it is open, and in a setting the product was ignoring.
 *
 * MapLibre needs WebGL, which jsdom does not have, so the module is replaced
 * by a fake that records what the component asks the map to do. That is
 * enough: what is under test here is the component's decisions, not
 * MapLibre's rendering.
 */
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MapView } from '../components/MapView'
import { useStore } from '../store'
import { NOW, makeEvent, minutesAgo, resetStore } from './helpers'

const fake = vi.hoisted(() => {
  type Handler = (payload?: unknown) => void

  class FakeMap {
    static instances: FakeMap[] = []
    handlers: Record<string, Handler[]> = {}
    sources: Record<string, { data: unknown; setData: (data: unknown) => void }> = {}
    layers: string[] = []
    paint: Array<[string, string, unknown]> = []
    flyToCalls: Array<Record<string, unknown>> = []
    jumpToCalls: Array<Record<string, unknown>> = []
    removed = 0

    constructor(public options: Record<string, unknown>) {
      FakeMap.instances.push(this)
    }

    /** `on(type, cb)` and `on(type, layer, cb)`, like the real one. */
    on(type: string, second: unknown, third?: unknown) {
      const cb = (typeof second === 'function' ? second : third) as Handler
      const key = typeof second === 'string' ? `${type}:${second}` : type
      ;(this.handlers[key] ||= []).push(cb)
    }

    fire(key: string, payload?: unknown) {
      for (const cb of this.handlers[key] ?? []) cb(payload)
    }

    addControl() {}

    addSource(id: string, spec: { data?: unknown }) {
      this.sources[id] = {
        data: spec.data,
        setData(data: unknown) {
          this.data = data
        },
      }
    }

    addLayer(spec: { id: string }) {
      this.layers.push(spec.id)
    }

    getSource(id: string) {
      return this.sources[id]
    }

    getLayer(id: string) {
      return this.layers.includes(id) ? { id } : undefined
    }

    setPaintProperty(layer: string, property: string, value: unknown) {
      this.paint.push([layer, property, value])
    }

    getCanvas() {
      return { style: {} } as unknown as HTMLCanvasElement
    }

    flyTo(options: Record<string, unknown>) {
      this.flyToCalls.push(options)
    }

    jumpTo(options: Record<string, unknown>) {
      this.jumpToCalls.push(options)
    }

    remove() {
      this.removed += 1
    }
  }

  class FakePopup {
    static instances: FakePopup[] = []
    element = document.createElement('div')
    lngLat: unknown = null
    html = ''
    added = 0
    removed = 0
    handlers: Record<string, Array<() => void>> = {}

    constructor(public options: unknown) {
      FakePopup.instances.push(this)
    }

    setLngLat(value: unknown) {
      this.lngLat = value
      return this
    }

    setHTML(html: string) {
      this.html = html
      this.element.innerHTML = html
      return this
    }

    addTo() {
      this.added += 1
      return this
    }

    getElement() {
      return this.element
    }

    on(type: string, cb: () => void) {
      ;(this.handlers[type] ||= []).push(cb)
    }

    remove() {
      this.removed += 1
      return this
    }
  }

  class FakeNavigationControl {}

  return { FakeMap, FakePopup, FakeNavigationControl }
})

vi.mock('maplibre-gl', () => ({
  default: {
    Map: fake.FakeMap,
    Popup: fake.FakePopup,
    NavigationControl: fake.FakeNavigationControl,
  },
}))
vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}))

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

/** Answers `matches` only for the queries listed, like a real browser. */
function stubMatchMedia(matching: string[]) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    media: query,
    matches: matching.includes(query),
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia
}

function lastMap() {
  return fake.FakeMap.instances.at(-1)!
}

beforeEach(() => {
  fake.FakeMap.instances = []
  fake.FakePopup.instances = []
  resetStore()
  useStore.getState().setLang('en')
})

afterEach(() => {
  vi.restoreAllMocks()
  // @ts-expect-error -- restoring the jsdom default
  delete window.matchMedia
})

describe('a selection made before the map is ready', () => {
  /* A deep link (`#e/...`) selects the event at App mount, and the map is
   * lazily loaded: `load` fires long after. When readiness was a ref, this
   * effect ran once, bailed on `!ready.current`, and never ran again --
   * `selected` never changes, so nothing re-triggered it. A shared link
   * centred nothing and opened nothing, forever. */
  it('centres and opens the popup as soon as the map becomes ready', () => {
    // old enough not to be a wave candidate: no animation loop in the way
    const event = makeEvent({ id: 'emsc:deep', time: minutesAgo(30) })
    useStore.setState({ selected: 'emsc:deep' })

    render(<MapView events={[event]} now={NOW} />)
    const map = lastMap()
    expect(map.flyToCalls).toHaveLength(0)

    act(() => map.fire('load'))

    expect(map.flyToCalls).toHaveLength(1)
    expect(map.flyToCalls[0].center).toEqual([event.lon, event.lat])
    expect(fake.FakePopup.instances).toHaveLength(1)
    expect(fake.FakePopup.instances[0].added).toBe(1)
  })

  it('still reacts to a selection made after the map is ready', () => {
    const event = makeEvent({ id: 'emsc:later', time: minutesAgo(30) })
    render(<MapView events={[event]} now={NOW} />)
    const map = lastMap()
    act(() => map.fire('load'))
    expect(map.flyToCalls).toHaveLength(0)

    act(() => useStore.getState().select('emsc:later'))

    expect(map.flyToCalls).toHaveLength(1)
    expect(fake.FakePopup.instances).toHaveLength(1)
  })
})

describe('the popup must not freeze the age it opened with', () => {
  /* The popup is built once per selection. Its "30 min ago" therefore stayed
   * frozen while the LivePanel two centimetres away kept counting -- stale
   * data presented as live, which is exactly what this product forbids. */
  it('keeps counting while the popup stays open', () => {
    const event = makeEvent({ id: 'emsc:age', time: minutesAgo(30) })
    useStore.setState({ selected: 'emsc:age' })
    const { rerender } = render(<MapView events={[event]} now={NOW} />)
    act(() => lastMap().fire('load'))

    const popup = fake.FakePopup.instances.at(-1)!
    expect(popup.element.textContent).toContain('30 min ago')

    rerender(<MapView events={[event]} now={NOW + 120_000} />)

    expect(popup.element.textContent).toContain('32 min ago')
    // the popup itself is NOT rebuilt: a link being read must not vanish
    // under the cursor once a second
    expect(fake.FakePopup.instances).toHaveLength(1)
  })
})

describe('prefers-reduced-motion', () => {
  /* The stylesheet honours the setting; the map defeated it. `essential: true`
   * is MapLibre's explicit opt-OUT of its own reduced-motion handling, and the
   * halo pulse ran on requestAnimationFrame regardless. Someone who asked
   * their system for less motion still got camera flights and throbbing
   * circles. */
  it('jumps instead of flying when motion is refused', () => {
    stubMatchMedia([REDUCED_MOTION])
    const event = makeEvent({ id: 'emsc:calm', time: minutesAgo(30) })
    useStore.setState({ selected: 'emsc:calm' })

    render(<MapView events={[event]} now={NOW} />)
    act(() => lastMap().fire('load'))

    const map = lastMap()
    expect(map.flyToCalls).toHaveLength(0)
    expect(map.jumpToCalls).toHaveLength(1)
    expect(map.jumpToCalls[0].center).toEqual([event.lon, event.lat])
  })

  it('flies when motion is not refused', () => {
    stubMatchMedia([])
    const event = makeEvent({ id: 'emsc:fly', time: minutesAgo(30) })
    useStore.setState({ selected: 'emsc:fly' })

    render(<MapView events={[event]} now={NOW} />)
    act(() => lastMap().fire('load'))

    expect(lastMap().flyToCalls).toHaveLength(1)
    expect(lastMap().jumpToCalls).toHaveLength(0)
  })

  it('shows the fresh halo without pulsing it', () => {
    stubMatchMedia([REDUCED_MOTION])
    const raf = vi.spyOn(window, 'requestAnimationFrame')
    const event = makeEvent({ id: 'emsc:halo', time: minutesAgo(30) })
    useStore.setState({ fresh: new Set(['emsc:halo']) })

    render(<MapView events={[event]} now={NOW} />)
    act(() => lastMap().fire('load'))

    expect(raf).not.toHaveBeenCalled()
    // the halo is still THERE -- freshness is information, the pulse is decor
    const opacity = lastMap().paint.filter(
      ([layer, property]) => layer === 'events-halo' && property === 'circle-opacity',
    )
    expect(opacity).toHaveLength(1)
    expect(opacity[0][2]).toBeGreaterThan(0)
  })

  it('pulses the halo when motion is not refused', () => {
    stubMatchMedia([])
    const raf = vi.spyOn(window, 'requestAnimationFrame')
    const event = makeEvent({ id: 'emsc:halo', time: minutesAgo(30) })
    useStore.setState({ fresh: new Set(['emsc:halo']) })

    render(<MapView events={[event]} now={NOW} />)
    act(() => lastMap().fire('load'))

    expect(raf).toHaveBeenCalled()
  })
})
