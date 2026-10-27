import { useEffect, useMemo } from 'react'
import { ALARM_MAX_AGE_S, alarmText, alarmsFor, type Alarm } from './alarm'
import { eventUrl } from './deeplink'
import { kindLabel } from './format'
import { showNotification } from './notify'
import { SEVERITY_RANK, playAlert, useStore } from './store'
import type { SosEvent } from './types'

const NONE: Alarm[] = []

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
    const fresh = alarms.filter((alarm) => !alarmed.has(alarm.event.id))
    if (fresh.length === 0) return
    // remembered BEFORE anything is shown: a notification that throws must
    // not leave the event free to ring again on the next second
    markAlarmed(fresh.map((alarm) => alarm.event.id))

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

  return alarms
}
