import type { SosEvent } from './types'

/** Propagation des ondes sismiques, dessinee en direct.
 *
 * Ce que ca montre et qu'aucun autre element de l'interface ne dit: **ou les
 * secousses arrivent MAINTENANT**. Un seisme est un point sur une carte; ce qui
 * se deplace, c'est le front d'onde, et il met des minutes a traverser une
 * region. Voir le cercle S atteindre une ville, c'est voir l'information que le
 * produit existe pour donner.
 *
 * Deux fronts, deux vitesses moyennes dans la croute:
 * - **P** (primaire, compression) ~6,0 km/s -- la premiere secousse, faible;
 * - **S** (secondaire, cisaillement) ~3,5 km/s -- celle qui fait les degats.
 *
 * Ce sont des moyennes crustales, pas un modele de terre: au-dela d'un millier
 * de kilometres les ondes plongent dans le manteau et accelerent. Les cercles
 * sont donc justes pres de l'epicentre, approximatifs loin, et on les arrete
 * avant qu'ils ne deviennent mensongers.
 */

export const P_SPEED_KM_S = 6.0
export const S_SPEED_KM_S = 3.5

/** Au-dela, le modele a vitesse constante ne vaut plus rien. */
const MAX_RADIUS_KM = 1200
/** Un seisme trop faible n'est ressenti nulle part: ne rien dessiner. */
const MIN_MAGNITUDE = 4.0

const EARTH_RADIUS_KM = 6371

/** Point atteint depuis un epicentre en suivant un cap, a une distance donnee. */
function destination(lat: number, lon: number, bearing: number, distanceKm: number): [number, number] {
  const angular = distanceKm / EARTH_RADIUS_KM
  const phi1 = (lat * Math.PI) / 180
  const lambda1 = (lon * Math.PI) / 180
  const theta = (bearing * Math.PI) / 180

  const phi2 = Math.asin(
    Math.sin(phi1) * Math.cos(angular) + Math.cos(phi1) * Math.sin(angular) * Math.cos(theta),
  )
  const lambda2 =
    lambda1 +
    Math.atan2(
      Math.sin(theta) * Math.sin(angular) * Math.cos(phi1),
      Math.cos(angular) - Math.sin(phi1) * Math.sin(phi2),
    )

  return [((lambda2 * 180) / Math.PI + 540) % 360 - 180, (phi2 * 180) / Math.PI]
}

/** Le cercle est trace en coordonnees geographiques, pas en pixels: il reste
 * juste a tous les niveaux de zoom, et se deforme correctement pres des poles. */
export function waveRing(lat: number, lon: number, radiusKm: number, points = 72): [number, number][] {
  const ring: [number, number][] = []
  for (let i = 0; i <= points; i += 1) {
    ring.push(destination(lat, lon, (i * 360) / points, radiusKm))
  }
  return ring
}

export function isWaveCandidate(event: SosEvent, now: number): boolean {
  if (event.kind !== 'earthquake' || event.lat === null || event.lon === null) return false
  if ((event.magnitude ?? 0) < MIN_MAGNITUDE) return false
  const elapsed = (now - Date.parse(event.time)) / 1000
  // le front S doit encore etre dans la zone ou le modele tient
  return elapsed > 0 && elapsed * S_SPEED_KM_S < MAX_RADIUS_KM
}

/** Les deux fronts de chaque seisme assez recent, a l'instant `now`. */
export function waveFronts(events: SosEvent[], now: number): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = []

  for (const event of events) {
    if (!isWaveCandidate(event, now)) continue
    const elapsed = (now - Date.parse(event.time)) / 1000
    const lat = event.lat as number
    const lon = event.lon as number

    for (const [phase, speed] of [
      ['p', P_SPEED_KM_S],
      ['s', S_SPEED_KM_S],
    ] as const) {
      const radius = elapsed * speed
      if (radius <= 0 || radius > MAX_RADIUS_KM) continue
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: waveRing(lat, lon, radius) },
        properties: {
          phase,
          id: event.id,
          // le front s'efface en s'eloignant: il perd son sens avec la distance
          opacity: Math.max(0, 1 - radius / MAX_RADIUS_KM),
        },
      })
    }
  }

  return { type: 'FeatureCollection', features }
}
