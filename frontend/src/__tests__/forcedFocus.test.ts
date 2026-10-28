/** Keyboard focus in forced colours (Windows High Contrast).
 *
 * THE focus ring of this product is a box-shadow (`--ring`), drawn with
 * `outline: none`, and forced colours remove every box-shadow: with a
 * high-contrast theme on, no focused control showed anything at all -- a
 * focused button measured outline none, shadow none (review of the fold
 * fix, 2026-10-27). jsdom has no forced-colours mode and no painting; this
 * pins what the stylesheets must say for Chrome to draw a ring. The proof
 * that it does is measured in a real Chrome, every focusable kind, pixels
 * changed around each one (tasks/todo.md, "UI leftovers").
 */
import { describe, expect, it } from 'vitest'
import { blocks, px, readSheet, rules, splitTop, type Rule } from './cssRules'

const STYLES = readSheet('src/styles.css')
const SHEETS: Record<string, string> = {
  'styles.css': STYLES,
  'ClimatePanel.css': readSheet('src/components/ClimatePanel.css'),
  'PlaceReport.css': readSheet('src/components/PlaceReport.css'),
}
const FORCED = '(forced-colors: active)'
const SYSTEM = /^\d+px solid (Highlight|CanvasText)( !important)?$/

const last = blocks(STYLES).at(-1)!
const lastRules = rules(`${last.prelude} {${last.body}}`)
/** The rule that gives every focused control its outline. */
const general = lastRules.find((r) => r.selectors.some((s) => s.startsWith(':focus-visible')))

/** What the general rule leaves out: its `:not(...)` list, as selectors. */
function excluded(): string[] {
  const sel = general?.selectors[0] ?? ''
  const at = sel.indexOf(':not(')
  if (at < 0) return []
  const start = at + ':not('.length
  let depth = 1
  let end = start
  while (end < sel.length && depth > 0) {
    if (sel[end] === '(') depth += 1
    else if (sel[end] === ')') depth -= 1
    end += 1
  }
  return splitTop(sel.slice(start, end - 1), ',')
}

const forced = rules(STYLES).filter((r) => r.media === FORCED)
const inForced = (selector: string, prop: string): string | undefined =>
  forced.filter((r) => r.selectors.includes(selector) && r.decls[prop] !== undefined).at(-1)
    ?.decls[prop]

/** Every focus ring drawn as a box-shadow, outside forced colours. */
function shadowRings(): { sheet: string; selector: string; inset: boolean }[] {
  const out: { sheet: string; selector: string; inset: boolean }[] = []
  for (const [sheet, css] of Object.entries(SHEETS)) {
    for (const rule of rules(css) as Rule[]) {
      if (rule.media?.includes('forced-colors')) continue
      const shadow = rule.decls['box-shadow'] ?? ''
      if (!shadow.includes('var(--ring')) continue
      for (const selector of rule.selectors) {
        if (!/:focus-(visible|within)$/.test(selector)) continue
        out.push({ sheet, selector, inset: shadow.includes('--ring-inset') })
      }
    }
  }
  return out
}

describe('keyboard focus in forced colours', () => {
  it('the stylesheet ends with the forced-colors focus rule: an outline in a system colour', () => {
    expect(last.prelude).toBe(`@media ${FORCED}`)
    expect(general, 'a :focus-visible rule in the last block').toBeDefined()
    const outline = general!.decls.outline ?? ''
    expect(outline).toMatch(SYSTEM)
    expect(px(outline.split(' ')[0])).toBeGreaterThanOrEqual(2)
    // Component sheets and MapLibre's (which lands AFTER this one) declare
    // `outline: none` on their own controls at a higher specificity
    // (`.place-report .place-more-btn:focus-visible`, `.maplibregl-ctrl-group
    // button`): only an important outline survives all of them.
    expect(outline).toMatch(/!important$/)
    expect(readSheet('node_modules/maplibre-gl/dist/maplibre-gl.css')).toMatch(
      /\.maplibregl-ctrl-group button\{[^}]*outline:none/,
    )
  })

  it('the general rule weighs no more than one pseudo-class, so the inset rings can move it', () => {
    // `:focus-visible:not(...)` would weigh (0,2,1) and beat
    // `.item:focus-visible { outline-offset }`: the exclusions go in :where()
    expect(general!.selectors).toHaveLength(1)
    expect(general!.selectors[0]).toMatch(/^:focus-visible(:where\(.*\))?$/)
  })

  it('every ring drawn as a box-shadow has its outline in forced colours', () => {
    const rings = shadowRings()
    // the scan saw the rings it is about (global, fields, rows, switch, panels)
    expect(rings.length).toBeGreaterThanOrEqual(10)
    const skip = excluded()
    for (const { sheet, selector, inset } of rings) {
      const where = `${sheet}: ${selector}`
      if (selector.endsWith(':focus-within')) {
        // a field's FRAME carries its ring: the frame gets the outline
        const frame = selector.split(' ').at(-1)!
        expect(inForced(frame, 'outline') ?? 'none', where).toMatch(SYSTEM)
      } else {
        // a :focus-visible ring: the general rule draws it, unless left out
        expect(skip, where).not.toContain(selector.replace(/:focus-visible$/, '') || '*')
      }
      if (inset) {
        // flush with a scroller's or the switch's edge, where an outer ring
        // is cut off: the outline stays inside too
        expect(px(inForced(selector, 'outline-offset')), where).toBeLessThan(0)
      }
    }
  })

  it('the fields whose frame carries the ring are left out of the general rule', () => {
    // the language select is transparent over its flag on a phone (an
    // outline on it is invisible), the search input sits inside its frame
    // (a ring inside a ring): their frames are outlined instead
    expect(excluded()).toEqual(expect.arrayContaining(['.search input', '.lang select']))
    for (const frame of ['.search:focus-within', '.lang:focus-within']) {
      expect(inForced(frame, 'outline') ?? 'none', frame).toMatch(SYSTEM)
    }
  })

  it('a dialog taking the focus as a container shows no ring, as in the normal theme', () => {
    // FooterTools focuses the place report's dialog itself on open (for the
    // screen reader); its panel shadow replaces THE ring in the normal
    // theme (same weight, later in the sheet), so forced colours do not
    // draw one around the whole sheet either
    expect(excluded()).toContain('[role="dialog"]')
    const panel = rules(STYLES).find((r) => r.media === null && r.selectors.includes('.climate-dialog'))
    expect(panel?.decls['box-shadow']).toBe('var(--shadow-panel)')
  })

  it('a control painted in Highlight is never ringed in Highlight', () => {
    const painted = forced.filter((r) => /^Highlight\b/.test(r.decls.background ?? ''))
    // the pressed segment of the Near me / World switch
    expect(painted.length).toBeGreaterThanOrEqual(1)
    for (const rule of painted) {
      for (const selector of rule.selectors) {
        const color = inForced(`${selector}:focus-visible`, 'outline-color') ?? ''
        expect(color, selector).toMatch(/^(HighlightText|CanvasText|Canvas)\b/)
        // it must beat the general rule's important outline
        expect(color, selector).toMatch(/!important$/)
      }
    }
  })
})
