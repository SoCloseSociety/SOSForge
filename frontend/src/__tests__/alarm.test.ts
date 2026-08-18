/** Proximity alarms decide whether to interrupt someone. Both failure modes are
 * bad: staying silent when it matters, and crying wolf until they turn it off. */
import { describe, expect, it } from 'vitest'
import { alarmsFor, concerns, reachOf } from '../alarm'
import { NOW, makeEvent, minutesAgo } from './helpers'

const PARIS: [number, number] = [48.8566, 2.3522]
const HOUR = 3600

describe('reach by hazard', () => {
  it('an earthquake reaches further as it gets bigger', () => {
    const small = makeEvent({ id: 'a', magnitude: 3, kind: 'earthquake' })
    const big = makeEvent({ id: 'b', magnitude: 7, kind: 'earthquake' })
    expect(reachOf(big)).toBeGreaterThan(reachOf(small) * 5)
  })

  it('a tsunami concerns a whole coastline, a wildfire does not', () => {
    expect(reachOf(makeEvent({ id: 't', kind: 'tsunami' }))).toBeGreaterThan(
      reachOf(makeEvent({ id: 'w', kind: 'wildfire' })) * 5,
    )
  })
})

describe('what deserves to interrupt someone', () => {
  it('a strong nearby earthquake does', () => {
    const near = makeEvent({
      id: 'q',
      kind: 'earthquake',
      magnitude: 6,
      severity: 'severe',
      time: minutesAgo(2),
      lat: 49.2,
      lon: 2.6,
    })
    expect(concerns(near, PARIS[0], PARIS[1], HOUR, NOW)).not.toBeNull()
  })

  it('the same earthquake on the other side of the world does not', () => {
    const far = makeEvent({
      id: 'q',
      kind: 'earthquake',
      magnitude: 6,
      severity: 'severe',
      time: minutesAgo(2),
      lat: -33,
      lon: -71,
    })
    expect(concerns(far, PARIS[0], PARIS[1], HOUR, NOW)).toBeNull()
  })

  it('a moderate heat warning next door does not: that is how people mute alarms', () => {
    const heat = makeEvent({
      id: 'h',
      kind: 'heat',
      severity: 'moderate',
      time: minutesAgo(2),
      lat: 48.9,
      lon: 2.4,
    })
    expect(concerns(heat, PARIS[0], PARIS[1], HOUR, NOW)).toBeNull()
  })

  it('a tsunami flag overrides the severity scale', () => {
    const bulletin = makeEvent({
      id: 't',
      kind: 'tsunami',
      severity: 'info',
      tsunami: true,
      time: minutesAgo(2),
      lat: 49,
      lon: 2.5,
    })
    expect(concerns(bulletin, PARIS[0], PARIS[1], HOUR, NOW)).not.toBeNull()
  })

  it('an ongoing alert keeps mattering, a past earthquake stops', () => {
    const oldQuake = makeEvent({
      id: 'q',
      kind: 'earthquake',
      magnitude: 6,
      severity: 'severe',
      time: minutesAgo(300),
      lat: 49,
      lon: 2.5,
    })
    const runningAlert = makeEvent({
      id: 'f',
      kind: 'flood',
      severity: 'severe',
      ongoing: true,
      time: minutesAgo(300),
      lat: 49,
      lon: 2.5,
    })
    expect(concerns(oldQuake, PARIS[0], PARIS[1], HOUR, NOW)).toBeNull()
    expect(concerns(runningAlert, PARIS[0], PARIS[1], HOUR, NOW)).not.toBeNull()
  })

  it('a warning that has not started yet still speaks up: lead time is the point', () => {
    const upcoming = makeEvent({
      id: 'w',
      kind: 'storm',
      severity: 'severe',
      ongoing: true,
      time: minutesAgo(-120), // starts in two hours
      lat: 49,
      lon: 2.5,
    })
    expect(concerns(upcoming, PARIS[0], PARIS[1], HOUR, NOW)).not.toBeNull()
  })

  it('an event without a position cannot be placed, so it never alarms', () => {
    const noWhere = makeEvent({
      id: 'x',
      kind: 'flood',
      severity: 'extreme',
      time: minutesAgo(1),
      lat: null,
      lon: null,
    })
    expect(concerns(noWhere, PARIS[0], PARIS[1], HOUR, NOW)).toBeNull()
  })
})

describe('ordering', () => {
  it('the nearest concern comes first', () => {
    const near = makeEvent({
      id: 'near', kind: 'earthquake', magnitude: 6, severity: 'severe',
      time: minutesAgo(1), lat: 48.9, lon: 2.4,
    })
    const further = makeEvent({
      id: 'further', kind: 'earthquake', magnitude: 6, severity: 'severe',
      time: minutesAgo(1), lat: 50.5, lon: 3.5,
    })
    const alarms = alarmsFor([further, near], PARIS[0], PARIS[1], HOUR, NOW)
    expect(alarms.map((a) => a.event.id)).toEqual(['near', 'further'])
  })
})
