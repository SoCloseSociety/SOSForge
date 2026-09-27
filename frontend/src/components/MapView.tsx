import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl, { Map as MapLibreMap, Popup } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useStore } from '../store'
import { SEVERITY_META, formatAge, kindLabel, severityLabel } from '../format'
import type { SosEvent } from '../types'
import { isWaveCandidate, waveFronts } from '../waves'
import { forecastTracks } from '../tracks'
import { useMediaQuery } from '../useMediaQuery'

/** Someone who asked their system for less motion asked the whole product,
 * not just the stylesheet. The map is where the motion actually is: camera
 * flights and a pulsing halo. */
const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

/** Dark basemap from OpenFreeMap: OpenStreetMap vector tiles served without
 * any API key, registration or quota (openfreemap.org). Attribution comes
 * with the tile metadata and is shown by the map's own control.
 *
 * Until 2026-09 this was CARTO's `dark_all` raster. CARTO then began
 * answering every tile request -- whatever the referrer -- with the same
 * 2.5 kB "API KEY REQUIRED" placeholder, and the live site showed markers
 * scattered over black tiles stamped with a watermark. Verified with curl
 * against three tiles under three referrers: one identical file each time. */
const BASEMAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/dark'

/** What the map falls back to when the style above cannot be fetched (host
 * down, blocked by a stricter CSP, offline): a plain dark background. The
 * plate boundaries, the markers and the wave fronts still render on it. A
 * basemap outage must degrade the picture, never take the alerts down. */
const FALLBACK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'base', type: 'background', paint: { 'background-color': '#0d0d0d' } }],
}

const SEVERITY_COLOR: maplibregl.ExpressionSpecification = [
  'match',
  ['get', 'severity'],
  'extreme',
  SEVERITY_META.extreme.color,
  'severe',
  SEVERITY_META.severe.color,
  'moderate',
  SEVERITY_META.moderate.color,
  'minor',
  SEVERITY_META.minor.color,
  SEVERITY_META.info.color,
]

/** Radius: magnitude when it exists, otherwise a fixed radius indexed on
 * severity. A magnitude 7 is not "3.5x" a magnitude 2: the scale is
 * logarithmic in energy, so we grow fast at the top of the spectrum. */
const RADIUS: maplibregl.ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  1,
  ['interpolate', ['linear'], ['get', 'weight'], 0, 3, 3, 5, 5, 9, 7, 16, 9, 24],
  6,
  ['interpolate', ['linear'], ['get', 'weight'], 0, 6, 3, 10, 5, 18, 7, 32, 9, 48],
]

function toFeatureCollection(events: SosEvent[], fresh: Set<string>): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: events
      .filter((e) => e.lat !== null && e.lon !== null)
      .map((event) => ({
        type: 'Feature',
        id: event.id,
        geometry: { type: 'Point', coordinates: [event.lon as number, event.lat as number] },
        properties: {
          id: event.id,
          severity: event.severity,
          kind: event.kind,
          // alerts without a magnitude still need a readable size
          weight: event.magnitude ?? (event.severity === 'extreme' ? 6 : 4),
          fresh: fresh.has(event.id) ? 1 : 0,
        },
      })),
  }
}

