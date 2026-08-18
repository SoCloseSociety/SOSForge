/** The React 19 / Zustand trap that brought the project to its knees: a
 * selector that builds a new array on every call makes
 * `useSyncExternalStore` loop and the component NEVER mounts -- blank page,
 * green build, no visible error in the UI.
 *
 * These tests protect both sides of the rule:
 * 1. the correct pattern (stable slices + `useMemo`, as in App.tsx) mounts;
 * 2. the faulty pattern (filterEvents directly in the selector) blows up
 *    detectably in a test, instead of slipping through unnoticed.
 */
import { useMemo } from 'react'
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { filterEvents, useStore } from '../store'
import { NOW, makeEvent, minutesAgo, resetStore } from './helpers'

/** The correct pattern, modeled on App.tsx: selectors on stable slices
 * (`s.events`, `s.filters`), memoized derivation on the component side. */
function GoodFeedCount() {
  const events = useStore((s) => s.events)
  const filters = useStore((s) => s.filters)
  const visible = useMemo(() => filterEvents(events, filters, NOW), [events, filters])
  return <div data-testid="count">{visible.length}</div>
}

/** The faulty pattern: the selector returns a FRESH array on every call. */
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

describe('Zustand derivations under React 19', () => {
  it('a component deriving via useMemo on stable slices mounts and renders', () => {
    render(<GoodFeedCount />)
    expect(screen.getByTestId('count')).toHaveTextContent('2')
  })

  it('a selector that builds a new array on every call never mounts', () => {
    // React detects the unstable snapshot and throws (infinite loop cut short
    // by the "Maximum update depth" guard or the getSnapshot error). This is
    // exactly the blank-page symptom: the render never completes.
    // console.error is silenced: React logs profusely before throwing, and
    // that expected noise would drown the test output.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<FaultyFeedCount />)).toThrow(
      /getSnapshot|Maximum update depth/i,
    )
  })
})
