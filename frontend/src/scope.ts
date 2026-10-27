import { distanceKm } from './arrival'
import { SEVERITY_RANK, type Here, type Place, type Scope } from './store'
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

/** What the feed shows. A chosen place always has its coordinates; Near me
 * needs a position, and without one the reader sees the world, the scope
 * bar saying why. */
export function effectiveScope(scope: Scope, here: Here | null, place: Place | null = null): Scope {
  if (scope === 'place' && place) return 'place'
  return scope === 'near' && here ? 'near' : 'world'
}

/** The nearest events to a point, with their distance: what the feed shows
 * when nothing is within the radius, instead of an empty list (owner,
 * 2026-10-27: "search shows nothing"). Placed events only. */
export function nearestEvents(
  events: SosEvent[],
  center: Here,
  count = 5,
): { event: SosEvent; km: number }[] {
  return events
    .filter((e) => e.lat !== null && e.lon !== null)
    .map((event) => ({
      event,
      km: distanceKm(center.lat, center.lon, event.lat as number, event.lon as number),
    }))
    .sort((a, b) => a.km - b.km)
    .slice(0, count)
}

/** A severity worth naming even when it is far: severe, extreme, or a
 * tsunami flag whatever its label. */
export function isSevere(event: SosEvent): boolean {
  return event.tsunami || SEVERITY_RANK[event.severity] >= SEVERITY_RANK.severe
}

/** Is this event placed: can a distance be measured to it? */
export function isPlaced(event: SosEvent): boolean {
  return event.lat !== null && event.lon !== null
}

/** Splits the visible events around the reader (or the chosen place).
 *
 * - `near`: placed within the radius, AND the events with no position whose
 *   country is one of `countries` -- the centre's own (countries.ts). Most
 *   Meteoalarm warnings carry no position at all; "Kreis Harz" orange storm
 *   went nowhere in Near me, no feed, no map, no count (review of PR #5).
 *   The row says "Germany, no exact area": the country is all we know.
 * - `severeElsewhere`: placed, beyond the radius, severe or worse.
 * - `unlocated`: no position, not matched to the centre's country, severe or
 *   worse. Counted apart: "elsewhere" is a claim about WHERE, and for these
 *   we do not know where.
 * - `unmatched`: no position, any severity, while `countries` is unknown
 *   (null): the feed must not say "nothing near you" about them. */
export function splitByScope(
  events: SosEvent[],
  here: Here,
  countries: readonly string[] | null = null,
  radiusKm = NEAR_RADIUS_KM,
): { near: SosEvent[]; severeElsewhere: number; unlocated: number; unmatched: number } {
  const near: SosEvent[] = []
  let severeElsewhere = 0
  let unlocated = 0
  let unmatched = 0
  for (const event of events) {
    if (isPlaced(event)) {
      if (distanceKm(here.lat, here.lon, event.lat as number, event.lon as number) <= radiusKm) {
        near.push(event)
      } else if (isSevere(event)) {
        severeElsewhere += 1
      }
      continue
    }
    const code = event.country_code?.toUpperCase() ?? null
    if (code && countries?.includes(code)) {
      near.push(event)
      continue
    }
    if (isSevere(event)) unlocated += 1
    if (countries === null) unmatched += 1
  }
  return { near, severeElsewhere, unlocated, unmatched }
}
