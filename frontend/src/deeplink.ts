import { useStore } from './store'

/** Deep link to an event.
 *
 * Without this, there's no way to say "look at THIS earthquake": sharing the
 * URL would send the other person to the home page, to a feed that has
 * already moved on. The fragment (`#e/usgs:ci40674530`) is enough -- it
 * never goes to the server, doesn't break any cache, and survives a reload.
 *
 * The id contains `:` and sometimes `+` (`bmkg:2026-08-17T18:10:06+00:00`),
 * hence the encoding.
 */
const PREFIX = '#e/'

export function eventUrl(eventId: string): string {
  return `${location.origin}${location.pathname}${PREFIX}${encodeURIComponent(eventId)}`
}

function idFromHash(): string | null {
  if (!location.hash.startsWith(PREFIX)) return null
  try {
    return decodeURIComponent(location.hash.slice(PREFIX.length)) || null
  } catch {
    return null
  }
}

/** Syncs the selection and the URL, both ways. Returns the unsubscribe
 * function. */
export function syncDeepLink(): () => void {
  // 1. the URL on arrival decides the initial selection
  const initial = idFromHash()
  if (initial) useStore.getState().select(initial)

  // 2. the browser's back button must bring back the previous event
  const onHashChange = () => {
    const id = idFromHash()
    if (id !== useStore.getState().selected) useStore.getState().select(id)
  }
  window.addEventListener('hashchange', onHashChange)

  // 3. selecting writes the URL, without stacking a history entry per click
  const unsubscribe = useStore.subscribe((state, previous) => {
    if (state.selected === previous.selected) return
    const target = state.selected ? PREFIX + encodeURIComponent(state.selected) : ' '
    if (location.hash !== target) {
      history.replaceState(null, '', state.selected ? target : location.pathname)
    }
  })

  return () => {
    window.removeEventListener('hashchange', onHashChange)
    unsubscribe()
  }
}
