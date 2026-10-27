import type { SosEvent } from './types'

/** How far back the banner looks for a point-in-time event (a quake). */
const BANNER_WINDOW_MS = 6 * 3600 * 1000

/** An `ongoing` alert with no `expires` counts as in force for this long
 * after its last update: EONET's "open" only means "not closed", and fires
 * last observed ten to fourteen days ago were listed in Near me as current
 * (recheck of PR #5). */
export const IN_FORCE_MAX_AGE_MS = 3 * 86_400_000

/** Still in force: ongoing, and either an `expires` still ahead or, without
 * one, updated within IN_FORCE_MAX_AGE_MS. */
export function inForce(e: SosEvent, now: number): boolean {
  if (!e.ongoing) return false
  if (e.expires) return Date.parse(e.expires) > now
  return Date.parse(e.updated_at ?? e.time) >= now - IN_FORCE_MAX_AGE_MS
}

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
