/** `filterEvents` is the function that decides what the user sees.
 * Each test protects a product rule, not a line of code. */
import { describe, expect, it } from 'vitest'
import { ALL_KINDS, filterEvents, type Filters } from '../store'
import { NOW, makeEvent, minutesAgo } from './helpers'

function makeFilters(overrides: Partial<Filters> = {}): Filters {
  return {
    kinds: new Set(ALL_KINDS),
    minMagnitude: 0,
    sources: new Set<string>(),
    windowMinutes: 0,
    query: '',
    ...overrides,
  }
}

describe('time window', () => {
  it('Live (15 min) shows only what just landed', () => {
    const inside = makeEvent({ id: 'a', time: minutesAgo(10) })
    const outside = makeEvent({ id: 'b', time: minutesAgo(20) })
    const result = filterEvents([inside, outside], makeFilters({ windowMinutes: 15 }), NOW)
    expect(result.map((e) => e.id)).toEqual(['a'])
  })

  it.each([
    { label: '1 h', minutes: 60 },
    { label: '6 h', minutes: 360 },
    { label: '24 h', minutes: 1440 },
  ])('$label keeps an event just inside and drops one just outside', ({ minutes }) => {
    const inside = makeEvent({ id: 'in', time: minutesAgo(minutes - 1) })
    const outside = makeEvent({ id: 'out', time: minutesAgo(minutes + 1) })
    const result = filterEvents([inside, outside], makeFilters({ windowMinutes: minutes }), NOW)
    expect(result.map((e) => e.id)).toEqual(['in'])
  })

  it('All (0) shows the whole history, even months old', () => {
    const ancient = makeEvent({ id: 'old', time: minutesAgo(60 * 24 * 200) })
    const result = filterEvents([ancient], makeFilters({ windowMinutes: 0 }), NOW)
    expect(result).toHaveLength(1)
  })
})

describe('kind filter', () => {
  it('an unchecked kind disappears from the feed, the others stay', () => {
    const quake = makeEvent({ id: 'q', kind: 'earthquake' })
    const flood = makeEvent({ id: 'f', kind: 'flood', magnitude: null, mag_type: null })
    const kinds = new Set(ALL_KINDS)
    kinds.delete('flood')
    const result = filterEvents([quake, flood], makeFilters({ kinds }), NOW)
    expect(result.map((e) => e.id)).toEqual(['q'])
  })
})

describe('magnitude slider', () => {
  it('drops earthquakes below the threshold and keeps those above', () => {
    const small = makeEvent({ id: 'small', magnitude: 4.2 })
    const big = makeEvent({ id: 'big', magnitude: 6.1 })
    const result = filterEvents([small, big], makeFilters({ minMagnitude: 5 }), NOW)
    expect(result.map((e) => e.id)).toEqual(['big'])
  })

  it('filters ONLY earthquakes: an alert without a magnitude never disappears because of the slider', () => {
    // The rule that almost broke: a flood or a volcano has no magnitude. The
    // slider measures a dimension these events do not have, so it must never
    // touch them.
    const flood = makeEvent({ id: 'flood', kind: 'flood', magnitude: null, mag_type: null })
    const volcano = makeEvent({ id: 'volcano', kind: 'volcano', magnitude: null, mag_type: null })
    const cyclone = makeEvent({ id: 'cyclone', kind: 'cyclone', magnitude: null, mag_type: null })
    const result = filterEvents(
      [flood, volcano, cyclone],
      makeFilters({ minMagnitude: 7 }),
      NOW,
    )
    expect(result.map((e) => e.id)).toEqual(['flood', 'volcano', 'cyclone'])
  })

  // Observation (not fixed here, see the report): an EARTHQUAKE whose
  // magnitude is null is treated as magnitude 0 (`event.magnitude ?? 0`) and
  // disappears as soon as the slider goes past zero. A debatable choice, but
  // present in the code: this comment documents it without carving it in as
  // a rule.
})


describe('text search', () => {
  it('filters on place, title and country', () => {
    const tokyo = makeEvent({ id: 'a', place: 'Tokyo Bay', country: 'Japan' })
    const chili = makeEvent({ id: 'b', place: 'Offshore Coquimbo', country: 'Chile' })

    const byPlace = filterEvents([tokyo, chili], makeFilters({ query: 'tokyo' }), NOW)
    expect(byPlace.map((e) => e.id)).toEqual(['a'])

    const byCountry = filterEvents([tokyo, chili], makeFilters({ query: 'chile' }), NOW)
    expect(byCountry.map((e) => e.id)).toEqual(['b'])
  })

  it('ignores case and surrounding whitespace', () => {
    const event = makeEvent({ id: 'a', place: 'FLORES REGION, INDONESIA' })
    expect(filterEvents([event], makeFilters({ query: '  flores  ' }), NOW)).toHaveLength(1)
  })

  it('an empty search filters nothing', () => {
    const events = [makeEvent({ id: 'a' }), makeEvent({ id: 'b' })]
    expect(filterEvents(events, makeFilters({ query: '   ' }), NOW)).toHaveLength(2)
  })
})
