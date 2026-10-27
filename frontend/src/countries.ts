/** Which countries a point is in, or next to, answered IN THE PAGE.
 *
 * Near me has to show the warnings that carry no position but name a
 * country -- most Meteoalarm warnings ("Kreis Harz - Bergland (Oberharz)",
 * orange storm, lat null), part of the WMO aggregate -- when that country is
 * the reader's. The page promises "your location never leaves your device",
 * so this is not a reverse-geocoding call: it is a point-in-polygon test on
 * Natural Earth's 1:50m countries (src/data/countries.json, built by
 * build_countries.py beside it), loaded lazily, the first time a position or a
 * place needs it.
 *
 * A point within NEAR_BORDER_KM of another country gets that country too.
 * 1:50m borders are a few kilometres off (downtown El Paso falls in Mexico
 * at that scale), and a reader on a border is concerned by both sides
 * anyway; every such row is labelled with its own country, so nothing is
 * claimed for the wrong one. The same margin keeps a coastal point that the
 * simplified coastline leaves at sea in its country. */

export const NEAR_BORDER_KM = 25

interface Shape {
  code: string
  /** lon, lat, lon, lat... in degrees */
  ring: Float64Array
  west: number
  south: number
  east: number
  north: number
}

interface Table {
  c: Record<string, number[][]>
}

export function decode(table: Table): Shape[] {
  const shapes: Shape[] = []
  for (const [code, rings] of Object.entries(table.c)) {
    for (const deltas of rings) {
      const ring = new Float64Array(deltas.length)
      let x = 0
      let y = 0
      let west = 180
      let south = 90
      let east = -180
      let north = -90
      for (let i = 0; i < deltas.length; i += 2) {
        x += deltas[i]
        y += deltas[i + 1]
        const lon = x / 100
        const lat = y / 100
        ring[i] = lon
        ring[i + 1] = lat
        west = Math.min(west, lon)
        east = Math.max(east, lon)
        south = Math.min(south, lat)
        north = Math.max(north, lat)
      }
      shapes.push({ code, ring, west, south, east, north })
    }
  }
  return shapes
}

function inside(ring: Float64Array, lon: number, lat: number): boolean {
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

/** Shortest distance from the point to the ring, in km, on a local flat
 * projection: exact enough within the 25 km it is asked about. */
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
    const px = ax + t * dx
    const py = ay + t * dy
    best = Math.min(best, Math.hypot(px, py))
  }
  return best
}

/** The ISO 3166-1 alpha-2 codes of the countries at, or within
 * NEAR_BORDER_KM of, a point. Empty at sea. */
export function countriesIn(shapes: Shape[], lat: number, lon: number): string[] {
  const marginLat = NEAR_BORDER_KM / 110.57
  const marginLon = NEAR_BORDER_KM / (111.32 * Math.max(0.05, Math.cos((lat * Math.PI) / 180)))
  const found = new Set<string>()
  for (const shape of shapes) {
    if (found.has(shape.code)) continue
    if (
      lat < shape.south - marginLat ||
      lat > shape.north + marginLat ||
      lon < shape.west - marginLon ||
      lon > shape.east + marginLon
    ) {
      continue
    }
    if (inside(shape.ring, lon, lat) || edgeKm(shape.ring, lon, lat) <= NEAR_BORDER_KM) {
      found.add(shape.code)
    }
  }
  return [...found].sort()
}

let loading: Promise<Shape[]> | null = null

/** Loads the table once (a lazy chunk), then answers. */
export async function countriesAt(lat: number, lon: number): Promise<string[]> {
  loading ??= import('./data/countries.json').then((m) => decode((m.default ?? m) as Table))
  try {
    return countriesIn(await loading, lat, lon)
  } catch (error) {
    // a failed chunk (offline first visit) is retried on the next call
    loading = null
    throw error
  }
}
