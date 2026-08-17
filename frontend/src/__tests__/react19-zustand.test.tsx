/** Le piege React 19 / Zustand qui a mis le projet a genoux: un selecteur qui
 * construit un nouveau tableau a chaque appel fait boucler
 * `useSyncExternalStore` et le composant ne monte JAMAIS -- page blanche,
 * build vert, aucune erreur visible dans l'UI.
 *
 * Ces tests protegent les deux faces de la regle:
 * 1. le motif correct (tranches stables + `useMemo`, comme dans App.tsx) monte;
 * 2. le motif fautif (filterEvents directement dans le selecteur) explose de
 *    facon detectable en test, au lieu de passer inapercu.
 */
import { useMemo } from 'react'
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { filterEvents, useStore } from '../store'
import { NOW, makeEvent, minutesAgo, resetStore } from './helpers'

/** Le motif correct, calque sur App.tsx: selecteurs sur des tranches stables
 * (`s.events`, `s.filters`), derivation memoisee cote composant. */
function GoodFeedCount() {
  const events = useStore((s) => s.events)
  const filters = useStore((s) => s.filters)
  const visible = useMemo(() => filterEvents(events, filters, NOW), [events, filters])
  return <div data-testid="count">{visible.length}</div>
}

/** Le motif fautif: le selecteur renvoie un tableau NEUF a chaque appel. */
function FaultyFeedCount() {
  const visible = useStore((s) => filterEvents(s.events, s.filters, NOW))
  return <div data-testid="count">{visible.length}</div>
}

beforeEach(() => {
  resetStore()
  useStore.setState({
    events: [
      makeEvent({ id: 'a', time: minutesAgo(2) }),
      makeEvent({ id: 'b', time: minutesAgo(8) }),
    ],
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('derivations Zustand sous React 19', () => {
  it('un composant qui derive via useMemo sur des tranches stables monte et rend', () => {
    render(<GoodFeedCount />)
    expect(screen.getByTestId('count')).toHaveTextContent('2')
  })

  it('un selecteur qui fabrique un nouveau tableau a chaque appel ne monte jamais', () => {
    // React detecte le snapshot instable et jette (boucle infinie coupee par
    // le garde-fou "Maximum update depth" ou l'erreur getSnapshot). C'est
    // exactement le symptome page-blanche: le rendu n'aboutit pas.
    // On coupe console.error: React log abondamment avant de jeter, et ce
    // bruit attendu noierait la sortie du test.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<FaultyFeedCount />)).toThrow(
      /getSnapshot|Maximum update depth/i,
    )
  })
})
