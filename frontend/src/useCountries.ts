import { useEffect, useMemo, useState } from 'react'
import { countriesAt, countriesNow } from './countries'

/** The countries of the point the feed is around (see countries.ts), or
 * null while they are not known: the table still loading, or failed to.
 * Null is not "no country" -- the feed says it could not match the alerts
 * without an area rather than claiming none concerns the reader.
 *
 * Once the table is loaded the answer is SYNCHRONOUS: a reader on a train
 * crossing a 1 km step used to get null for a frame, and their country's
 * warnings flickered out of the feed every kilometre (final review of
 * PR #5).
 *
 * `known` is a country already given for the point -- the geocoder's own
 * `country_code` for a searched place. It always counts: Nominatim puts
 * "Indonesia" or "Philippines" at sea, where the table finds no land. */
export function useCountries(
  center: { lat: number; lon: number } | null,
  known: string | null = null,
): string[] | null {
  const key = center ? `${center.lat.toFixed(2)},${center.lon.toFixed(2)}` : null
  const [loaded, setLoaded] = useState(() => (key ? countriesNow(0, 0) !== undefined : false))

  useEffect(() => {
    if (!key || loaded) return
    let alive = true
    countriesAt(0, 0).then(
      () => alive && setLoaded(true),
      () => undefined, // stays unknown; retried when the point changes
    )
    return () => {
      alive = false
    }
  }, [key, loaded])

  return useMemo(() => {
    if (!key) return null
    const [lat, lon] = key.split(',').map(Number)
    const found = loaded ? (countriesNow(lat, lon) ?? null) : null
    const code = known?.toUpperCase() ?? null
    if (!code) return found
    return found ? [...new Set([...found, code])].sort() : [code]
  }, [key, loaded, known])
}
