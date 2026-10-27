import { useEffect } from 'react'
import { promptAlreadyShown, saveScope, useStore, type LocateState } from './store'

/** How long a position may take before the world view stays. The browser's
 * own `timeout` does not run while its permission prompt is open, so this is
 * our own clock: a prompt left unanswered must not leave the reader in front
 * of "Locating..." forever. A late answer still counts (see below). */
export const LOCATE_TIMEOUT_MS = 8000

type Permission = 'granted' | 'prompt' | 'denied' | 'unknown'

async function geolocationPermission(): Promise<Permission> {
  try {
    const status = await navigator.permissions?.query({ name: 'geolocation' as PermissionName })
    return status ? (status.state as Permission) : 'unknown'
  } catch {
    // Safari before 16 has no Permissions API for geolocation
    return 'unknown'
  }
}

function failure(error: GeolocationPositionError): LocateState {
  if (error.code === error.PERMISSION_DENIED) return 'denied'
  if (error.code === error.TIMEOUT) return 'timeout'
  return 'unavailable'
}

/** Near me by default, mounted once at the top of the app.
 *
 * The owner's rule: show the live feed around the reader when the location
 * is available, the latest live events of the whole world when it is not --
 * and never nag. So:
 *  - the reader chose World: nothing is asked;
 *  - permission already granted: located silently;
 *  - permission not decided yet: asked ONCE, with a one-line reason in the
 *    scope bar, while the world view shows; a prompt dismissed on an earlier
 *    visit is not shown again unless the reader presses "Near me";
 *  - denied, unavailable, insecure page, or no fix within 8 s: the world
 *    view, the reason said in place, and nothing else.
 * A fix that arrives after the 8 s still switches to Near me: the reader
 * answered the prompt late, which is an answer.
 *
 * The position is followed (`watchPosition`, low accuracy) so a reader on
 * the move stays "near"; the store keeps it at two decimals only.
 */
export function useNearMe(): void {
  const scope = useStore((s) => s.scope)
  const request = useStore((s) => s.locateRequest)

  useEffect(() => {
    if (scope !== 'near') return
    const { setHere, setLocate } = useStore.getState()
    let cancelled = false
    let guard: number | undefined
    let watchId: number | undefined

    const giveUp = (state: LocateState) => {
      setLocate(state)
      setHere(null)
    }

    if (!window.isSecureContext) {
      giveUp('insecure')
      return
    }
    if (!navigator.geolocation) {
      giveUp('unavailable')
      return
    }

    const start = (asking: boolean) => {
      setLocate(asking ? 'asking' : 'locating')
      if (asking) saveScope({ asked: true })
      guard = window.setTimeout(() => {
        if (!cancelled && useStore.getState().locate !== 'ok') giveUp('timeout')
      }, LOCATE_TIMEOUT_MS)
      watchId = navigator.geolocation.watchPosition(
        (position) => {
          if (cancelled) return
          window.clearTimeout(guard)
          setHere({ lat: position.coords.latitude, lon: position.coords.longitude })
          setLocate('ok')
        },
        (error) => {
          if (cancelled) return
          window.clearTimeout(guard)
          // a timeout once we already have a position is not a reason to
          // drop it: the last fix stays, the reader keeps their view
          if (error.code === error.TIMEOUT && useStore.getState().locate === 'ok') return
          giveUp(failure(error))
        },
        { enableHighAccuracy: false, maximumAge: 600_000, timeout: LOCATE_TIMEOUT_MS },
      )
    }

    void geolocationPermission().then((answer) => {
      if (cancelled) return
      if (answer === 'denied') return giveUp('denied')
      if (answer === 'granted') return start(false)
      // Not decided yet. A saved position no longer stands for anything.
      setHere(null)
      if (request === 0 && promptAlreadyShown()) {
        setLocate('idle')
        return
      }
      start(true)
    })

    return () => {
      cancelled = true
      window.clearTimeout(guard)
      if (watchId !== undefined) navigator.geolocation.clearWatch(watchId)
    }
  }, [scope, request])
}
