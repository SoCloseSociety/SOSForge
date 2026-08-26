import type { Filters } from './store'
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
  filters: Pick<Filters, 'query' | 'kinds' | 'minMagnitude' | 'windowMinutes'>
}): string {
  const { events, filters } = state
  if (filters.query.trim()) return 'filters.empty.search'
  if (events.length === 0) return 'filters.empty'
  if (filters.kinds.size === 0) return 'filters.empty.kinds'

  const ofKind = events.filter((e) => filters.kinds.has(e.kind))
  if (ofKind.length === 0) return 'filters.empty.kinds'

  if (filters.minMagnitude > 0) {
    const strongEnough = ofKind.filter((e) => (e.magnitude ?? 0) >= filters.minMagnitude)
    if (strongEnough.length === 0) return 'filters.empty.magnitude'
  }

  if (filters.windowMinutes > 0) return 'filters.empty.window'
  return 'filters.empty'
}
