/** Wave arrival is the only real anticipation this product offers for an
 * earthquake. A wrong countdown would be worse than none: it would tell someone
 * they have time when they do not. */
import { describe, expect, it } from 'vitest'
import { arrivalAt, distanceKm, nextArrival } from '../arrival'
import { P_SPEED_KM_S, S_SPEED_KM_S } from '../waves'
import { NOW, makeEvent, minutesAgo } from './helpers'

const TOKYO: [number, number] = [35.68, 139.77]

describe('distance', () => {
  it('matches a known great-circle distance', () => {
    // Paris -> Lyon, about 392 km
    expect(distanceKm(48.8566, 2.3522, 45.764, 4.8357)).toBeGreaterThan(380)
    expect(distanceKm(48.8566, 2.3522, 45.764, 4.8357)).toBeLessThan(400)
  })
})

describe('countdown', () => {
  it('the S wave always arrives after the P wave', () => {
    const quake = makeEvent({ id: 'q', time: minutesAgo(0.2), magnitude: 6, lat: 36.5, lon: 140.5 })
    const arrival = arrivalAt(quake, TOKYO[0], TOKYO[1], NOW)!
    expect(arrival).not.toBeNull()
    expect(arrival.sIn).toBeGreaterThan(arrival.pIn)
  })

  it('the countdown is distance over speed, minus elapsed time', () => {
    const quake = makeEvent({ id: 'q', time: minutesAgo(1), magnitude: 6, lat: 36.5, lon: 140.5 })
    const arrival = arrivalAt(quake, TOKYO[0], TOKYO[1], NOW)!
    expect(arrival.pIn).toBeCloseTo(arrival.distanceKm / P_SPEED_KM_S - 60, 1)
    expect(arrival.sIn).toBeCloseTo(arrival.distanceKm / S_SPEED_KM_S - 60, 1)
  })

  it('goes negative once the wave has passed, and is then not offered', () => {
    const old = makeEvent({ id: 'q', time: minutesAgo(20), magnitude: 6, lat: 36.5, lon: 140.5 })
    expect(arrivalAt(old, TOKYO[0], TOKYO[1], NOW)!.sIn).toBeLessThan(0)
    expect(nextArrival([old], TOKYO[0], TOKYO[1], NOW)).toBeNull()
  })

  it('picks the soonest inbound wave when several are travelling', () => {
    const near = makeEvent({ id: 'near', time: minutesAgo(0.1), magnitude: 6, lat: 36, lon: 140 })
    const far = makeEvent({ id: 'far', time: minutesAgo(0.1), magnitude: 7, lat: 45, lon: 143 })
    expect(nextArrival([far, near], TOKYO[0], TOKYO[1], NOW)!.event.id).toBe('near')
  })
})

describe('what it refuses to claim', () => {
  it('says nothing about an earthquake too small to be felt at a distance', () => {
    const small = makeEvent({ id: 's', time: minutesAgo(0.1), magnitude: 2.5, lat: 36, lon: 140 })
    expect(arrivalAt(small, TOKYO[0], TOKYO[1], NOW)).toBeNull()
  })

  it('says nothing beyond the distance where constant speed stops meaning anything', () => {
    // Chile, roughly 17000 km from Tokyo
    const faraway = makeEvent({ id: 'f', time: minutesAgo(0.1), magnitude: 7, lat: -33, lon: -71 })
    expect(arrivalAt(faraway, TOKYO[0], TOKYO[1], NOW)).toBeNull()
  })

  it('says nothing for a non-earthquake or an event without a position', () => {
    const flood = makeEvent({ id: 'a', kind: 'flood', time: minutesAgo(0.1), magnitude: 6 })
    const nowhere = makeEvent({ id: 'b', time: minutesAgo(0.1), magnitude: 6, lat: null, lon: null })
    expect(arrivalAt(flood, TOKYO[0], TOKYO[1], NOW)).toBeNull()
    expect(arrivalAt(nowhere, TOKYO[0], TOKYO[1], NOW)).toBeNull()
  })

  it('never counts down from a future-dated event', () => {
    const future = makeEvent({ id: 'f', time: minutesAgo(-5), magnitude: 6, lat: 36, lon: 140 })
    expect(arrivalAt(future, TOKYO[0], TOKYO[1], NOW)).toBeNull()
  })
})
