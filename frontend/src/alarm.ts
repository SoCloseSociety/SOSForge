import type { Kind, Severity, SosEvent } from './types'
import { distanceKm } from './arrival'

/** Proximity alarms: an alert that matters where YOU are.
 *
 * The feed is worldwide, which is exactly what makes it useless as a warning:
 * a magnitude 6 on the other side of the planet and one 40 km away scroll past
 * at the same size. This module answers one question -- does this event concern
 * the place the user is standing in right now?
 *
 * Everything here is opt-in. The location is never guessed: the browser asks,
 * the user grants, and nothing leaves the device -- distances are computed in
 * the page, and the server is never told where anyone is.
 */

/** How far each hazard actually reaches.
 *
 * These are not arbitrary. An earthquake's felt radius grows with magnitude,
 * so it gets a formula rather than a constant. A tsunami concerns a whole
 * coastline. A flood warning is issued for a county. Using one radius for
 * everything would either drown the user in irrelevant alarms or stay silent
 * when it matters.
 */
export const REACH_KM: Record<Kind, number> = {
  earthquake: 0, // computed from magnitude, see reachOf
  tsunami: 600, // a tsunami travels along an entire coastline
  volcano: 150, // ashfall and pyroclastic reach
  cyclone: 500, // wind field of a mature storm
  flood: 100,
  storm: 120,
  wildfire: 80,
  heat: 200, // heat waves are regional by nature
  drought: 300,
  other: 100,
}

/** Minimum severity worth waking someone for, per hazard.
 *
 * A tsunami advisory is worth an alarm anywhere. A "moderate" heat warning is
 * not -- and an alarm that fires for everything is an alarm people switch off,
 * which is the worst outcome for the one time it matters.
 */
const MIN_SEVERITY: Record<Kind, Severity> = {
  earthquake: 'moderate',
  tsunami: 'moderate',
  volcano: 'severe',
  cyclone: 'severe',
  flood: 'severe',
  storm: 'severe',
  wildfire: 'severe',
  heat: 'extreme',
  drought: 'extreme',
  other: 'severe',
}

const RANK: Record<Severity, number> = { info: 0, minor: 1, moderate: 2, severe: 3, extreme: 4 }

/** An earthquake's felt radius, roughly. Empirical and deliberately generous:
 * missing a quake that was felt is worse than one alarm too many. */
export function reachOf(event: SosEvent): number {
  if (event.kind !== 'earthquake') return REACH_KM[event.kind]
  const mag = event.magnitude ?? 0
  if (mag < 4) return 60
  // grows fast with magnitude, as felt intensity does
  return Math.min(1500, 10 ** (0.55 * mag - 0.6))
}

export interface Alarm {
  event: SosEvent
  distanceKm: number
  reachKm: number
}

/** Does this event concern the watched place, right now? */
export function concerns(
  event: SosEvent,
  lat: number,
  lon: number,
  maxAgeSeconds: number,
  now: number,
): Alarm | null {
  if (event.lat === null || event.lon === null) return null

  const age = (now - Date.parse(event.time)) / 1000
  // An ongoing alert stays relevant for as long as it runs; a past earthquake
  // stops being news. And an event dated in the future is a warning with lead
  // time, which is precisely when we DO want to speak up.
  if (!event.ongoing && age > maxAgeSeconds) return null

  const required = MIN_SEVERITY[event.kind] ?? 'severe'
  // a tsunami flag overrides the scale: it is the one thing worth an alarm
  // whatever the label says
  if (!event.tsunami && RANK[event.severity] < RANK[required]) return null

  const km = distanceKm(lat, lon, event.lat, event.lon)
  const reach = reachOf(event)
  if (km > reach) return null

  return { event, distanceKm: km, reachKm: reach }
}

/** Every event that concerns the place, nearest first. */
export function alarmsFor(
  events: SosEvent[],
  lat: number,
  lon: number,
  maxAgeSeconds: number,
  now: number,
): Alarm[] {
  return events
    .map((event) => concerns(event, lat, lon, maxAgeSeconds, now))
    .filter((a): a is Alarm => a !== null)
    .sort((a, b) => a.distanceKm - b.distanceKm)
}

/** A one-line reason, for the notification body. */
export function alarmText(alarm: Alarm): string {
  const km = Math.round(alarm.distanceKm)
  const mag = alarm.event.magnitude !== null ? `M${alarm.event.magnitude} ` : ''
  return `${mag}${alarm.event.place} -- ${km} km away`
}
