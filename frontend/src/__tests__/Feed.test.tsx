/** Feed: le rendu d'une ligne du flux. Les regles testees sont celles du
 * produit -- lisibilite sans la couleur, globe pour la haute mer, selection. */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { Feed } from '../components/Feed'
import { useStore } from '../store'
import { NOW, makeEvent, minutesAgo, resetStore } from './helpers'

beforeEach(() => {
  resetStore()
  // Langue fixee pour des libelles deterministes, quel que soit le navigateur
  // (jsdom) qui execute la suite.
  useStore.setState({ lang: 'en' })
})

describe('une ligne du flux', () => {
  it('la couleur ne porte jamais le sens seule: libelle et glyphe de gravite sont presents', () => {
    render(
      <Feed
        events={[makeEvent({ id: 'sev-1', severity: 'severe' })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    )
    // Le libelle traduit ("Severe") et le glyphe ("⚠") accompagnent la couleur:
    // la ligne reste lisible en vision des couleurs deficiente comme en
    // impression noir et blanc.
    expect(screen.getByText('Severe')).toBeInTheDocument()
    expect(screen.getByText('⚠')).toBeInTheDocument()
  })

  it('pas de pays identifiable: un globe, jamais un drapeau approximatif', () => {
    render(
      <Feed
        events={[makeEvent({ id: 'sea-1', country: null, country_code: null })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    )
    expect(screen.getByText('🌐')).toBeInTheDocument()
  })

  it('avec un code pays, le drapeau du pays remplace le globe', () => {
    render(
      <Feed events={[makeEvent({ id: 'jp-1', country_code: 'JP' })]} now={NOW} emptyKey="filters.empty" />,
    )
    expect(screen.getByText('🇯🇵')).toBeInTheDocument()
    expect(screen.queryByText('🌐')).not.toBeInTheDocument()
  })

  it('le clic selectionne l evenement, un second clic le deselectionne', () => {
    render(
      <Feed
        events={[makeEvent({ id: 'click-1', place: 'Off the coast of Honshu' })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    )
    const row = screen.getByRole('button', { name: /Honshu/ })
    fireEvent.click(row)
    expect(useStore.getState().selected).toBe('click-1')
    fireEvent.click(row)
    expect(useStore.getState().selected).toBeNull()
  })

  it('l age affiche vient de l horloge `now` fournie (celle du serveur)', () => {
    render(
      <Feed
        events={[makeEvent({ id: 'age-1', time: minutesAgo(12) })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    )
    expect(screen.getByText('12 min ago')).toBeInTheDocument()
  })

  it('une revision est signalee sur la ligne', () => {
    render(
      <Feed events={[makeEvent({ id: 'rev-1', revision: 2 })]} now={NOW} emptyKey="filters.empty" />,
    )
    expect(screen.getByText('revised')).toBeInTheDocument()
  })
})

describe('flux vide', () => {
  it('affiche le message de la cle fournie plutot qu un panneau muet', () => {
    render(<Feed events={[]} now={NOW} emptyKey="filters.empty.window" />)
    expect(screen.getByText('Nothing in this window. Widen the period.')).toBeInTheDocument()
  })
})
