import type { SosEvent } from './types'

/** When an event belongs in the "what is happening" order.
 *
 * A warning published BEFORE its onset carries the onset as its `time`: a
 * Meteoalarm "minor" for the day after tomorrow sat at the top of the world
 * feed, above the earthquake of a minute ago, because the feed sorted on
 * `time` and the future is always the most recent. Until it starts, an
 * upcoming warning is ranked by when it reached us (`received_at`, the
 * nearest thing to "issued" the feed has); from its onset on, by its onset.
 * It keeps its "in 2 d" label either way: only its PLACE in the list moves.
 */
export function relevanceTime(event: SosEvent, now: number): number {
  const onset = Date.parse(event.time)
  if (onset <= now) return onset
  const received = Date.parse(event.received_at)
  return Number.isFinite(received) ? Math.min(received, now) : now
}

/** What is happening now first. Ties (same instant) keep the most recent
 * onset first, then the store's order. Returns a NEW array. */
export function byRelevance(events: SosEvent[], now: number): SosEvent[] {
  return events
    .map((event, index) => ({ event, index, at: relevanceTime(event, now) }))
    .sort((a, b) => b.at - a.at || Date.parse(b.event.time) - Date.parse(a.event.time) || a.index - b.index)
    .map((entry) => entry.event)
}
