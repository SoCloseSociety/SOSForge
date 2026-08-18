/** The wave fronts are the only element of the interface that asserts
 * something PHYSICAL. A circle with the wrong radius would be false
 * information presented as a measurement. */
import { describe, expect, it } from 'vitest'
import { P_SPEED_KM_S, S_SPEED_KM_S, isWaveCandidate, waveFronts, waveRing } from '../waves'
import { NOW, makeEvent, minutesAgo } from './helpers'

/** Great-circle distance, to check the radii independently of the code under test. */
function haversineKm(a: [number, number], b: [number, number]): number {
  const R = 6371
  const dLat = ((b[1] - a[1]) * Math.PI) / 180
  const dLon = ((b[0] - a[0]) * Math.PI) / 180
  const p1 = (a[1] * Math.PI) / 180
  const p2 = (b[1] * Math.PI) / 180
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

describe('front geometry', () => {
  it('every point of the circle sits at the requested distance', () => {
    const ring = waveRing(35, 139, 300)
    for (const point of ring) {
      expect(haversineKm([139, 35], point)).toBeCloseTo(300, 0)
    }
  })

  it('the circle is closed', () => {
    const ring = waveRing(-8, 121, 150)
    expect(ring[0][0]).toBeCloseTo(ring[ring.length - 1][0], 6)
    expect(ring[0][1]).toBeCloseTo(ring[ring.length - 1][1], 6)
  })

  it('longitudes stay within [-180, 180] when crossing the antimeridian', () => {
    // epicentre just west of the date line
    for (const point of waveRing(0, 179.5, 400)) {
      expect(point[0]).toBeGreaterThanOrEqual(-180)
      expect(point[0]).toBeLessThanOrEqual(180)
    }
  })
})

describe('physics', () => {
  it('the P wave always precedes the S wave', () => {
    const quake = makeEvent({ id: 'q', time: minutesAgo(2), magnitude: 6, lat: 35, lon: 139 })
    const fronts = waveFronts([quake], NOW)
    const p = fronts.features.find((f) => f.properties?.phase === 'p')!
    const s = fronts.features.find((f) => f.properties?.phase === 's')!

    const radius = (f: GeoJSON.Feature) =>
      haversineKm([139, 35], (f.geometry as GeoJSON.LineString).coordinates[0] as [number, number])

    expect(radius(p)).toBeGreaterThan(radius(s))
    // 2 minutes: 720 km for P, 420 km for S
    expect(radius(p)).toBeCloseTo(120 * P_SPEED_KM_S, 0)
    expect(radius(s)).toBeCloseTo(120 * S_SPEED_KM_S, 0)
  })

  it('the front fades as it moves away, because it loses its meaning', () => {
    const near = waveFronts(
      [makeEvent({ id: 'a', time: minutesAgo(0.5), magnitude: 6, lat: 0, lon: 0 })],
      NOW,
    )
    const far = waveFronts(
      [makeEvent({ id: 'b', time: minutesAgo(4), magnitude: 6, lat: 0, lon: 0 })],
      NOW,
    )
    expect(near.features[0].properties!.opacity).toBeGreaterThan(
      far.features[0].properties!.opacity,
    )
  })
})

describe('what does not deserve a front', () => {
  it('a quake too weak is felt nowhere', () => {
    const small = makeEvent({ id: 'p', time: minutesAgo(1), magnitude: 2.1, lat: 0, lon: 0 })
    expect(isWaveCandidate(small, NOW)).toBe(false)
    expect(waveFronts([small], NOW).features).toHaveLength(0)
  })

  it('a quake too old: the waves have left the zone where the model holds', () => {
    const old = makeEvent({ id: 'v', time: minutesAgo(30), magnitude: 7, lat: 0, lon: 0 })
    expect(isWaveCandidate(old, NOW)).toBe(false)
  })

  it('an alert is not an earthquake, and neither is a quake without a position', () => {
    const alertEvent = makeEvent({ id: 'a', kind: 'flood', time: minutesAgo(1), magnitude: 6 })
    const noPosition = makeEvent({
      id: 's',
      time: minutesAgo(1),
      magnitude: 6,
      lat: null,
      lon: null,
    })
    expect(isWaveCandidate(alertEvent, NOW)).toBe(false)
    expect(isWaveCandidate(noPosition, NOW)).toBe(false)
  })

  it('a future timestamp does not draw a front backwards', () => {
    const future = makeEvent({ id: 'f', time: minutesAgo(-5), magnitude: 6, lat: 0, lon: 0 })
    expect(isWaveCandidate(future, NOW)).toBe(false)
  })
})
