import { useStore } from './store'

/** Lien profond vers un evenement.
 *
 * Sans ca, on ne peut pas dire "regarde CE seisme": partager l'URL renvoyait
 * l'autre sur la page d'accueil, sur un flux qui a deja bouge. Le fragment
 * (`#e/usgs:ci40674530`) suffit -- il ne part pas au serveur, ne casse aucun
 * cache, et survit au rechargement.
 *
 * L'identifiant contient des `:` et parfois des `+` (`bmkg:2026-08-17T18:10:06+00:00`),
 * d'ou l'encodage.
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

/** Synchronise la selection et l'URL, dans les deux sens. Rend la fonction de
 * desabonnement. */
export function syncDeepLink(): () => void {
  // 1. l'URL d'arrivee decide de la selection initiale
  const initial = idFromHash()
  if (initial) useStore.getState().select(initial)

  // 2. le bouton retour du navigateur doit ramener a l'evenement precedent
  const onHashChange = () => {
    const id = idFromHash()
    if (id !== useStore.getState().selected) useStore.getState().select(id)
  }
  window.addEventListener('hashchange', onHashChange)

  // 3. selectionner ecrit l'URL, sans empiler une entree d'historique par clic
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
