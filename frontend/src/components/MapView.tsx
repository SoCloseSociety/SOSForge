import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl, { Map as MapLibreMap, Popup } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useStore } from '../store'
import { SEVERITY_META, formatAge, kindLabel, severityLabel } from '../format'
import type { SosEvent } from '../types'
import { isWaveCandidate, waveFronts } from '../waves'
import { forecastTracks } from '../tracks'

/** Dark CARTO basemap: no API key needed, OSM + CARTO attribution required. */
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    carto: {
      type: 'raster',
      tiles: [
        'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
        'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
        'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
      ],
      tileSize: 256,
      attribution: '&copy; OpenStreetMap &copy; CARTO',
    },
  },
  layers: [{ id: 'base', type: 'raster', source: 'carto' }],
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
    <div style="color:${severity.color};font-size:12px">
      ${severity.glyph} ${severityLabel(t, event.severity)} &middot; ${kindLabel(t, event.kind)}
      &middot; ${formatAge(t, (now - Date.parse(event.time)) / 1000)}
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
  const ready = useRef(false)
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
        style: STYLE,
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
    instance.on('error', (event) => console.warn('maplibre:', event.error?.message ?? event))
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

      ready.current = true
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
      ready.current = false
    }
  }, [])

  // --- data
  const fresh = useStore((s) => s.fresh)
  useEffect(() => {
    if (!map.current || !ready.current) return
    const source = map.current.getSource('events') as maplibregl.GeoJSONSource | undefined
    source?.setData(toFeatureCollection(events, fresh))
  }, [events, fresh])

  // --- forecast tracks: fed from the storms' raw payload
  useEffect(() => {
    if (!map.current || !ready.current) return
    const source = map.current.getSource('tracks') as maplibregl.GeoJSONSource | undefined
    source?.setData(forecastTracks(events))
  }, [events])

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
    let frame = 0
    const animate = () => {
      const instance = map.current
      if (instance && ready.current && instance.getLayer('events-halo')) {
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
  }, [fresh])

  // --- search: go to the requested area
  const focus = useStore((s) => s.focus)
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready.current || !focus) return
    instance.flyTo({
      center: [focus.lon, focus.lat],
      zoom: focus.zoom,
      speed: 1.6,
      curve: 1.5,
      essential: true,
    })
  }, [focus])

  // --- selection: center on it and open the card
  const selected = useStore((s) => s.selected)
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready.current) return
    popup.current?.remove()
    popup.current = null
    if (!selected) return

    const event = latest.current.events.find((e) => e.id === selected)
    if (!event || event.lat === null || event.lon === null) return

    // Zoom in as close as possible to the area: the user clicks to SEE what's
    // happening there, not to guess a continent. A point event (earthquake,
    // volcano) is viewed at neighborhood scale; an alert described by an
    // administrative zone (NWS, GDACS) only makes sense at regional scale --
    // pulling it in to 300 m would show nothing but a field.
    const pointLike = event.source !== 'nws' && event.source !== 'gdacs'
    instance.flyTo({
      center: [event.lon, event.lat],
      zoom: pointLike ? 11 : 8,
      speed: 1.4,
      curve: 1.5,
      essential: true,
    })
    popup.current = new maplibregl.Popup({ closeButton: true, maxWidth: '320px' })
      .setLngLat([event.lon, event.lat])
      .setHTML(popupHtml(event, latest.current.now))
      .addTo(instance)
    popup.current.on('close', () => {
      if (useStore.getState().selected === event.id) useStore.getState().select(null)
    })
  }, [selected])

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