function popupHtml(event: SosEvent, now: number): string {
  const t = useStore.getState().t
  const severity = SEVERITY_META[event.severity]

  // EVERY field coming from a source goes through `escape`. `mag_type` was the
  // one field missed: it does come from an external feed (AFAD `type`,
  // USGS/INGV `magType`) and was going straight into the popup's HTML as-is.
  const escape = (value: string) =>
    value.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)

  const rows: string[] = []
  if (event.magnitude !== null)
    rows.push(
      `<dt>${t('detail.magnitude')}</dt><dd>${event.magnitude} ${escape(event.mag_type ?? '')}</dd>`,
    )
  if (event.depth_km !== null)
    rows.push(`<dt>${t('detail.depth')}</dt><dd>${Math.round(event.depth_km)} km</dd>`)
  rows.push(`<dt>${t('detail.time')}</dt><dd>${event.time.slice(11, 19)}</dd>`)
  rows.push(`<dt>${t('detail.source')}</dt><dd>${escape(event.source)}</dd>`)

  return `<div class="popup">
    <h3>${escape(event.place || event.title)}</h3>
    <div style="color:${severity.text};font-size:12px">
      ${severity.glyph} ${severityLabel(t, event.severity)} &middot; ${kindLabel(t, event.kind)}
      &middot; <span class="popup-age">${formatAge(t, (now - Date.parse(event.time)) / 1000)}</span>
    </div>
    <dl>${rows.join('')}</dl>
    ${event.url ? `<p style="margin:8px 0 0"><a href="${escape(event.url)}" target="_blank" rel="noreferrer">${t('detail.official')}</a></p>` : ''}
  </div>`
}

