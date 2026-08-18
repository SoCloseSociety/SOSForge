/** Les fronts d'onde sont le seul element de l'interface qui affirme quelque
 * chose de PHYSIQUE. Un cercle au mauvais rayon serait une information fausse
 * presentee comme une mesure. */
import { describe, expect, it } from 'vitest'
import { P_SPEED_KM_S, S_SPEED_KM_S, isWaveCandidate, waveFronts, waveRing } from '../waves'
import { NOW, makeEvent, minutesAgo } from './helpers'

/** Distance orthodromique, pour verifier les rayons independamment du code teste. */
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

describe('geometrie du front', () => {
  it('tous les points du cercle sont a la distance demandee', () => {
    const ring = waveRing(35, 139, 300)
    for (const point of ring) {
      expect(haversineKm([139, 35], point)).toBeCloseTo(300, 0)
    }
  })

  it('le cercle est ferme', () => {
    const ring = waveRing(-8, 121, 150)
    expect(ring[0][0]).toBeCloseTo(ring[ring.length - 1][0], 6)
    expect(ring[0][1]).toBeCloseTo(ring[ring.length - 1][1], 6)
  })

  it('les longitudes restent dans [-180, 180] en franchissant l antimeridien', () => {
    // epicentre juste a l ouest de la ligne de changement de date
    for (const point of waveRing(0, 179.5, 400)) {
      expect(point[0]).toBeGreaterThanOrEqual(-180)
      expect(point[0]).toBeLessThanOrEqual(180)
    }
  })
})

describe('physique', () => {
  it('l onde P precede toujours l onde S', () => {
    const quake = makeEvent({ id: 'q', time: minutesAgo(2), magnitude: 6, lat: 35, lon: 139 })
    const fronts = waveFronts([quake], NOW)
    const p = fronts.features.find((f) => f.properties?.phase === 'p')!
    const s = fronts.features.find((f) => f.properties?.phase === 's')!

    const rayon = (f: GeoJSON.Feature) =>
      haversineKm([139, 35], (f.geometry as GeoJSON.LineString).coordinates[0] as [number, number])

    expect(rayon(p)).toBeGreaterThan(rayon(s))
    // 2 minutes: 720 km pour P, 420 km pour S
    expect(rayon(p)).toBeCloseTo(120 * P_SPEED_KM_S, 0)
    expect(rayon(s)).toBeCloseTo(120 * S_SPEED_KM_S, 0)
  })

  it('le front s efface en s eloignant, parce qu il perd son sens', () => {
    const proche = waveFronts(
      [makeEvent({ id: 'a', time: minutesAgo(0.5), magnitude: 6, lat: 0, lon: 0 })],
      NOW,
    )
    const loin = waveFronts(
      [makeEvent({ id: 'b', time: minutesAgo(4), magnitude: 6, lat: 0, lon: 0 })],
      NOW,
    )
    expect(proche.features[0].properties!.opacity).toBeGreaterThan(
      loin.features[0].properties!.opacity,
    )
  })
})

describe('ce qui ne merite pas de front', () => {
  it('un seisme trop faible n est ressenti nulle part', () => {
    const petit = makeEvent({ id: 'p', time: minutesAgo(1), magnitude: 2.1, lat: 0, lon: 0 })
    expect(isWaveCandidate(petit, NOW)).toBe(false)
    expect(waveFronts([petit], NOW).features).toHaveLength(0)
  })

  it('un seisme trop ancien: les ondes ont quitte la zone ou le modele tient', () => {
    const vieux = makeEvent({ id: 'v', time: minutesAgo(30), magnitude: 7, lat: 0, lon: 0 })
    expect(isWaveCandidate(vieux, NOW)).toBe(false)
  })

  it('une alerte n est pas un seisme, et un seisme sans position non plus', () => {
    const alerte = makeEvent({ id: 'a', kind: 'flood', time: minutesAgo(1), magnitude: 6 })
    const sansPosition = makeEvent({
      id: 's',
      time: minutesAgo(1),
      magnitude: 6,
      lat: null,
      lon: null,
    })
    expect(isWaveCandidate(alerte, NOW)).toBe(false)
    expect(isWaveCandidate(sansPosition, NOW)).toBe(false)
  })

  it('un horodatage dans le futur ne dessine pas de front a l envers', () => {
    const futur = makeEvent({ id: 'f', time: minutesAgo(-5), magnitude: 6, lat: 0, lon: 0 })
    expect(isWaveCandidate(futur, NOW)).toBe(false)
  })
})
