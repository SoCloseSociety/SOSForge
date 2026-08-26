import type { SosEvent } from './types'
import { P_SPEED_KM_S, S_SPEED_KM_S } from './waves'

/** Seismic wave arrival at a place you care about.
 *
 * This is the only genuine anticipation this product can offer for an
 * earthquake, and it is worth being precise about what it is and is not.
 *
 * **It does not predict earthquakes.** No science does. What it does is exploit
 * the gap between two speeds: the alert travels at the speed of light through
 * the network, the destructive waves travel through rock at a few kilometres per
 * second. For an earthquake 200 km away, that gap is about a minute of warning.
 * That is what earthquake early warning systems are built on, and it is the
 * whole reason the JMA EEW source is in this product.
 *
 * The estimate uses average crustal speeds, so it is good near the source and
 * rough far away. It is deliberately capped twice: past the distance where a
 * constant speed stops meaning anything, and past the depth where the waves
 * leave the crust altogether, we say nothing rather than say something wrong.
 */

const EARTH_RADIUS_KM = 6371

/** Beyond this, a constant-speed model is no longer honest. */
const MAX_USEFUL_KM = 1000

/** Below the crust, this model stops applying at all.
 *
 * The waves of a deep-focus earthquake (Japan, Tonga, the Andes: routinely
 * 300 to 600 km) leave the crust and travel through the mantle, where P
 * exceeds 8 km/s instead of the 6.0 used here. The error runs in the
 * dangerous direction: the model would count down from a travel time longer
 * than the real one, so the counter would still be showing seconds while the
 * shaking was already there. On the one element of this interface that
 * behaves like a siren, saying nothing beats being late.
 *
 * 150 km is where the honest crustal model ends -- deeper than any crust,
 * shallow enough to keep every ordinary subduction earthquake.
 */
const MAX_DEPTH_KM = 150

/** Below this magnitude nothing is felt at a distance, so a countdown would be
 * theatre. */
const MIN_MAGNITUDE = 4.0

export interface Arrival {
  event: SosEvent
  /** distance to the EPICENTRE, the point drawn on the map. This is what the
   * reader is shown ("120 km away"): the slant distance below is the model's
   * business, not theirs. */
  distanceKm: number
  /** distance actually travelled, from the HYPOCENTRE. This is what the
   * countdown is computed from. */
  hypocentralKm: number
  /** seconds until the P wave reaches the watched place; negative once passed */
  pIn: number
  /** seconds until the S wave, the damaging one */
  sIn: number
}

export function distanceKm(
  aLat: number,
  aLon: number,
  bLat: number,
  bLon: number,
): number {
  const dLat = ((bLat - aLat) * Math.PI) / 180
  const dLon = ((bLon - aLon) * Math.PI) / 180
  const p1 = (aLat * Math.PI) / 180
  const p2 = (bLat * Math.PI) / 180
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** The arrival of one event at one place, or null if it says nothing useful. */
export function arrivalAt(
  event: SosEvent,
  lat: number,
  lon: number,
  now: number,
): Arrival | null {
  if (event.kind !== 'earthquake' || event.lat === null || event.lon === null) return null
  if ((event.magnitude ?? 0) < MIN_MAGNITUDE) return null

  const depth = event.depth_km ?? 0
  if (depth > MAX_DEPTH_KM) return null

  // An earthquake happens at a hypocentre, not at the dot on the map. The
  // waves travel the slant distance from that point, and close in, the depth
  // is not a rounding error: 40 km away at 100 km deep is 108 km of rock,
  // nearly three times the distance the map shows -- 31 s for the S wave, not
  // 11 s. Counting from the surface distance runs the countdown out before
  // the shaking arrives, which is the one failure mode a countdown must not
  // have. A missing depth is treated as the surface: the only assumption that
  // adds nothing we do not know.
  const epicentralKm = distanceKm(lat, lon, event.lat, event.lon)
  const km = Math.sqrt(epicentralKm ** 2 + depth ** 2)
  if (km > MAX_USEFUL_KM) return null

  const elapsed = (now - Date.parse(event.time)) / 1000
  // an event dated in the future would produce an arrival in the past
  if (elapsed < 0) return null

  return {
    event,
    distanceKm: epicentralKm,
    hypocentralKm: km,
    pIn: km / P_SPEED_KM_S - elapsed,
    sIn: km / S_SPEED_KM_S - elapsed,
  }
}

/** The one arrival worth showing: waves still inbound, soonest first.
 *
 * Once the S wave has passed there is nothing left to warn about -- the shaking
 * either happened or it did not, and a countdown at zero would just be noise. */
export function nextArrival(
  events: SosEvent[],
  lat: number,
  lon: number,
  now: number,
): Arrival | null {
  let best: Arrival | null = null
  for (const event of events) {
    const arrival = arrivalAt(event, lat, lon, now)
    if (!arrival || arrival.sIn <= 0) continue
    if (!best || arrival.sIn < best.sIn) best = arrival
  }
  return best
}
