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
    styleLoaded = false
    setStyleCalls: unknown[] = []

    constructor(public options: Record<string, unknown>) {
      FakeMap.instances.push(this)
    }

    isStyleLoaded() {
      return this.styleLoaded
    }

    setStyle(style: unknown) {
      this.setStyleCalls.push(style)
      this.styleLoaded = true
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

    isMoving() {
      return false
    }

    /** Where a point is on screen: fixed, what is tested is who asks. */
    projected: unknown[] = []
    project(lngLat: unknown) {
      this.projected.push(lngLat)
      return { x: 160.4, y: 105.2 }
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

describe('the basemap is a third party, the events are not', () => {
  /* The style is fetched from tiles.openfreemap.org. If that fetch fails,
   * MapLibre never fires `load`, and every layer the component adds on
   * `load` -- plates, markers, wave fronts -- would never exist: a remote
   * basemap outage would blank the alerts. The component must swap in its
   * local fallback style, once. */
  it('falls back to a plain background when the remote style fails before load', () => {
    render(<MapView events={[makeEvent({ time: minutesAgo(30) })]} now={NOW} />)
    const map = lastMap()
    expect(map.options.style).toBe('https://tiles.openfreemap.org/styles/dark')

    act(() => map.fire('error', { error: new Error('style: 503') }))
    expect(map.setStyleCalls).toHaveLength(1)
    expect((map.setStyleCalls[0] as { sources: object }).sources).toEqual({})

    // once only: a later tile error is not a reason to reset the style
    act(() => map.fire('error', { error: new Error('tile: 404') }))
    expect(map.setStyleCalls).toHaveLength(1)
  })

  /* Seen in a real Chrome on 2026-10-26: `isStyleLoaded()` is false while
   * TILES are still loading, long after the style itself arrived. A tile that
   * failed in that window swapped the whole style for the fallback -- after
   * `load` had already added the plates, the markers and the wave layers --
   * so the map went black with not one marker on it, and MapLibre threw on
   * every frame (`updateTransitions` of a layer that no longer existed). */
  it('never swaps the style once it has arrived, even while tiles still load', () => {
    render(<MapView events={[makeEvent({ time: minutesAgo(30) })]} now={NOW} />)
    const map = lastMap()
    act(() => map.fire('load'))
    map.styleLoaded = false // tiles still in flight
    act(() => map.fire('error', { error: new Error('tile: timeout') }))
    expect(map.setStyleCalls).toHaveLength(0)
  })

  it('leaves a loaded style alone when a tile fails', () => {
    render(<MapView events={[makeEvent({ time: minutesAgo(30) })]} now={NOW} />)
    const map = lastMap()
    map.styleLoaded = true
    act(() => map.fire('error', { error: new Error('tile: 404') }))
    expect(map.setStyleCalls).toHaveLength(0)
  })
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

  it('waits for the event itself when the link arrives before the feed', () => {
    /* The other half of the same race: the map can be ready while the
     * websocket snapshot is still in flight, so the selected id matches
     * nothing yet. Bailing out for good here would leave the shared link just
     * as dead as before. */
    const event = makeEvent({ id: 'emsc:pending', time: minutesAgo(30) })
    useStore.setState({ selected: 'emsc:pending' })

    const { rerender } = render(<MapView events={[]} now={NOW} />)
    act(() => lastMap().fire('load'))
    expect(lastMap().flyToCalls).toHaveLength(0)

    rerender(<MapView events={[event]} now={NOW} />)

    expect(lastMap().flyToCalls).toHaveLength(1)
    expect(fake.FakePopup.instances).toHaveLength(1)
  })

  it('opens the popup once, not on every tick of the feed', () => {
    const event = makeEvent({ id: 'emsc:once', time: minutesAgo(30) })
    useStore.setState({ selected: 'emsc:once' })
    const { rerender } = render(<MapView events={[event]} now={NOW} />)
    act(() => lastMap().fire('load'))

    // a new array on every tick, as App produces it
    rerender(<MapView events={[{ ...event }]} now={NOW + 1000} />)
    rerender(<MapView events={[{ ...event }]} now={NOW + 2000} />)

    expect(lastMap().flyToCalls).toHaveLength(1)
    expect(fake.FakePopup.instances).toHaveLength(1)
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

describe('the place report button over the map', () => {
  /* The place report's button sits over the map's top-left (App.tsx
   * `.map-report`). At 320x568 and 200 % text it was two lines tall, y 12
   * to 99 on a 139 px map, and a flight to the place put the place at the
   * map's centre, y 70: under the opaque button. The flight now pads its
   * top by what the button covers, like the event panel on the right. */
  const NONE = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }
  function layout(rects: Record<string, Partial<DOMRect>>) {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const hit = Object.entries(rects).find(([selector]) => this.matches(selector))
      return { ...NONE, ...hit?.[1] } as DOMRect
    })
  }
  const MAP = { top: 0, left: 0, right: 320, bottom: 140, width: 320, height: 140 }
  const KO_SAMUI = { lat: 9.51, lon: 100.01, zoom: 7, name: 'Ko Samui' }

  function mapUnder(button: boolean) {
    render(
      <div className="body">
        {button ? <div className="map-report" /> : null}
        <div className="map-column">
          <MapView events={[]} now={NOW} />
        </div>
      </div>,
    )
    act(() => lastMap().fire('load'))
    act(() => useStore.getState().setFocus(KO_SAMUI))
    return lastMap()
  }

  it('a flight to the place lands below a two-line button, capped at half the map', () => {
    layout({ '.map-wrap > .map': MAP, '.map-report': { top: 12, left: 12, right: 252, bottom: 99, width: 240, height: 87 } })
    const map = mapUnder(true)
    expect(map.flyToCalls).toHaveLength(1)
    expect(map.flyToCalls[0].center).toEqual([100.01, 9.51])
    // 99 + 12 = 111, capped at 70: the place lands at y 105, below y 99
    expect(map.flyToCalls[0].padding).toEqual({ top: 70, bottom: 0, left: 0, right: 0 })
  })

  it('a one-line button pads by what it covers, plus the 12 px margin', () => {
    layout({ '.map-wrap > .map': MAP, '.map-report': { top: 12, left: 12, right: 56, bottom: 56, width: 44, height: 44 } })
    expect(mapUnder(true).flyToCalls[0].padding).toEqual({ top: 68, bottom: 0, left: 0, right: 0 })
  })

  it('no button, no padding: Near me and the world fly to the centre', () => {
    layout({ '.map-wrap > .map': MAP })
    expect(mapUnder(false).flyToCalls[0].padding).toEqual({ top: 0, bottom: 0, left: 0, right: 0 })
  })

  it('says where the place landed once the camera stops, and nothing while it moves', () => {
    layout({ '.map-wrap > .map': MAP, '.map-report': { top: 12, left: 12, right: 56, bottom: 56, width: 44, height: 44 } })
    render(
      <div className="body">
        <div className="map-report" />
        <div className="map-column">
          <MapView events={[]} now={NOW} />
        </div>
      </div>,
    )
    const map = lastMap()
    const el = document.querySelector<HTMLElement>('.map-wrap > .map')!
    act(() => map.fire('load'))
    // a camera stop before any flight says nothing
    act(() => map.fire('moveend'))
    expect(el.dataset.focusAt).toBeUndefined()
    act(() => useStore.getState().setFocus(KO_SAMUI))
    act(() => map.fire('moveend'))
    expect(map.projected.at(-1)).toEqual([100.01, 9.51])
    expect(el.dataset.focusAt).toBe('160,105')
    act(() => map.fire('movestart'))
    expect(el.dataset.focusAt).toBeUndefined()
  })

  /* The button can arrive AFTER the flight to the place has started: it
   * waits for /api/version (useApiFeature), and a check that got no answer
   * is asked again 5 s later. Measured in a real Chrome at 568x320 and
   * 200 % text, the first answer held 4 s: the button appeared at 4.8 s,
   * x 12 to 330 and y 12 to 62 of a 99 px map, and the place stayed where
   * the unpadded flight had put it, (284, 50): under the button. The flight
   * is padded again when the button arrives or changes size, until the
   * reader (or another flight) moves the camera. */
  describe('a button that arrives or changes size after the flight', () => {
    const LANDSCAPE = { top: 0, left: 0, right: 568, bottom: 99, width: 568, height: 99 }
    const ONE_LINE = { top: 12, left: 12, right: 330, bottom: 62, width: 318, height: 50 }

    /** jsdom has no ResizeObserver: this one is triggered by the test. */
    class FakeResizeObserver {
      static instances: FakeResizeObserver[] = []
      targets = new Set<Element>()
      constructor(public callback: () => void) {
        FakeResizeObserver.instances.push(this)
      }
      observe(el: Element) {
        this.targets.add(el)
      }
      unobserve(el: Element) {
        this.targets.delete(el)
      }
      disconnect() {
        this.targets.clear()
      }
    }
    /** What a browser does after a layout that changed a watched box. */
    function resized(el: Element) {
      for (const observer of FakeResizeObserver.instances) if (observer.targets.has(el)) observer.callback()
    }

    beforeEach(() => {
      FakeResizeObserver.instances = []
      vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    })
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    function Page({ button, events = [] }: { button: boolean; events?: ReturnType<typeof makeEvent>[] }) {
      return (
        <div className="body">
          {button ? <div className="map-report" /> : null}
          <div className="map-column">
            <MapView events={events} now={NOW} />
          </div>
        </div>
      )
    }
    /** The button's mount is seen by a MutationObserver: a microtask. */
    async function settle(change: () => void) {
      await act(async () => {
        change()
        await Promise.resolve()
      })
    }

    it('pads the flight again when the button arrives after it started', async () => {
      layout({ '.map-wrap > .map': LANDSCAPE, '.map-report': ONE_LINE })
      const { rerender } = render(<Page button={false} />)
      const map = lastMap()
      act(() => map.fire('load'))
      act(() => useStore.getState().setFocus(KO_SAMUI))
      expect(map.flyToCalls).toHaveLength(1)
      expect(map.flyToCalls[0].padding).toEqual({ top: 0, bottom: 0, left: 0, right: 0 })
      // the flight's own start says nothing about the reader
      act(() => map.fire('movestart', {}))

      await settle(() => rerender(<Page button />))
      // 62 + 12 = 74, capped at half the 99 px map: the place lands at y 75
      expect(map.flyToCalls).toHaveLength(2)
      expect(map.flyToCalls[1]).toMatchObject({
        center: [100.01, 9.51],
        zoom: 7,
        padding: { top: 50, bottom: 0, left: 0, right: 0 },
      })
    })

    it('jumps there instead when the reader asked for less motion', async () => {
      stubMatchMedia([REDUCED_MOTION])
      layout({ '.map-wrap > .map': LANDSCAPE, '.map-report': ONE_LINE })
      const { rerender } = render(<Page button={false} />)
      const map = lastMap()
      act(() => map.fire('load'))
      act(() => useStore.getState().setFocus(KO_SAMUI))
      await settle(() => rerender(<Page button />))
      expect(map.flyToCalls).toHaveLength(0)
      expect(map.jumpToCalls.map((call) => call.padding)).toEqual([
        { top: 0, bottom: 0, left: 0, right: 0 },
        { top: 50, bottom: 0, left: 0, right: 0 },
      ])
    })

    it('pads it again when the button changes size, and only then', async () => {
      const rects: Record<string, Partial<DOMRect>> = {
        '.map-wrap > .map': { top: 0, left: 0, right: 320, bottom: 200, width: 320, height: 200 },
        // the icon alone
        '.map-report': { top: 12, left: 12, right: 56, bottom: 56, width: 44, height: 44 },
      }
      layout(rects)
      const { rerender } = render(<Page button />)
      const map = lastMap()
      act(() => map.fire('load'))
      act(() => useStore.getState().setFocus(KO_SAMUI))
      const tops = () => map.flyToCalls.map((call) => (call.padding as { top: number }).top)
      expect(tops()).toEqual([68])
      const report = document.querySelector('.map-report')!
      const mapEl = document.querySelector('.map-wrap > .map')!

      // a size report that changes nothing moves nothing
      act(() => resized(report))
      act(() => resized(mapEl))
      expect(tops()).toEqual([68])

      // the label shown again, on two lines: 99 + 12, capped at 100
      rects['.map-report'] = { top: 12, left: 12, right: 252, bottom: 99, width: 240, height: 87 }
      act(() => resized(report))
      expect(tops()).toEqual([68, 100])
      expect(map.flyToCalls[1]).toMatchObject({ center: [100.01, 9.51], zoom: 7 })

      // the map's own height follows the URL bar on a phone (dvh): never
      // watched, or every scroll would move the camera
      rects['.map-wrap > .map'] = { top: 0, left: 0, right: 320, bottom: 180, width: 320, height: 180 }
      act(() => resized(mapEl))
      expect(tops()).toEqual([68, 100])

      // the button goes: the place goes back to the centre
      await settle(() => rerender(<Page button={false} />))
      expect(tops()).toEqual([68, 100, 0])
    })

    it('never pulls back a reader who moved the map', async () => {
      layout({ '.map-wrap > .map': LANDSCAPE, '.map-report': ONE_LINE })
      const { rerender } = render(<Page button={false} />)
      const map = lastMap()
      act(() => map.fire('load'))
      act(() => useStore.getState().setFocus(KO_SAMUI))
      // a drag (MapLibre passes the DOM event that started it)
      act(() => map.fire('movestart', { originalEvent: new MouseEvent('mousedown') }))
      await settle(() => rerender(<Page button />))
      act(() => resized(document.querySelector('.map-report')!))
      expect(map.flyToCalls).toHaveLength(1)
    })

    it('never undoes the flight to an event selected since', async () => {
      const event = makeEvent({ id: 'emsc:since', time: minutesAgo(30) })
      layout({ '.map-wrap > .map': LANDSCAPE, '.map-report': ONE_LINE })
      const { rerender } = render(<Page button={false} events={[event]} />)
      const map = lastMap()
      act(() => map.fire('load'))
      act(() => useStore.getState().setFocus(KO_SAMUI))
      act(() => useStore.getState().select('emsc:since'))
      expect(map.flyToCalls.map((call) => call.center)).toEqual([
        [100.01, 9.51],
        [event.lon, event.lat],
      ])
      await settle(() => rerender(<Page button events={[event]} />))
      expect(map.flyToCalls).toHaveLength(2)
    })

    it('a new place is padded by what the button covers then', async () => {
      layout({ '.map-wrap > .map': LANDSCAPE, '.map-report': ONE_LINE })
      const { rerender } = render(<Page button={false} />)
      const map = lastMap()
      act(() => map.fire('load'))
      act(() => useStore.getState().setFocus(KO_SAMUI))
      act(() => map.fire('movestart', { originalEvent: new MouseEvent('mousedown') }))
      await settle(() => rerender(<Page button />))
      act(() => useStore.getState().setFocus({ lat: 13.75, lon: 100.5, zoom: 7, name: 'Bangkok' }))
      expect(map.flyToCalls.at(-1)).toMatchObject({ center: [100.5, 13.75], padding: { top: 50 } })
      act(() => resized(document.querySelector('.map-report')!))
      expect(map.flyToCalls).toHaveLength(2)
    })
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

  /* MapLibre accepts `zoom` only as the input of a TOP-LEVEL interpolate or
   * step. The halo was sized `['*', RADIUS, k]`, which buries the zoom
   * interpolate inside a product: MapLibre rejected it ("zoom expression may
   * only be used as input to a top-level step or interpolate"), so the halo
   * never grew, and the pulse logged that warning on every animation frame.
   * Seen in a real Chrome on 2026-10-26. */
  it('sizes the halo with zoom at the top of the expression, as MapLibre requires', () => {
    stubMatchMedia([REDUCED_MOTION])
    const event = makeEvent({ id: 'emsc:halo', time: minutesAgo(30) })
    useStore.setState({ fresh: new Set(['emsc:halo']) })

    render(<MapView events={[event]} now={NOW} />)
    act(() => lastMap().fire('load'))

    const radius = lastMap().paint.filter(
      ([layer, property]) => layer === 'events-halo' && property === 'circle-radius',
    )
    expect(radius).toHaveLength(1)
    const expression = radius[0][2] as unknown[]
    expect(expression[0]).toBe('interpolate')
    expect(expression[2]).toEqual(['zoom'])
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
