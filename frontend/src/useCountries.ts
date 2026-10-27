import { useEffect, useState } from 'react'
import { countriesAt } from './countries'

/** The countries of the point the feed is around (see countries.ts), or
 * null while they are not known: the table still loading, or failed to.
 * Null is not "no country" -- the feed says it could not match the alerts
 * without an area rather than claiming none concerns the reader. */
export function useCountries(center: { lat: number; lon: number } | null): string[] | null {
  const key = center ? `${center.lat.toFixed(2)},${center.lon.toFixed(2)}` : null
  const [answer, setAnswer] = useState<{ key: string; codes: string[] } | null>(null)

  useEffect(() => {
    if (!key) return
    let alive = true
    const [lat, lon] = key.split(',').map(Number)
    countriesAt(lat, lon).then(
      (codes) => alive && setAnswer({ key, codes }),
      () => undefined, // stays unknown; retried when the point changes
    )
    return () => {
      alive = false
    }
  }, [key])

  return key && answer?.key === key ? answer.codes : null
}
