import type { SosEvent } from './types'

/** Forecast cyclone tracks, taken from the storm's own payload.
 *
 * The NHC publishes where a storm is FORECAST to go, up to five days out. It
 * belongs to the same event as the storm itself -- one storm is one marker --
 * so the track travels inside `raw` and is unpacked here for drawing.
 *
 * Drawn dashed, and never solid: on a map where every other line is something
 * that has already happened, a solid forecast would be read as observation.
 */

interface TrackPoint {
  tau?: number
  valid?: string
  lat?: number
  lon?: number
  wind_kt?: number
  category?: number
}

export function forecastTracks(events: SosEvent[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = []

  for (const event of events) {
    if (event.kind !== 'cyclone') continue
    const track: TrackPoint[] | null = event.forecast_track
    if (!Array.isArray(track) || track.length < 2) continue

    const points = track.filter(
      (p): p is TrackPoint & { lat: number; lon: number } =>
        typeof p.lat === 'number' && typeof p.lon === 'number',
    )
    if (points.length < 2) continue

    features.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: points.map((p) => [p.lon, p.lat]) },
      properties: { id: event.id, name: event.place },
    })

    for (const point of points) {
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [point.lon, point.lat] },
        properties: {
          id: event.id,
          tau: point.tau ?? null,
          wind_kt: point.wind_kt ?? null,
        },
      })
    }
  }

  return { type: 'FeatureCollection', features }
}
