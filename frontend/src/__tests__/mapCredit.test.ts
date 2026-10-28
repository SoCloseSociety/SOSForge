/** The map's credit on the phone layout (review of the fold fix, 2026-10-27).
 *
 * MapLibre puts its compact attribution in the bottom-right corner, right
 * under the zoom column: 10 px in, two 44 px buttons on touch, so the
 * column runs from y 10 to 98. The phone layout bounds the map to 100 px
 * in landscape (568x320, 667x375, 844x390), where the credit's button
 * started at y 66: ON the zoom-out button (2 of its 9 tap points went to
 * the credit at 568x320) and, opened, its strip covered the button whole.
 * The layout is measured in Chrome by tools/responsive.py (the map
 * controls check); this pins the stylesheet's side of it.
 */
import { describe, expect, it } from 'vitest'
import { classWeight, px, readSheet, rules } from './cssRules'

const STYLES = rules(readSheet('src/styles.css'))
const MAPLIBRE = rules(readSheet('node_modules/maplibre-gl/dist/maplibre-gl.css'))
const PHONE = '(max-width: 779px), (max-height: 560px)'
const COARSE = '(pointer: coarse), (hover: none)'

const maplibre = (selector: string, prop: string): string | undefined =>
  MAPLIBRE.filter((r) => r.selectors.includes(selector) && r.decls[prop] !== undefined).at(-1)
    ?.decls[prop]

/** The right-hand value of a CSS `margin` shorthand. */
function marginRight(value: string | undefined): number {
  const parts = (value ?? '').split(' ')
  return px(parts.length === 1 ? parts[0] : parts[1])
}

describe("the map's credit on the phone layout", () => {
  const credit = STYLES.find(
    (r) => r.media === PHONE && r.selectors.some((s) => s.includes('.maplibregl-ctrl-attrib')),
  )

  it('MapLibre stacks the credit under the zoom column, 10 px from the same edge', () => {
    // the premise of the fix, read from MapLibre's own sheet: if it ever
    // moves either control, this says so before a reader's thumb does
    expect(marginRight(maplibre('.maplibregl-ctrl-top-right .maplibregl-ctrl', 'margin'))).toBe(10)
    expect(marginRight(maplibre('.maplibregl-ctrl-attrib.maplibregl-compact', 'margin'))).toBe(10)
  })

  it('sits BESIDE the zoom column, not under it', () => {
    expect(credit, 'a phone rule for .maplibregl-ctrl-attrib').toBeDefined()
    const zoom = STYLES.find(
      (r) => r.media === COARSE && r.selectors.includes('.map .maplibregl-ctrl-group button'),
    )
    const column =
      marginRight(maplibre('.maplibregl-ctrl-top-right .maplibregl-ctrl', 'margin')) +
      px(zoom?.decls.width)
    expect(column).toBe(54)
    // the column, and a gap a thumb can tell apart
    expect(px(credit!.decls['margin-right'])).toBeGreaterThanOrEqual(column + 8)
  })

  it("wins over MapLibre's own margin, whatever the load order", () => {
    // MapLibre's sheet is injected after ours (the map is lazy loaded)
    const theirs = classWeight('.maplibregl-ctrl-attrib.maplibregl-compact')
    for (const selector of credit!.selectors) expect(classWeight(selector)).toBeGreaterThan(theirs)
  })

  it('every .maplibregl-* override is scoped under .map', () => {
    const overrides = STYLES.flatMap((r) => r.selectors).filter((s) => s.includes('.maplibregl-'))
    expect(overrides.length).toBeGreaterThan(5)
    for (const selector of overrides) expect(selector).toMatch(/^\.map /)
  })
})
