import type { SosEvent } from './types'

/** Propagation of seismic waves, drawn live.
 *
 * What this shows, and that no other part of the interface says: **where
 * the shaking is arriving RIGHT NOW**. An earthquake is a point on a map;
 * what moves is the wave front, and it takes minutes to cross a region.
 * Watching the S circle reach a city is watching the very information this
 * product exists to give.
 *
 * Two fronts, two average speeds in the crust:
 * - **P** (primary, compressional) ~6.0 km/s -- the first, weak jolt;
 * - **S** (secondary, shear) ~3.5 km/s -- the one that does the damage.
 *
 * These are crustal averages, not an earth model: beyond about a thousand
 * kilometers the waves dive into the mantle and speed up. So the circles
 * are accurate near the epicenter, approximate far from it, and we stop
 * them before they become misleading.
 */

export const P_SPEED_KM_S = 6.0
export const S_SPEED_KM_S = 3.5

/** Beyond this, the constant-speed model is no longer worth anything. */
const MAX_RADIUS_KM = 1200
/** An earthquake too weak is felt nowhere: draw nothing. */
const MIN_MAGNITUDE = 4.0

const EARTH_RADIUS_KM = 6371

/** Point reached from an epicenter following a bearing, at a given distance. */
function destination(lat: number, lon: number, bearing: number, distanceKm: number): [number, number] {
  const angular = distanceKm / EARTH_RADIUS_KM
  const phi1 = (lat * Math.PI) / 180
  const lambda1 = (lon * Math.PI) / 180
  const theta = (bearing * Math.PI) / 180

  const phi2 = Math.asin(
    Math.sin(phi1) * Math.cos(angular) + Math.cos(phi1) * Math.sin(angular) * Math.cos(theta),
  )
  const lambda2 =
    lambda1 +
    Math.atan2(
      Math.sin(theta) * Math.sin(angular) * Math.cos(phi1),
      Math.cos(angular) - Math.sin(phi1) * Math.sin(phi2),
    )

  return [((lambda2 * 180) / Math.PI + 540) % 360 - 180, (phi2 * 180) / Math.PI]
}

/** The circle is drawn in geographic coordinates, not pixels: it stays
 * accurate at every zoom level, and deforms correctly near the poles. */
export function waveRing(lat: number, lon: number, radiusKm: number, points = 72): [number, number][] {
  const ring: [number, number][] = []
  for (let i = 0; i <= points; i += 1) {
    ring.push(destination(lat, lon, (i * 360) / points, radiusKm))
  }
  return ring
}

export function isWaveCandidate(event: SosEvent, now: number): boolean {
  if (event.kind !== 'earthquake' || event.lat === null || event.lon === null) return false
  if ((event.magnitude ?? 0) < MIN_MAGNITUDE) return false
  const elapsed = (now - Date.parse(event.time)) / 1000
  // the S front must still be within the zone where the model holds
  return elapsed > 0 && elapsed * S_SPEED_KM_S < MAX_RADIUS_KM
}

/** The two fronts of each recent-enough earthquake, at instant `now`. */
export function waveFronts(events: SosEvent[], now: number): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = []

  for (const event of events) {
    if (!isWaveCandidate(event, now)) continue
    const elapsed = (now - Date.parse(event.time)) / 1000
    const lat = event.lat as number
    const lon = event.lon as number

    for (const [phase, speed] of [
      ['p', P_SPEED_KM_S],
      ['s', S_SPEED_KM_S],
    ] as const) {
      const radius = elapsed * speed
      if (radius <= 0 || radius > MAX_RADIUS_KM) continue
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: waveRing(lat, lon, radius) },
        properties: {
          phase,
          id: event.id,
          // the front fades as it moves outward: it loses meaning with distance
          opacity: Math.max(0, 1 - radius / MAX_RADIUS_KM),
        },
      })
    }
  }

  return { type: 'FeatureCollection', features }
}
