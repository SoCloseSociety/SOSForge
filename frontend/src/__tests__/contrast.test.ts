/** Contrast, measured on the stylesheet itself, in both themes.
 *
 * Same method as Sentinel House `tools/verifier-contraste.js`: read the
 * literal tokens of each theme block, resolve `var()` chains, COMPOSE every
 * translucent surface over the worst background it can sit on, and keep the
 * worst ratio. A number, not an adjective -- "it reads fine" proved nothing
 * the day `info` measured 3.9:1 on the panel.
 *
 * Bars: 4.5:1 for text (WCAG AA), 3:1 for a fill or a control's outline
 * (WCAG 1.4.11). The severity rows are the point of this file: the hue is
 * fixed, only the lightness moves between themes, and every rank must clear
 * AA on every surface a severity word is written on.
 */
import { describe, expect, it } from 'vitest'

// Read from disk: vitest stubs every `.css` import to an empty string, `?raw`
// included, and the project carries no Node typings (hence the untyped
// specifier). This is the same file Vite serves.
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as {
  readFileSync(path: string, encoding: string): string
}
// vitest runs from the frontend root (vitest.config.ts lives there)
const root = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
const css = fs.readFileSync(`${root}/src/styles.css`, 'utf8')

type RGBA = [number, number, number, number]

function block(selector: string): string {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const at = clean.indexOf(`${selector} {`)
  if (at < 0) throw new Error(`no ${selector} block in styles.css`)
  const open = clean.indexOf('{', at)
  return clean.slice(open + 1, clean.indexOf('}', open))
}

function tokens(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of text.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim()
  return out
}

const LUNAR = tokens(block(':root'))
const SOFT = { ...LUNAR, ...tokens(block(':root[data-theme="soft"]')) }

function resolve(pal: Record<string, string>, value: string, depth = 0): string {
  const m = /^var\((--[a-z0-9-]+)\)$/.exec(value.trim())
  if (m && depth < 8) return resolve(pal, pal[m[1]] ?? '', depth + 1)
  return value.trim()
}

function color(value: string): RGBA {
  let m = /^#([0-9a-f]{6})$/i.exec(value)
  if (m) return [0, 2, 4].map((i) => parseInt(m![1].slice(i, i + 2), 16)).concat(1) as RGBA
  m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(value)
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]]
  throw new Error(`not a colour: ${value}`)
}

const tok = (pal: Record<string, string>, name: string) => color(resolve(pal, pal[name] ?? ''))

const over = (top: RGBA, under: RGBA): RGBA =>
  [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat(1) as RGBA

function luminance([r, g, b]: RGBA): number {
  const f = (c: number) => {
    c /= 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

function ratio(a: RGBA, b: RGBA): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** The grounds of a theme: what any translucent surface can sit on. */
const GROUNDS = ['--ground', '--bg-0', '--bg-1', '--bg-2', '--bg-3', '--bg-4']

/** Worst ratio of `fg` over `surface`, the surface composed on every ground. */
function worst(pal: Record<string, string>, fg: string, surface: string): number {
  const ink = tok(pal, fg)
  const top = tok(pal, surface)
  const unders = top[3] < 1 ? GROUNDS.map((g) => tok(pal, g)) : [top]
  return Math.min(...unders.map((u) => ratio(over(ink, top[3] < 1 ? over(top, u) : u), top[3] < 1 ? over(top, u) : u)))
}

/** Where severity WORDS are written: the panel, a feed row (rest, hover,
 * open), a card, a counter, the detail panel, the popup, the legend. */
const SEVERITY_SURFACES = [
  '--ground',
  '--bg-1',
  '--bg-2',
  '--bg-3',
  '--surface-card',
  '--surface-mini',
  '--surface-panel',
]
const RANKS = ['info', 'minor', 'moderate', 'severe', 'extreme']

describe.each([
  ['Lunar', LUNAR],
  ['Soft', SOFT],
])('%s theme', (_name, pal) => {
  it.each(RANKS)('the %s severity word clears 4.5:1 on every surface it is written on', (rank) => {
    for (const surface of SEVERITY_SURFACES) {
      expect(worst(pal, `--sev-${rank}-text`, surface), `${rank} on ${surface}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('body, secondary and caption text clear 4.5:1 on cards and panels', () => {
    for (const ink of ['--t1', '--t2', '--t3']) {
      for (const surface of ['--ground', '--bg-2', '--bg-3', '--surface-card', '--surface-panel']) {
        expect(worst(pal, ink, surface), `${ink} on ${surface}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('the state colours (OK, attention, alert) and links clear 4.5:1 on a card', () => {
    for (const ink of ['--mint', '--amber', '--red', '--cyan']) {
      for (const surface of ['--ground', '--surface-card', '--surface-pill', '--surface-panel', '--bg-2']) {
        expect(worst(pal, ink, surface), `${ink} on ${surface}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('the primary button and the vital alert clear 4.5:1', () => {
    expect(worst(pal, '--primary-ink', '--primary-bg')).toBeGreaterThanOrEqual(4.5)
    expect(worst(pal, '--vital-ink', '--vital-bg')).toBeGreaterThanOrEqual(4.5)
    expect(worst(pal, '--vital-ink', '--vital-bg-2')).toBeGreaterThanOrEqual(4.5)
  })

  it('a control outline that must be found clears 3:1 (WCAG 1.4.11)', () => {
    for (const surface of ['--ground', '--bg-1', '--bg-2']) {
      const edge = over(tok(pal, '--edge'), tok(pal, surface))
      expect(ratio(edge, tok(pal, surface)), `--edge on ${surface}`).toBeGreaterThanOrEqual(3)
    }
  })
})

describe('the severity palette is one palette, whatever the theme', () => {
  const hue = ([r, g, b]: RGBA) => {
    const [R, G, B] = [r / 255, g / 255, b / 255]
    const max = Math.max(R, G, B)
    const d = max - Math.min(R, G, B)
    if (d === 0) return 0
    const h = max === R ? ((G - B) / d) % 6 : max === G ? (B - R) / d + 2 : (R - G) / d + 4
    return (h * 60 + 360) % 360
  }

  it.each(RANKS)('%s keeps the hue of its fill in both themes (lightness only)', (rank) => {
    const fill = hue(tok(LUNAR, `--sev-${rank}`))
    for (const pal of [LUNAR, SOFT]) {
      const text = hue(tok(pal, `--sev-${rank}-text`))
      const gap = Math.min(Math.abs(text - fill), 360 - Math.abs(text - fill))
      expect(gap, rank).toBeLessThanOrEqual(3)
    }
  })

  it('the fills are the same in both themes: the map is dark in both', () => {
    for (const rank of RANKS) expect(SOFT[`--sev-${rank}`]).toBe(LUNAR[`--sev-${rank}`])
  })

  it('every fill clears 3:1 on the dark map ground', () => {
    for (const rank of RANKS) {
      expect(ratio(tok(LUNAR, `--sev-${rank}`), tok(LUNAR, '--ground')), rank).toBeGreaterThanOrEqual(3)
    }
  })

  it('mint never doubles as a severity', () => {
    for (const rank of RANKS) {
      for (const pal of [LUNAR, SOFT]) {
        expect(pal[`--sev-${rank}-text`]).not.toBe(pal['--mint'])
        expect(pal[`--sev-${rank}`]).not.toBe(pal['--mint'])
      }
    }
  })
})
