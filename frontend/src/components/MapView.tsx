import { useEffect, useRef, useState } from 'react'
import maplibregl, { Map as MapLibreMap, Popup } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useStore } from '../store'
import { SEVERITY_META, formatAge, kindLabel, severityLabel } from '../format'
import type { SosEvent } from '../types'

/** Fond de carte sombre CARTO: pas de cle API, attribution OSM + CARTO obligatoire. */
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

/** Rayon: la magnitude quand elle existe, sinon un rayon fixe indexe sur la
 * gravite. Une magnitude 7 n'est pas "3.5x" une magnitude 2: l'echelle est
 * logarithmique en energie, donc on grossit vite dans le haut du spectre. */
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
          // les alertes sans magnitude ont quand meme besoin d'une taille lisible
          weight: event.magnitude ?? (event.severity === 'extreme' ? 6 : 4),
          fresh: fresh.has(event.id) ? 1 : 0,
        },
      })),
  }
}

function popupHtml(event: SosEvent, now: number): string {
  const t = useStore.getState().t
  const severity = SEVERITY_META[event.severity]
  const rows: string[] = []
  if (event.magnitude !== null)
    rows.push(`<dt>${t('detail.magnitude')}</dt><dd>${event.magnitude} ${event.mag_type ?? ''}</dd>`)
  if (event.depth_km !== null)
    rows.push(`<dt>${t('detail.depth')}</dt><dd>${Math.round(event.depth_km)} km</dd>`)
  rows.push(`<dt>${t('detail.time')}</dt><dd>${event.time.slice(11, 19)}</dd>`)
  rows.push(`<dt>${t('detail.source')}</dt><dd>${event.source}</dd>`)

  const escape = (value: string) =>
    value.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)

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

  // --- initialisation, une seule fois
  useEffect(() => {
    if (!container.current || map.current) return

    // MapLibre exige WebGL. Sans GPU (machine virtuelle, navigateur bride,
    // pilote casse) le constructeur leve, et une exception non rattrapee ici
    // demonterait TOUTE l'application: le flux d'alertes disparaitrait a cause
    // d'un fond de carte. On degrade, on ne meurt pas.
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
      instance.addSource('events', { type: 'geojson', data: toFeatureCollection([], new Set()) })

      // halo des evenements tout juste arrives: c'est le signal "ca vient de
      // tomber", anime par la boucle plus bas
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

  // --- donnees
  const fresh = useStore((s) => s.fresh)
  useEffect(() => {
    if (!map.current || !ready.current) return
    const source = map.current.getSource('events') as maplibregl.GeoJSONSource | undefined
    source?.setData(toFeatureCollection(events, fresh))
  }, [events, fresh])

  // --- pulsation du halo, uniquement quand il y a quelque chose de frais
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

  // --- recherche: aller a la zone demandee
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

  // --- selection: on centre et on ouvre la fiche
  const selected = useStore((s) => s.selected)
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready.current) return
    popup.current?.remove()
    popup.current = null
    if (!selected) return

    const event = latest.current.events.find((e) => e.id === selected)
    if (!event || event.lat === null || event.lon === null) return

    // Zoom au plus pres de la zone: l'utilisateur clique pour VOIR ce qui se
    // passe la-bas, pas pour deviner un continent. Un evenement ponctuel
    // (seisme, volcan) se regarde a l'echelle du quartier; une alerte decrite
    // par une zone administrative (NWS, GDACS) n'a de sens qu'a l'echelle de la
    // region, la ramener a 300 m ne montrerait qu'un champ.
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
      </div>
    </div>
  )
}
