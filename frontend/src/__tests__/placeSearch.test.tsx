/** The owner, in production (2026-10-27): typed "koh samui", picked the
 * place -- the watch said "Watching Ko Samui" -- and the feed said "No event
 * matches this search." The text "koh samui" stayed as a filter after the
 * place was chosen, and no event is titled after the island. */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import { makeEvent, makeStats, resetStore } from './helpers'
import type { ServerMessage, SosEvent } from '../types'

vi.mock('../components/MapView', () => ({ MapView: () => null }))

const SAMUI = { name: 'Ko Samui, Surat Thani Province, Thailand', lat: 9.51, lon: 100.01, type: 'island', bbox: [9.4, 9.6, 99.9, 100.1] }
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()

function serve(results: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: url.startsWith('/api/geocode'),
      status: url.startsWith('/api/geocode') ? 200 : 404,
      headers: { get: () => 'application/json' },
      json: async () => (url.startsWith('/api/geocode') ? { results } : {}),
    })),
  )
}

async function pickSamui(events: SosEvent[]) {
  const { default: App } = await import('../App')
  useStore.setState({ scope: 'world' })
  useStore.getState().ingest({
    type: 'snapshot',
    server_time: new Date().toISOString(),
    events,
    stats: makeStats(),
    sources: [],
  } as ServerMessage)
  render(<App />)
  const box = screen.getByRole('combobox', { name: 'Search an area...' })
  fireEvent.change(box, { target: { value: 'koh samui' } })
  // the result list (debounced 450 ms), then Enter picks the first place
  await screen.findByRole('listbox', {}, { timeout: 3000 })
  fireEvent.keyDown(box, { key: 'Enter' })
  await act(async () => {})
  return box as HTMLInputElement
}

beforeEach(() => {
  resetStore()
  localStorage.clear()
  vi.stubGlobal('WebSocket', class { close() {} })
  serve([SAMUI])
})
afterEach(() => vi.unstubAllGlobals())

describe('picking a place from the search', () => {
  it('clears the text filter and scopes the feed to the place, named in the scope bar', async () => {
    const nearby = makeEvent({ id: 'tmd:1', place: 'Gulf of Thailand', lat: 9.0, lon: 99.5, time: ago(30) })
    const far = makeEvent({ id: 'jma:1', place: 'Off Honshu', lat: 38.3, lon: 142.4, time: ago(5), severity: 'severe' })
    const box = await pickSamui([nearby, far])
    expect(box.value).toBe('')
    expect(useStore.getState().filters.query).toBe('')
    expect(screen.queryByText('No event matches this search.')).toBeNull()
    const pill = document.querySelector('.scope-bar .scope-place')
    expect(pill).toHaveAttribute('aria-pressed', 'true')
    expect(pill?.textContent).toContain('Ko Samui')
    expect(pill?.textContent).not.toContain('Thailand')
    const rows = document.querySelectorAll('.item .place')
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain('Gulf of Thailand')
    // the severe quake off Honshu is out of scope, and still counted
    expect(document.querySelector('.scope-elsewhere')?.textContent).toContain('1 severe elsewhere')
  })

  it('with nothing near: says so, and lists the nearest events with their distance', async () => {
    const far = makeEvent({ id: 'jma:1', place: 'Off Honshu', lat: 38.3, lon: 142.4, time: ago(5) })
    const farther = makeEvent({ id: 'ingv:1', place: 'Central Italy', lat: 42.7, lon: 13.2, time: ago(15) })
    await pickSamui([farther, far])
    expect(screen.getByText(/Nothing within 1,000 km of Ko Samui in this window/)).toBeInTheDocument()
    const rows = [...document.querySelectorAll('.item')]
    expect(rows).toHaveLength(2)
    // nearest first, each with its distance
    expect(rows[0].textContent).toContain('Off Honshu')
    expect(rows[0].querySelector('.tag-distance')?.textContent).toMatch(/km away/)
  })

  it('typing without picking a place keeps the instant text filter', async () => {
    const { default: App } = await import('../App')
    useStore.setState({ scope: 'world' })
    useStore.getState().ingest({
      type: 'snapshot',
      server_time: new Date().toISOString(),
      events: [makeEvent({ id: 'a', place: 'Honshu', time: ago(5) }), makeEvent({ id: 'b', place: 'Chile', time: ago(5) })],
      stats: makeStats(),
      sources: [],
    } as ServerMessage)
    render(<App />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Search an area...' }), { target: { value: 'chile' } })
    const rows = document.querySelectorAll('.item .place')
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain('Chile')
  })

  it('the ✕ stops following the place and goes back to Near me', async () => {
    await pickSamui([makeEvent({ id: 'x', lat: 9.0, lon: 99.5, time: ago(5) })])
    fireEvent.click(screen.getByRole('button', { name: 'Stop following Ko Samui' }))
    expect(useStore.getState().place).toBeNull()
    expect(useStore.getState().scope).toBe('near')
  })

  it('a place restored on reload, or picked again after World, flies the map there', async () => {
    const { default: App } = await import('../App')
    useStore.getState().setPlace({ lat: 9.51, lon: 100.01, name: 'Ko Samui' })
    render(<App />)
    expect(useStore.getState().focus).toMatchObject({ lat: 9.51, lon: 100.01 })
    fireEvent.click(screen.getByRole('button', { name: 'World' }))
    expect(useStore.getState().focus).toMatchObject({ name: 'world' })
    fireEvent.click(document.querySelector('.scope-bar .scope-place') as HTMLElement)
    expect(useStore.getState().focus).toMatchObject({ lat: 9.51, lon: 100.01 })
  })
})
