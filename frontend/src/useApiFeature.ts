import { useEffect, useState } from 'react'
import { serves } from './guidance'

/** The first wait after a version check that got no answer, doubled each
 * time up to RETRY_MAX_MS. */
export const RETRY_FIRST_MS = 5_000
export const RETRY_MAX_MS = 60_000

/** Does the API serve this optional feature (`GET /api/version`)? False
 * until it SAYS so: with `SOS_ENABLE_PLACE=false`, or an older API behind a
 * newer frontend, the place report's button was offered anyway, for a
 * report the API answers with a 404 (`place_disabled`, or no such route).
 * Detect, never assume (guidance.ts).
 *
 * Asked once at mount, so the answer is usually in before the reader picks
 * a place: the button then appears in the same frame as the place, and the
 * map's flight to it already knows what the button covers (MapView
 * `coveredTop`). A late answer brings the button in after that flight
 * started, and the map pads it again then. While `needed`, a check that got
 * no answer (network down, a 5xx) is asked again after a growing wait: one
 * dropped request must not hide the feature for the whole session.
 * /api/version carries no position. */
export function useApiFeature(feature: string, needed: boolean): boolean {
  const [served, setServed] = useState(false)
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let wait = RETRY_FIRST_MS
    const ask = () => {
      void serves(feature).then((answer) => {
        if (!alive) return
        if (answer !== undefined) setServed(answer)
        else if (needed) {
          timer = setTimeout(ask, wait)
          wait = Math.min(wait * 2, RETRY_MAX_MS)
        }
      })
    }
    ask()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [feature, needed])
  return served
}
