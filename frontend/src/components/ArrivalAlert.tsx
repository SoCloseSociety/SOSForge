import { useMemo } from 'react'
import { useStore } from '../store'
import { nextArrival } from '../arrival'
import { SEVERITY_META } from '../format'
import type { SosEvent } from '../types'

/** The countdown of seismic waves reaching the watched place.
 *
 * This is the only screen in the product that says something about the future,
 * and it earns that by arithmetic, not by prediction: the earthquake has
 * already happened, its waves are already travelling, and we know how fast.
 * Everything else here reports the past.
 *
 * It appears only while the S wave is still inbound. Once it has passed there
 * is nothing to warn about, and a counter sitting at zero would be noise on a
 * screen that must stay readable in an emergency.
 */
export function ArrivalAlert({ events, now }: { events: SosEvent[]; now: number }) {
  const watch = useStore((s) => s.watch)
  const t = useStore((s) => s.t)
  const select = useStore((s) => s.select)

  const arrival = useMemo(
    () => (watch ? nextArrival(events, watch.lat, watch.lon, now) : null),
    [events, watch, now],
  )

  if (!watch || !arrival) return null

  const seconds = Math.max(0, Math.round(arrival.sIn))
  const pPassed = arrival.pIn <= 0

  return (
    <button
      type="button"
      className="arrival"
      onClick={() => select(arrival.event.id)}
    >
      {/* The countdown ticks every second. `role="alert"` is implicitly
          atomic, so putting it on this button made a screen reader re-read
          the ENTIRE alert -- "45 s, seismic waves inbound to Tokyo, M6.8,
          120 km away" -- once per second for the whole countdown,
          interrupting itself and everything else. It also overrode the
          button role, so the user was never told it could be activated.

          So: the button stays a button, and a separate one-shot region
          below announces the arrival once. */}
      <span className="arrival-count" aria-hidden="true">
        {seconds}
        <small>s</small>
      </span>
      <span className="arrival-text">
        <strong>
          {t('arrival.title', { place: watch.name })}
        </strong>
        <span>
          {t('arrival.detail', {
            mag: arrival.event.magnitude?.toFixed(1) ?? '?',
            km: Math.round(arrival.distanceKm),
            place: arrival.event.place,
          })}
        </span>
        <span className="arrival-phase" style={{ color: SEVERITY_META.severe.color }}>
          {pPassed ? t('arrival.p.passed') : t('arrival.p.in', { n: Math.round(arrival.pIn) })}
        </span>
      </span>
    </button>
  )
}
