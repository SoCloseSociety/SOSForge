import { useEffect, useRef, useState } from 'react'
import { useStore, playAlert } from '../store'
import { alarmText, alarmsFor } from '../alarm'
import { SEVERITY_META, kindLabel } from '../format'
import type { SosEvent } from '../types'

/** How far back an event can be and still be worth an alarm. Beyond an hour,
 * you already know: you felt it, or it is not about you. */
const ALARM_MAX_AGE_S = 3600

/** Location watch: alerts that concern where you actually are.
 *
 * Three things happen here, and all three are opt-in:
 * - the browser asks for the location, and it never leaves the device (every
 *   distance is computed in this page; the server is never told where anyone is);
 * - anything that concerns that place raises an alarm -- sound, and a browser
 *   notification if the user granted one, so it reaches them even in another tab;
 * - the same event never alarms twice.
 *
 * The anticipation sensors need no special wiring: early warnings, swarms and
 * warnings published ahead of their onset all travel the same feed, so they
 * raise the same alarm as anything else.
 */
export function WatchPanel({ events, now }: { events: SosEvent[]; now: number }) {
  const watch = useStore((s) => s.watch)
  const setWatch = useStore((s) => s.setWatch)
  const soundOn = useStore((s) => s.soundOn)
  const select = useStore((s) => s.select)
  const t = useStore((s) => s.t)

  const [locating, setLocating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notifyOn, setNotifyOn] = useState(
    () => typeof Notification !== 'undefined' && Notification.permission === 'granted',
  )
  // ids already alarmed for: an alarm that repeats every second is an alarm
  // people switch off
  const alarmed = useRef<Set<string>>(new Set())

  const alarms = watch ? alarmsFor(events, watch.lat, watch.lon, ALARM_MAX_AGE_S, now) : []

  useEffect(() => {
    if (!watch) return
    for (const alarm of alarms) {
      if (alarmed.current.has(alarm.event.id)) continue
      alarmed.current.add(alarm.event.id)

      if (soundOn) playAlert(alarm.event.severity)
      if (notifyOn && typeof Notification !== 'undefined') {
        try {
          new Notification(
            `${kindLabel(t, alarm.event.kind)} -- ${watch.name}`,
            { body: alarmText(alarm), tag: alarm.event.id },
          )
        } catch {
          /* a refused notification must not break the page */
        }
      }
    }
  }, [alarms, watch, soundOn, notifyOn, t])

  const locate = () => {
    if (!navigator.geolocation) {
      setError(t('watch.unavailable'))
      return
    }
    setLocating(true)
    setError(null)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setWatch({
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          name: t('watch.here'),
        })
        setLocating(false)
      },
      () => {
        // a refusal is a legitimate answer, not an error to shout about
        setError(t('watch.denied'))
        setLocating(false)
      },
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 300000 },
    )
  }

  const askNotifications = async () => {
    if (typeof Notification === 'undefined') return
    const result = await Notification.requestPermission()
    setNotifyOn(result === 'granted')
  }

  return (
    <div className="watch">
      {watch ? (
        <>
          <div className="watch-head">
            <span className="watch-place">
              <span aria-hidden="true">📍</span> {watch.name}
            </span>
            <span className="watch-actions">
              {typeof Notification !== 'undefined' && !notifyOn ? (
                <button type="button" onClick={askNotifications} title={t('watch.notify')}>
                  🔔
                </button>
              ) : null}
              <button type="button" onClick={() => setWatch(null)} aria-label={t('watch.clear')}>
                ✕
              </button>
            </span>
          </div>

          {alarms.length === 0 ? (
            <p className="watch-quiet">{t('watch.quiet')}</p>
          ) : (
            <ul className="watch-alarms">
              {alarms.slice(0, 4).map((alarm) => (
                <li key={alarm.event.id}>
                  <button type="button" onClick={() => select(alarm.event.id)}>
                    <span
                      className="watch-dot"
                      style={{ background: SEVERITY_META[alarm.event.severity].color }}
                    />
                    <span>
                      <strong>{kindLabel(t, alarm.event.kind)}</strong> {alarmText(alarm)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <>
          <button type="button" className="watch-cta" onClick={locate} disabled={locating}>
            <span aria-hidden="true">📍</span>
            {locating ? t('watch.locating') : t('watch.use')}
          </button>
          <p className="watch-note">{t('watch.privacy')}</p>
        </>
      )}
      {error ? <p className="watch-note">{error}</p> : null}
    </div>
  )
}
