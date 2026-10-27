import { useEffect, useMemo } from 'react'
import { ALARM_MAX_AGE_S, alarmText, alarmsFor, type Alarm } from './alarm'
import { eventUrl } from './deeplink'
import { kindLabel } from './format'
import { closeNotification, showNotification } from './notify'
import { SEVERITY_RANK, playAlert, useStore } from './store'
import type { SosEvent } from './types'

const NONE: Alarm[] = []

/** The rank an alarm is remembered at: the severity, the macOS agent's rule. */
function rankOf(event: SosEvent): number {
  return SEVERITY_RANK[event.severity]
}

/** Tags already taken off the tray, so a purge is not closed again on every
 * message. Cleared when the event rings again. */
const closedTags = new Set<string>()

/** Test seam. */
export function resetClosedTags(): void {
  closedTags.clear()
}

/** The watch ALARM, mounted once at the top of the app.
 *
 * It used to live inside the watch panel, which on a phone sits in the filter
 * sheet -- collapsed by default. With the sheet closed the panel was not
 * mounted, so an M6 forty kilometres away rang nothing at all on the device
 * people carry. And each opening of the sheet remounted the panel with an
 * empty memory, so the whole last hour rang again. The alarm now runs
 * whatever is on screen, and what it already rang for lives in the store
 * (`alarmed`, persisted), not in a component.
 *
 * Returns the current alarms so the caller can show them; it never derives
 * them inside a `useStore` selector (lesson 1).
 */
export function useWatchAlarms(events: SosEvent[], now: number): Alarm[] {
  const watch = useStore((s) => s.watch)
  const soundOn = useStore((s) => s.soundOn)
  const t = useStore((s) => s.t)

  const alarms = useMemo(
    () =>
      watch ? alarmsFor(events, watch.lat, watch.lon, ALARM_MAX_AGE_S, now, watch.kinds) : NONE,
    [events, watch, now],
  )

  useEffect(() => {
    if (!watch || alarms.length === 0) return
    const { alarmed, markAlarmed } = useStore.getState()
    // An event rings when it is new, or when it ESCALATED past the highest
    // rank already rung. A rank unknown (an old saved memory) is recorded
    // without ringing: it rang before, at a rank we did not keep.
    const silent: { id: string; rank: number }[] = []
    const fresh = alarms.filter((alarm) => {
      const rank = rankOf(alarm.event)
      const before = alarmed.get(alarm.event.id)
      if (before === undefined) return true
      if (before === null) {
        silent.push({ id: alarm.event.id, rank })
        return false
      }
      return rank > before
    })
    if (silent.length) markAlarmed(silent)
    if (fresh.length === 0) return
    // remembered BEFORE anything is shown: a notification that throws must
    // not leave the event free to ring again on the next second
    markAlarmed(fresh.map((alarm) => ({ id: alarm.event.id, rank: rankOf(alarm.event) })))
    for (const alarm of fresh) closedTags.delete(alarm.event.id)

    if (soundOn) {
      // one sound for the batch, at the pitch of the worst: five beeps for
      // five alerts read as five separate emergencies
      const worst = fresh.reduce((a, b) =>
        SEVERITY_RANK[b.event.severity] > SEVERITY_RANK[a.event.severity] ? b : a,
      )
      playAlert(worst.event.severity)
    }
    for (const alarm of fresh) {
      void showNotification(`${kindLabel(t, alarm.event.kind)} -- ${watch.name}`, {
        body: alarmText(alarm),
        tag: alarm.event.id,
        url: eventUrl(alarm.event.id),
      })
    }
  }, [alarms, watch, soundOn, t])

  // What rang and no longer concerns the place leaves the tray: purged
  // (cancelled, lifted, expired), or moved out of reach, or downgraded below
  // what alarms. NOT when it merely ages: an hour-old quake's notification
  // may be the one the reader has not seen yet. Judged only on a complete
  // picture -- before the snapshot is whole, "not in the list" means "not
  // received yet", and closing then would empty the tray at every reload.
  const complete = useStore((s) => s.received && s.snapshotRemaining === 0)
  useEffect(() => {
    if (!watch || !complete) return
    const reach = new Set(
      alarmsFor(events, watch.lat, watch.lon, Number.POSITIVE_INFINITY, Date.now(), watch.kinds).map(
        (alarm) => alarm.event.id,
      ),
    )
    for (const id of useStore.getState().alarmed.keys()) {
      if (reach.has(id) || closedTags.has(id)) continue
      closedTags.add(id)
      void closeNotification(id)
    }
  }, [events, watch, complete])

  return alarms
}
