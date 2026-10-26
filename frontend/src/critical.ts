import type { SosEvent } from './types'

/** How far back the banner looks for a point-in-time event (a quake). */
const BANNER_WINDOW_MS = 6 * 3600 * 1000

/** What the top banner announces: tsunamis and extreme events.
 *
 * A point-in-time event ages out after six hours. An ONGOING alert does not:
 * a tsunami advisory issued at dawn is still in force at noon, and dropping it
 * from the banner while it runs said "all clear" when nothing was. Ongoing
 * alerts leave when the server lifts them (a purge), not by the clock.
 */
export function criticalEvents(events: SosEvent[], now: number): SosEvent[] {
  const cutoff = now - BANNER_WINDOW_MS
  return events.filter(
    (e) =>
      (e.ongoing || Date.parse(e.time) > cutoff) && (e.tsunami || e.severity === 'extreme'),
  )
}