export function MapView({ events, now }: { events: SosEvent[]; now: number }) {
  const t = useStore((s) => s.t)
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const popup = useRef<Popup | null>(null)
  /** Event the open popup belongs to. */
  const popupId = useRef<string | null>(null)
  /** Instant the open popup is dated from, so its age can keep counting
   * without rebuilding the whole card. */
  const popupTime = useRef<number | null>(null)
  /** Readiness is STATE and not a ref, and that is the whole fix for a deep
   * link: `#e/...` selects the event at App mount, while the map is lazily
   * loaded and fires `load` much later. With a ref, the selection effect ran
   * once, bailed out, and had nothing left to re-trigger it -- `selected`
   * never changes again. The shared link centred nothing and opened nothing.
   * As state, the flip to `true` re-runs every effect that was waiting. */
  const [ready, setReady] = useState(false)
  const reduceMotion = useMediaQuery(REDUCED_MOTION)
  const [failed, setFailed] = useState(false)
  const latest = useRef({ events, now })
  latest.current = { events, now }

  // --- initialization, once only
  useEffect(() => {
    if (!container.current || map.current) return

    // MapLibre requires WebGL. Without a GPU (virtual machine, locked-down
    // browser, broken driver) the constructor throws, and an uncaught
    // exception here would take down the WHOLE app: the alert feed would
    // disappear because of a basemap. We degrade, we don't die.
    let instance: MapLibreMap
    try {
      instance = new maplibregl.Map({
        container: container.current,
        style: BASEMAP_STYLE_URL,
        center: [10, 20],
        zoom: 1.4,
        attributionControl: { compact: true },
      })
    } catch (error) {
      console.warn('carte indisponible (WebGL):', error)
      setFailed(true)
      return
    }
    map.current = instance
    // An error BEFORE the style is loaded is the style itself failing to
    // arrive: without one, MapLibre never fires `load`, and none of the
    // layers below would exist -- the whole map, markers included, would
    // stay blank because a third-party basemap was unreachable. Swap in the
    // local fallback style once; `load` then fires on the next frame.
    let fallbackApplied = false
    instance.on('error', (event) => {
      console.warn('maplibre:', event.error?.message ?? event)
      if (!fallbackApplied && !instance.isStyleLoaded()) {
        fallbackApplied = true
        console.warn('basemap unavailable: showing the events on a plain background')
        instance.setStyle(FALLBACK_STYLE)
      }
    })
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

    instance.on('load', () => {
      // Tectonic plate boundaries, at the very bottom of the stack.
      //
      // This is the only static layer in the product, and it earns its place:
      // almost every earthquake on the map sits on one of these lines, and
      // seeing that turns a scatter of dots into a readable planet. Subduction
      // zones are drawn thicker -- that is where the biggest quakes and nearly
      // every tsunami come from.
      instance.addSource('plates', { type: 'geojson', data: '/plate-boundaries.json' })
      instance.addLayer({
        id: 'plates',
        type: 'line',
        source: 'plates',
        paint: {
          'line-color': ['case', ['==', ['get', 'type'], 'subduction'], '#8a6a3a', '#4a4a46'],
          'line-width': ['case', ['==', ['get', 'type'], 'subduction'], 1.6, 0.9],
          'line-opacity': 0.75,
        },
      })

      instance.addSource('events', { type: 'geojson', data: toFeatureCollection([], new Set()) })

      // P and S wave fronts, BELOW the markers: they provide context, they
      // must never hide the event itself.
      instance.addSource('waves', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })
      instance.addLayer({
        id: 'waves-p',
        type: 'line',
        source: 'waves',
        filter: ['==', ['get', 'phase'], 'p'],
        paint: {
          'line-color': '#9ec5f4',
          'line-width': 1.2,
          'line-opacity': ['*', ['get', 'opacity'], 0.55],
        },
      })
      instance.addLayer({
        id: 'waves-s',
        type: 'line',
        source: 'waves',
        filter: ['==', ['get', 'phase'], 's'],
        paint: {
          'line-color': SEVERITY_META.severe.color,
          'line-width': 2,
          'line-opacity': ['*', ['get', 'opacity'], 0.8],
        },
      })

      // Forecast cyclone tracks: the only line on this map that shows the
      // FUTURE. Dashed on purpose -- a solid line would read as observed.
      instance.addSource('tracks', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })
      instance.addLayer({
        id: 'tracks-line',
        type: 'line',
        source: 'tracks',
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': SEVERITY_META.severe.color,
          'line-width': 2,
          'line-dasharray': [2, 2],
          'line-opacity': 0.85,
        },
      })
      instance.addLayer({
        id: 'tracks-point',
        type: 'circle',
        source: 'tracks',
        filter: ['==', ['geometry-type'], 'Point'],
        paint: {
          'circle-radius': 3.5,
          'circle-color': SEVERITY_META.severe.color,
          'circle-opacity': 0.9,
          'circle-stroke-width': 1,
          'circle-stroke-color': 'rgba(0,0,0,0.5)',
        },
      })

      // halo for just-arrived events: it's the "this just happened" signal,
      // animated by the loop further below
      instance.addLayer({
        id: 'events-halo',
        type: 'circle',
        source: 'events',
        filter: ['==', ['get', 'fresh'], 1],
        paint: {
          'circle-radius': RADIUS,
          'circle-color': SEVERITY_COLOR,
          'circle-opacity': 0.25,
          'circle-stroke-width': 0,
        },
      })

      instance.addLayer({
        id: 'events-dot',
        type: 'circle',
        source: 'events',
        paint: {
          'circle-radius': RADIUS,
          'circle-color': SEVERITY_COLOR,
          'circle-opacity': 0.72,
          'circle-stroke-width': 1,
          'circle-stroke-color': 'rgba(255,255,255,0.55)',
        },
      })

      setReady(true)
      instance.getSource('events') &&
        (instance.getSource('events') as maplibregl.GeoJSONSource).setData(
          toFeatureCollection(latest.current.events, useStore.getState().fresh),
        )

      instance.on('click', 'events-dot', (event) => {
        const id = event.features?.[0]?.properties?.id as string | undefined
        if (id) useStore.getState().select(id)
      })
      instance.on('mouseenter', 'events-dot', () => {
        instance.getCanvas().style.cursor = 'pointer'
      })
      instance.on('mouseleave', 'events-dot', () => {
        instance.getCanvas().style.cursor = ''
      })
    })

    return () => {
      instance.remove()
      map.current = null
      setReady(false)
    }
  }, [])

  // --- data
  const fresh = useStore((s) => s.fresh)
  useEffect(() => {
    if (!map.current || !ready) return
    const source = map.current.getSource('events') as maplibregl.GeoJSONSource | undefined
    source?.setData(toFeatureCollection(events, fresh))
  }, [events, fresh, ready])

  // --- forecast tracks: fed from the storms' raw payload
  useEffect(() => {
    if (!map.current || !ready) return
    const source = map.current.getSource('tracks') as maplibregl.GeoJSONSource | undefined
    source?.setData(forecastTracks(events))
  }, [events, ready])

  // --- wave fronts: a loop that runs ONLY when there's an earthquake recent
  // enough for its waves to still be propagating. The rest of the time, no
  // frame is computed.
  const waveCandidates = useMemo(
    () => events.filter((e) => isWaveCandidate(e, now)).map((e) => e.id).join(','),
    // `now` advances every second: we only restart the loop if the LIST of
    // relevant earthquakes changes, not on every tick
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events, Math.floor(now / 30_000)],
  )

  // Deliberately NOT gated on prefers-reduced-motion: the front's position is
  // the information itself -- where the shaking is arriving right now --
  // whereas the halo pulse and the camera flight are only ways of drawing
  // attention. Freezing this one would not calm the page, it would make it
  // wrong.
  useEffect(() => {
    if (!waveCandidates) return
    let frame = 0
    const animate = () => {
      const instance = map.current
      const source = instance?.getSource('waves') as maplibregl.GeoJSONSource | undefined
      if (source) {
        // the server clock, not the browser's: a front drawn against a wrong
        // clock would be in the wrong place
        const serverNow = Date.now() + useStore.getState().clockSkew
        source.setData(waveFronts(latest.current.events, serverNow))
      }
      frame = requestAnimationFrame(animate)
    }
    frame = requestAnimationFrame(animate)
    return () => {
      cancelAnimationFrame(frame)
      const source = map.current?.getSource('waves') as maplibregl.GeoJSONSource | undefined
      source?.setData({ type: 'FeatureCollection', features: [] })
    }
  }, [waveCandidates])

  // --- halo pulsation, only when there's something fresh
  useEffect(() => {
    if (fresh.size === 0) return
    // Reduced motion: the halo stays, the throbbing goes. Freshness is
    // information -- it says "this just happened" -- while the pulse is only
    // the way we draw attention to it, and it is exactly the kind of repeated
    // movement that makes a vestibular disorder unbearable. So we paint the
    // halo once, statically, and start no frame loop at all.
    if (reduceMotion) {
      const instance = map.current
      if (instance && ready && instance.getLayer('events-halo')) {
        instance.setPaintProperty('events-halo', 'circle-opacity', 0.25)
        instance.setPaintProperty('events-halo', 'circle-radius', [
          '*',
          RADIUS,
          1.8,
        ] as unknown as maplibregl.ExpressionSpecification)
      }
      return
    }
    let frame = 0
    const animate = () => {
      const instance = map.current
      if (instance && ready && instance.getLayer('events-halo')) {
        const phase = (Date.now() % 1600) / 1600
        instance.setPaintProperty('events-halo', 'circle-opacity', 0.3 * (1 - phase))
        instance.setPaintProperty('events-halo', 'circle-radius', [
          '*',
          RADIUS,
          1 + phase * 2.2,
        ] as unknown as maplibregl.ExpressionSpecification)
      }
      frame = requestAnimationFrame(animate)
    }
    frame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frame)
  }, [fresh, ready, reduceMotion])

  /** Move the camera, honouring the reader's motion setting.
   *
   * `essential: true` is MapLibre's explicit opt-OUT of its own
   * prefers-reduced-motion handling: passing it unconditionally is what
   * defeated the setting. We take the decision here instead -- jump when
   * motion is refused, fly otherwise -- and only then is `essential` honest,
   * since we have already checked. */
  const moveCamera = (
    instance: MapLibreMap,
    center: [number, number],
    zoom: number,
    speed: number,
  ) => {
    if (reduceMotion) instance.jumpTo({ center, zoom })
    else instance.flyTo({ center, zoom, speed, curve: 1.5, essential: true })
  }

  // --- search: go to the requested area
  const focus = useStore((s) => s.focus)
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready || !focus) return
    moveCamera(instance, [focus.lon, focus.lat], focus.zoom, 1.6)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- moveCamera is
    // rebuilt on every render; `reduceMotion` is what actually changes it
  }, [focus, ready, reduceMotion])

  // --- selection: center on it and open the card
  const selected = useStore((s) => s.selected)
  /** Is the selected event on the map RIGHT NOW? A deep link can also land
   * before the websocket snapshot does, and then the id matches nothing yet.
   * A boolean, so the effect wakes up when the event finally arrives and not
   * on every one-second tick of the feed (which would re-fly the camera and
   * rebuild the popup a second after it opened). */
  const selectedPlaced = useMemo(
    () => events.some((e) => e.id === selected && e.lat !== null && e.lon !== null),
    [events, selected],
  )
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready) return
    // Close the card only when it belongs to a DIFFERENT event. Reopening it
    // on every re-run would tear it down under the reader's cursor, and
    // closing it because the event dropped out of the current filter would
    // silently clear a selection the panel beside the map still shows.
    if (popupId.current !== selected) {
      popup.current?.remove()
      popup.current = null
      popupId.current = null
      popupTime.current = null
    }
    if (!selected || popup.current) return

    const event = latest.current.events.find((e) => e.id === selected)
    // not here YET: `selectedPlaced` brings us back when it arrives
    if (!event || event.lat === null || event.lon === null) return

    // Zoom in as close as possible to the area: the user clicks to SEE what's
    // happening there, not to guess a continent. A point event (earthquake,
    // volcano) is viewed at neighborhood scale; an alert described by an
    // administrative zone (NWS, GDACS) only makes sense at regional scale --
    // pulling it in to 300 m would show nothing but a field.
    const pointLike = event.source !== 'nws' && event.source !== 'gdacs'
    moveCamera(instance, [event.lon, event.lat], pointLike ? 11 : 8, 1.4)
    popup.current = new maplibregl.Popup({ closeButton: true, maxWidth: '320px' })
      .setLngLat([event.lon, event.lat])
      .setHTML(popupHtml(event, latest.current.now))
      .addTo(instance)
    popupId.current = event.id
    popupTime.current = Date.parse(event.time)
    popup.current.on('close', () => {
      if (useStore.getState().selected === event.id) useStore.getState().select(null)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see moveCamera
  }, [selected, selectedPlaced, ready, reduceMotion])

  // --- the open popup's age must keep counting
  //
  // The card is built once per selection, so "12 s ago" froze at the instant
  // it opened while the LivePanel beside it kept counting: stale data
  // presented as live, which is the one thing this product forbids itself.
  // Only the age NODE is rewritten -- rebuilding the popup once a second
  // would tear the official link out from under the cursor and close any
  // text selection.
  useEffect(() => {
    if (popupTime.current === null) return
    const age = popup.current?.getElement()?.querySelector('.popup-age')
    if (!age) return
    age.textContent = formatAge(useStore.getState().t, (now - popupTime.current) / 1000)
  }, [now, selected, ready])

  if (failed) {
    return (
      <div className="map-wrap map-fallback">
        <p>
          <strong>{t('map.unavailable')}</strong>
          <br />
          {t('map.unavailable.detail')}
        </p>
      </div>
    )
  }

  return (
    <div className="map-wrap">
      <div className="map" ref={container} />
      <div className="legend">
        <h4>{t('map.legend')}</h4>
        <ul>
          {(['extreme', 'severe', 'moderate', 'minor', 'info'] as const).map((key) => (
            <li key={key}>
              <span className="swatch" style={{ background: SEVERITY_META[key].color }} />
              <span aria-hidden="true">{SEVERITY_META[key].glyph}</span>
              {severityLabel(t, key)}
            </li>
          ))}
        </ul>
        {events.some((e) => isWaveCandidate(e, now)) ? (
          <ul className="legend-waves">
            <li>
              <span className="wave-line p" /> {t('wave.p')}
            </li>
            <li>
              <span className="wave-line s" /> {t('wave.s')}
            </li>
          </ul>
        ) : null}
      </div>
    </div>
  )
}
