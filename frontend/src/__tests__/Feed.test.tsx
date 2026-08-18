/** Feed: the rendering of one feed row. The rules tested here are the
 * product's -- readability without color, a globe for the open sea, selection. */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { Feed } from '../components/Feed'
import { useStore } from '../store'
import { NOW, makeEvent, minutesAgo, resetStore } from './helpers'

beforeEach(() => {
  resetStore()
  // Language pinned for deterministic labels, whatever browser (jsdom) runs
  // the suite.
  useStore.setState({ lang: 'en' })
})

describe('a feed row', () => {
  it('color never carries the meaning alone: severity label and glyph are present', () => {
    render(
      <Feed
        events={[makeEvent({ id: 'sev-1', severity: 'severe' })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    )
    // The translated label ("Severe") and the glyph ("⚠") accompany the
    // color: the row stays readable with color-deficient vision as well as
    // in black-and-white print.
    expect(screen.getByText('Severe')).toBeInTheDocument()
    expect(screen.getByText('⚠')).toBeInTheDocument()
  })

  it('no identifiable country: a globe, never an approximate flag', () => {
    render(
      <Feed
        events={[makeEvent({ id: 'sea-1', country: null, country_code: null })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    )
    expect(screen.getByText('🌐')).toBeInTheDocument()
  })

  it('with a country code, the country flag replaces the globe', () => {
    render(
      <Feed events={[makeEvent({ id: 'jp-1', country_code: 'JP' })]} now={NOW} emptyKey="filters.empty" />,
    )
    expect(screen.getByText('🇯🇵')).toBeInTheDocument()
    expect(screen.queryByText('🌐')).not.toBeInTheDocument()
  })

  it('a click selects the event, a second click deselects it', () => {
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

  it('the displayed age comes from the provided `now` clock (the server one)', () => {
    render(
      <Feed
        events={[makeEvent({ id: 'age-1', time: minutesAgo(12) })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    )
    expect(screen.getByText('12 min ago')).toBeInTheDocument()
  })

  it('a revision is flagged on the row', () => {
    render(
      <Feed events={[makeEvent({ id: 'rev-1', revision: 2 })]} now={NOW} emptyKey="filters.empty" />,
    )
    expect(screen.getByText('revised')).toBeInTheDocument()
  })
})

describe('empty feed', () => {
  it('shows the message of the provided key rather than a mute panel', () => {
    render(<Feed events={[]} now={NOW} emptyKey="filters.empty.window" />)
    expect(screen.getByText('Nothing in this window. Widen the period.')).toBeInTheDocument()
  })
})
