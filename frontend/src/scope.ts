import { distanceKm } from './arrival'
import { SEVERITY_RANK, type Here, type Scope } from './store'
import type { SosEvent } from './types'

/** "Near me" is a region, not a street: a felt earthquake reaches 300 to
 * 1500 km, a cyclone's wind field 500. A thousand kilometres keeps what can
 * touch the reader in view and leaves the rest of the planet to the World
 * view -- and to the permanent "N severe elsewhere" count, so a far tsunami
 * never silently disappears. */
export const NEAR_RADIUS_KM = 1000

/** The map zoom that frames about that radius on a laptop or a phone. */
export const NEAR_ZOOM = 4.6

/** The world view's camera, the map's own starting point. */
export const WORLD_VIEW = { lat: 20, lon: 10, zoom: 1.4 }

/** The world zoom for a map this wide. 1.4 fits a laptop; on a 2560 or a 4K
 * screen it drew the planet two and a half times side by side (measured at
 * 3840x2160), so a wider map gets a closer world, one copy across. */
export function worldZoom(mapWidth: number): number {
  // the world is 512 px wide at zoom 0 and doubles per level
  return Math.max(WORLD_VIEW.zoom, Math.min(3, Math.log2(mapWidth / 512)))
}

/** Near me needs a position. Without one the reader sees the world, and the
 * scope bar says why. */
export function effectiveScope(scope: Scope, here: Here | null): Scope {
  return scope === 'near' && here ? 'near' : 'world'
}

/** A severity worth naming even when it is far: severe, extreme, or a
 * tsunami flag whatever its label. */
export function isSevere(event: SosEvent): boolean {
  return event.tsunami || SEVERITY_RANK[event.severity] >= SEVERITY_RANK.severe
}

/** Splits the visible events around the reader.
 *
 * `near`: within the radius. An event with NO position is not near -- we
 * cannot say it is -- so it leaves the Near me feed; when it is severe it is
 * counted with the far ones, which is what keeps it from vanishing. */
export function splitByScope(
  events: SosEvent[],
  here: Here,
  radiusKm = NEAR_RADIUS_KM,
): { near: SosEvent[]; severeElsewhere: number } {
  const near: SosEvent[] = []
  let severeElsewhere = 0
  for (const event of events) {
    const placed = event.lat !== null && event.lon !== null
    if (placed && distanceKm(here.lat, here.lon, event.lat as number, event.lon as number) <= radiusKm) {
      near.push(event)
    } else if (isSevere(event)) {
      severeElsewhere += 1
    }
  }
  return { near, severeElsewhere }
}
