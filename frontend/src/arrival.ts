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
 * rough far away. It is deliberately capped: past the distance where a constant
 * speed stops meaning anything, we say nothing rather than say something wrong.
 */

const EARTH_RADIUS_KM = 6371

/** Beyond this, a constant-speed model is no longer honest. */
const MAX_USEFUL_KM = 1000

/** Below this magnitude nothing is felt at a distance, so a countdown would be
 * theatre. */
const MIN_MAGNITUDE = 4.0

export interface Arrival {
  event: SosEvent
  distanceKm: number
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

  const km = distanceKm(lat, lon, event.lat, event.lon)
  if (km > MAX_USEFUL_KM) return null

  const elapsed = (now - Date.parse(event.time)) / 1000
  // an event dated in the future would produce an arrival in the past
  if (elapsed < 0) return null

  return {
    event,
    distanceKm: km,
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
