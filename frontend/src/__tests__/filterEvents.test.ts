/** `filterEvents` est la fonction qui decide de ce que l'utilisateur voit.
 * Chaque test protege une regle produit, pas une ligne de code. */
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

describe('fenetre temporelle', () => {
  it('Direct (15 min) ne montre que ce qui vient de tomber', () => {
    const inside = makeEvent({ id: 'a', time: minutesAgo(10) })
    const outside = makeEvent({ id: 'b', time: minutesAgo(20) })
    const result = filterEvents([inside, outside], makeFilters({ windowMinutes: 15 }), NOW)
    expect(result.map((e) => e.id)).toEqual(['a'])
  })

  it.each([
    { label: '1 h', minutes: 60 },
    { label: '6 h', minutes: 360 },
    { label: '24 h', minutes: 1440 },
  ])('$label garde un evenement juste dedans et ecarte un juste dehors', ({ minutes }) => {
    const inside = makeEvent({ id: 'in', time: minutesAgo(minutes - 1) })
    const outside = makeEvent({ id: 'out', time: minutesAgo(minutes + 1) })
    const result = filterEvents([inside, outside], makeFilters({ windowMinutes: minutes }), NOW)
    expect(result.map((e) => e.id)).toEqual(['in'])
  })

  it('Tout (0) montre tout l historique, meme vieux de plusieurs mois', () => {
    const ancient = makeEvent({ id: 'old', time: minutesAgo(60 * 24 * 200) })
    const result = filterEvents([ancient], makeFilters({ windowMinutes: 0 }), NOW)
    expect(result).toHaveLength(1)
  })
})

describe('filtre par type', () => {
  it('un type decoche disparait du flux, les autres restent', () => {
    const quake = makeEvent({ id: 'q', kind: 'earthquake' })
    const flood = makeEvent({ id: 'f', kind: 'flood', magnitude: null, mag_type: null })
    const kinds = new Set(ALL_KINDS)
    kinds.delete('flood')
    const result = filterEvents([quake, flood], makeFilters({ kinds }), NOW)
    expect(result.map((e) => e.id)).toEqual(['q'])
  })
})

describe('curseur de magnitude', () => {
  it('ecarte les seismes sous le seuil et garde ceux au-dessus', () => {
    const small = makeEvent({ id: 'small', magnitude: 4.2 })
    const big = makeEvent({ id: 'big', magnitude: 6.1 })
    const result = filterEvents([small, big], makeFilters({ minMagnitude: 5 }), NOW)
    expect(result.map((e) => e.id)).toEqual(['big'])
  })

  it('ne filtre QUE les seismes: une alerte sans magnitude ne disparait jamais a cause du curseur', () => {
    // La regle qui a failli casser: une inondation ou un volcan n'a pas de
    // magnitude. Le curseur mesure une dimension que ces evenements n'ont pas,
    // il ne doit donc jamais les toucher.
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

  // Constat (non corrige ici, voir le rapport): un SEISME dont la magnitude est
  // null est traite comme magnitude 0 (`event.magnitude ?? 0`) et disparait des
  // que le curseur depasse zero. C'est un choix discutable mais present dans le
  // code: ce commentaire le documente sans le graver comme une regle.
})


describe('recherche texte', () => {
  it('filtre sur le lieu, le titre et le pays', () => {
    const tokyo = makeEvent({ id: 'a', place: 'Tokyo Bay', country: 'Japan' })
    const chili = makeEvent({ id: 'b', place: 'Offshore Coquimbo', country: 'Chile' })

    const byPlace = filterEvents([tokyo, chili], makeFilters({ query: 'tokyo' }), NOW)
    expect(byPlace.map((e) => e.id)).toEqual(['a'])

    const byCountry = filterEvents([tokyo, chili], makeFilters({ query: 'chile' }), NOW)
    expect(byCountry.map((e) => e.id)).toEqual(['b'])
  })

  it('ignore la casse et les espaces autour', () => {
    const event = makeEvent({ id: 'a', place: 'FLORES REGION, INDONESIA' })
    expect(filterEvents([event], makeFilters({ query: '  flores  ' }), NOW)).toHaveLength(1)
  })

  it('une recherche vide ne filtre rien', () => {
    const events = [makeEvent({ id: 'a' }), makeEvent({ id: 'b' })]
    expect(filterEvents(events, makeFilters({ query: '   ' }), NOW)).toHaveLength(2)
  })
})
