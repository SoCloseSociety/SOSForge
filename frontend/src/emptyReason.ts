import { SEVERITY_RANK, type Filters } from './store'
import type { SosEvent } from './types'

/** Which filter emptied the feed -- so the message can name a fix that works.
 *
 * The old logic asked one question ("is there a window?") and answered a
 * different one. With events present and every kind chip switched off, it told
 * the reader "Nothing in this window. Widen the period." -- an instruction that
 * cannot possibly help, because the window is not what removed anything. On a
 * product someone opens because they felt shaking, being sent to the wrong
 * control is worse than saying nothing.
 *
 * The order below is the order of blame: the narrowest, most deliberate action
 * the reader took is the one to point at.
 */
export function emptyReason(state: {
  events: SosEvent[]
  visible: SosEvent[]
  filters: Pick<Filters, 'query' | 'kinds' | 'minMagnitude' | 'windowMinutes'> &
    Partial<Pick<Filters, 'minSeverity'>>
  /** snapshot events still on their way (chunked snapshot) */
  remaining?: number
  /** has the first snapshot arrived? Absent = yes (older callers) */
  received?: boolean
}): string {
  const { events, filters } = state
  // Before the feed has spoken at all, there is no "no event": only "not
  // connected yet".
  if (state.received === false) return 'feed.waiting'
  // First, before any blame: while the snapshot is still arriving, "no
  // event" would be a statement about events we have not received yet.
  if ((state.remaining ?? 0) > 0) return 'feed.loading'
  if (filters.query.trim()) return 'filters.empty.search'
  if (events.length === 0) return 'filters.empty'
  if (filters.kinds.size === 0) return 'filters.empty.kinds'

  const ofKind = events.filter((e) => filters.kinds.has(e.kind))
  if (ofKind.length === 0) return 'filters.empty.kinds'

  if (filters.minMagnitude > 0) {
    const strongEnough = ofKind.filter((e) => (e.magnitude ?? 0) >= filters.minMagnitude)
    if (strongEnough.length === 0) return 'filters.empty.magnitude'
  }

  // The severity floor (a tsunami flag passes it, as in filterEvents). It
  // used to be blamed on the window: "Widen the period", which cannot help
  // when nothing that grave exists at all (review of PR #5, #14, #15).
  const floor = SEVERITY_RANK[filters.minSeverity ?? 'info']
  if (floor > 0 && !ofKind.some((e) => e.tsunami || SEVERITY_RANK[e.severity] >= floor)) {
    return 'filters.empty.severity'
  }

  if (filters.windowMinutes > 0) return 'filters.empty.window'
  return 'filters.empty'
}
