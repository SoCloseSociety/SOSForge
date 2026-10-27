/** Which countries a point is in, or next to, answered IN THE PAGE.
 *
 * Near me has to show the warnings that carry no position but name a
 * country -- most Meteoalarm warnings ("Kreis Harz - Bergland (Oberharz)",
 * orange storm, lat null), part of the WMO aggregate -- when that country is
 * the reader's. The page promises "your location never leaves your device",
 * so this is not a reverse-geocoding call: it is a point-in-polygon test on
 * Natural Earth's 1:10m countries (src/data/countries.json, built by
 * build_countries.py beside it, which also gives the French overseas
 * departments and the Caribbean Netherlands their own codes), loaded
 * lazily, the first time a position or a place needs it.
 *
 * A point within NEAR_BORDER_KM of another country gets that country too: a
 * reader on a border is concerned by both sides, and every such row is
 * labelled with its own country, so nothing is claimed for the wrong one.
 * The same margin keeps a coastal point that the simplified coastline leaves
 * at sea in its country. A point farther out at sea gets the one nearest
 * country within OFFSHORE_KM (an island too small for the table, Ponza, sits
 * 35 km off the mainland), and beyond that, none -- said as such by the feed.
 *
 * Known limit: an island Natural Earth does not draw at all and that lies
 * next to another country stays with that country (Kastellorizo, 2 km off
 * Turkey, reads TR). */

export const NEAR_BORDER_KM = 25
export const OFFSHORE_KM = 100

interface Shape {
  code: string
  /** the exterior ring, then the holes: lon, lat, lon, lat... in degrees */
  rings: Float64Array[]
  west: number
  south: number
  east: number
  north: number
}

export interface CountryTable {
  c: Record<string, number[][][]>
}

function decodeRing(deltas: number[]): Float64Array {
  const ring = new Float64Array(deltas.length)
  let x = 0
  let y = 0
  for (let i = 0; i < deltas.length; i += 2) {
    x += deltas[i]
    y += deltas[i + 1]
    ring[i] = x / 100
    ring[i + 1] = y / 100
  }
  return ring
}

export function decode(table: CountryTable): Shape[] {
  const shapes: Shape[] = []
  for (const [code, polygons] of Object.entries(table.c)) {
    for (const polygon of polygons) {
      const rings = polygon.map(decodeRing)
      const outer = rings[0]
      let west = 180
      let south = 90
      let east = -180
      let north = -90
      for (let i = 0; i < outer.length; i += 2) {
        west = Math.min(west, outer[i])
        east = Math.max(east, outer[i])
        south = Math.min(south, outer[i + 1])
        north = Math.max(north, outer[i + 1])
      }
      shapes.push({ code, rings, west, south, east, north })
    }
  }
  return shapes
}

function inRing(ring: Float64Array, lon: number, lat: number): boolean {
  let hit = false
  const n = ring.length
  for (let i = 0, j = n - 2; i < n; j = i, i += 2) {
    const xi = ring[i]
    const yi = ring[i + 1]
    const xj = ring[j]
    const yj = ring[j + 1]
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

/** Inside the exterior and in none of the holes: South Africa's ring no
 * longer covers Lesotho. */
function inside(shape: Shape, lon: number, lat: number): boolean {
  if (!inRing(shape.rings[0], lon, lat)) return false
  for (let h = 1; h < shape.rings.length; h += 1) if (inRing(shape.rings[h], lon, lat)) return false
  return true
}

/** Shortest distance from the point to the ring, in km, on a local flat
 * projection: exact enough within the 100 km it is asked about. */
function edgeKm(ring: Float64Array, lon: number, lat: number): number {
  const kx = 111.32 * Math.cos((lat * Math.PI) / 180)
  const ky = 110.57
  let best = Infinity
  const n = ring.length
  for (let i = 0, j = n - 2; i < n; j = i, i += 2) {
    const ax = (ring[j] - lon) * kx
    const ay = (ring[j + 1] - lat) * ky
    const bx = (ring[i] - lon) * kx
    const by = (ring[i + 1] - lat) * ky
    const dx = bx - ax
    const dy = by - ay
    const len = dx * dx + dy * dy
    const t = len === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len))
    best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy))
  }
  return best
}

/** The ISO 3166-1 alpha-2 codes of the countries at, or within
 * NEAR_BORDER_KM of, a point; else the nearest one within OFFSHORE_KM; else
 * none (open sea). */
export function countriesIn(shapes: Shape[], lat: number, lon: number): string[] {
  const kmLat = 110.57
  const kmLon = 111.32 * Math.max(0.05, Math.cos((lat * Math.PI) / 180))
  const found = new Set<string>()
  let nearest: { code: string; km: number } | null = null
  for (const shape of shapes) {
    if (found.has(shape.code)) continue
    if (
      lat < shape.south - OFFSHORE_KM / kmLat ||
      lat > shape.north + OFFSHORE_KM / kmLat ||
      lon < shape.west - OFFSHORE_KM / kmLon ||
      lon > shape.east + OFFSHORE_KM / kmLon
    ) {
      continue
    }
    if (inside(shape, lon, lat)) {
      found.add(shape.code)
      continue
    }
    let km = Infinity
    for (const ring of shape.rings) km = Math.min(km, edgeKm(ring, lon, lat))
    if (km <= NEAR_BORDER_KM) found.add(shape.code)
    else if (km <= OFFSHORE_KM && (!nearest || km < nearest.km)) nearest = { code: shape.code, km }
  }
  if (found.size === 0 && nearest) found.add(nearest.code)
  return [...found].sort()
}

let shapes: Shape[] | null = null
let loading: Promise<Shape[]> | null = null

/** The answer right now, when the table is already loaded; undefined when it
 * is not (yet). Lets a moving reader keep their country between two fixes
 * instead of losing it for a frame (final review of PR #5). */
export function countriesNow(lat: number, lon: number): string[] | undefined {
  return shapes ? countriesIn(shapes, lat, lon) : undefined
}

/** Loads the table once (a lazy chunk), then answers. */
export async function countriesAt(lat: number, lon: number): Promise<string[]> {
  loading ??= import('./data/countries.json').then((m) => {
    shapes = decode((m.default ?? m) as CountryTable)
    return shapes
  })
  try {
    return countriesIn(await loading, lat, lon)
  } catch (error) {
    // a failed chunk (offline first visit) is retried on the next call
    loading = null
    throw error
  }
}
