/** Depth was missing from the countdown, and depth is not a detail.
 *
 * An earthquake happens at a HYPOCENTRE, not at the dot drawn on the map.
 * Waves travel the slant distance from that point, so a countdown built on
 * the surface distance alone starts too early. And past a certain depth the
 * waves leave the crust entirely: the constant crustal speed the model uses
 * stops being an approximation and becomes a wrong answer, on the one element
 * of this interface that behaves like a siren.
 */
import { describe, expect, it } from 'vitest'
import { arrivalAt, distanceKm, nextArrival } from '../arrival'
import { P_SPEED_KM_S, S_SPEED_KM_S } from '../waves'
import { NOW, makeEvent, minutesAgo } from './helpers'

const TOKYO: [number, number] = [35.68, 139.77]
const EPICENTRE = { lat: 36.5, lon: 140.5 }

describe('the countdown travels from the hypocentre', () => {
  it('counts the slant distance, not the surface distance', () => {
    const deep = makeEvent({
      id: 'q',
      time: minutesAgo(1),
      magnitude: 6,
      ...EPICENTRE,
      depth_km: 120,
    })
    const arrival = arrivalAt(deep, TOKYO[0], TOKYO[1], NOW)!
    expect(arrival).not.toBeNull()

    const epi = distanceKm(TOKYO[0], TOKYO[1], EPICENTRE.lat, EPICENTRE.lon)
    const hypo = Math.sqrt(epi ** 2 + 120 ** 2)
    expect(arrival.hypocentralKm).toBeCloseTo(hypo, 3)
    expect(arrival.pIn).toBeCloseTo(hypo / P_SPEED_KM_S - 60, 2)
    expect(arrival.sIn).toBeCloseTo(hypo / S_SPEED_KM_S - 60, 2)

    // and that is strictly later than the surface distance would have said
    expect(arrival.sIn).toBeGreaterThan(epi / S_SPEED_KM_S - 60)
  })

  it('still reports the epicentral distance to the reader', () => {
    /* "120 km away" means the distance to the point on the map. The slant
     * distance is the model's business, not the reader's. */
    const deep = makeEvent({ id: 'q', time: minutesAgo(1), magnitude: 6, ...EPICENTRE, depth_km: 120 })
    const arrival = arrivalAt(deep, TOKYO[0], TOKYO[1], NOW)!
    expect(arrival.distanceKm).toBeCloseTo(
      distanceKm(TOKYO[0], TOKYO[1], EPICENTRE.lat, EPICENTRE.lon),
      3,
    )
    expect(arrival.hypocentralKm).toBeGreaterThan(arrival.distanceKm)
  })

  it('treats an event with no reported depth as a surface source', () => {
    const unknown = makeEvent({
      id: 'q',
      time: minutesAgo(1),
      magnitude: 6,
      ...EPICENTRE,
      depth_km: null,
    })
    const arrival = arrivalAt(unknown, TOKYO[0], TOKYO[1], NOW)!
    expect(arrival.hypocentralKm).toBeCloseTo(arrival.distanceKm, 6)
  })
})

describe('what a deep-focus earthquake is not allowed to claim', () => {
  it('says nothing at all below the crust', () => {
    /* A 600 km deep earthquake sends its waves through the mantle, where they
     * travel much faster than the crustal average this model uses. The
     * countdown would run PAST the shaking -- it would say "45 s" to someone
     * who has thirty. Saying nothing is the only honest answer. */
    const deep = makeEvent({
      id: 'q',
      time: minutesAgo(0.2),
      magnitude: 6.5,
      ...EPICENTRE,
      depth_km: 580,
    })
    expect(arrivalAt(deep, TOKYO[0], TOKYO[1], NOW)).toBeNull()
    expect(nextArrival([deep], TOKYO[0], TOKYO[1], NOW)).toBeNull()
  })

  it('keeps the shallow earthquake when a deep one is closer', () => {
    const deep = makeEvent({
      id: 'deep',
      time: minutesAgo(0.1),
      magnitude: 6.5,
      lat: 36,
      lon: 140,
      depth_km: 400,
    })
    const shallow = makeEvent({
      id: 'shallow',
      time: minutesAgo(0.1),
      magnitude: 6,
      ...EPICENTRE,
      depth_km: 10,
    })
    expect(nextArrival([deep, shallow], TOKYO[0], TOKYO[1], NOW)!.event.id).toBe('shallow')
  })
})
