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
  return events.filter((e) => isCritical(e, now))
}

/** One event, the banner's rule. Also what the scope bar's counts must
 * never leave out: a BMKG tsunami-potential quake is not `ongoing`, and
 * under a 15 min window the bar said "Nothing severe elsewhere" beside the
 * banner's "TSUNAMI ALERT" for six hours (final review of PR #5). */
export function isCritical(e: SosEvent, now: number): boolean {
  return (
    (e.ongoing || Date.parse(e.time) > now - BANNER_WINDOW_MS) &&
    (e.tsunami || e.severity === 'extreme')
  )
}
