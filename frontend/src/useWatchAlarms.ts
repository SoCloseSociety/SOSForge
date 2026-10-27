import { useEffect, useMemo } from 'react'
import { ALARM_MAX_AGE_S, alarmText, alarmsFor, type Alarm } from './alarm'
import { eventUrl } from './deeplink'
import { kindLabel } from './format'
import { closeNotification, showNotification } from './notify'
import { SEVERITY_RANK, playAlert, useStore } from './store'
import type { SosEvent } from './types'

const NONE: Alarm[] = []

/** The rank an alarm is remembered at: the severity, the macOS agent's
 * rule, plus half a step for the tsunami flag. A quake that rang at EXTREME
 * and then gains its tsunami flag (a BMKG update setting Potensi) stays
 * EXTREME, and only the flag says "go to high ground": the server counts that
 * change as an escalation (pipeline.py), so does this (review of PR #5).
 * Half a step keeps every rank saved before it comparable. */
export function rankOf(event: SosEvent): number {
  return SEVERITY_RANK[event.severity] + (event.tsunami ? 0.5 : 0)
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
        // an escalation replaces the notification already in the tray: it
        // must alert again, not be swapped in silently
        renotify: true,
      })
    }
  }, [alarms, watch, soundOn, t])

  // What rang and no longer concerns the place leaves the tray: purged
  // (cancelled, lifted, expired), or moved out of reach, or downgraded below
  // what alarms. NOT when it merely ages: an hour-old quake's notification
  // may be the one the reader has not seen yet. Judged only on a complete
  // picture -- before the snapshot is whole, "not in the list" means "not
  // received yet", and closing then would empty the tray at every reload.
  // And it is FORGOTTEN with its notification: back in reach (a revision
  // M6.0 -> M5.9 -> M6.0, a kind unticked and ticked again) it rings again,
  // instead of staying silent with nothing left in the tray (review of PR #5).
  const complete = useStore((s) => s.received && s.snapshotRemaining === 0)
  useEffect(() => {
    if (!watch || !complete) return
    const reach = new Set(
      alarmsFor(events, watch.lat, watch.lon, Number.POSITIVE_INFINITY, Date.now(), watch.kinds).map(
        (alarm) => alarm.event.id,
      ),
    )
    const gone = [...useStore.getState().alarmed.keys()].filter((id) => !reach.has(id))
    for (const id of gone) void closeNotification(id)
    useStore.getState().forgetAlarmed(gone)
  }, [events, watch, complete])

  return alarms
}
