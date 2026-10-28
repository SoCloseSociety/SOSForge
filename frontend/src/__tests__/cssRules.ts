/** A small reader for the stylesheets, for the tests that pin what jsdom
 * cannot lay out (it has no layout engine and no forced-colours mode).
 *
 * Read from disk: vitest stubs every `.css` import to an empty string, `?raw`
 * included, and the project carries no Node typings (hence the untyped
 * specifier, as in contrast.test.ts). Comments are stripped; `@media` blocks
 * are walked one level deep (no stylesheet here nests deeper); other
 * at-rules (`@keyframes`, `@font-face`, `@supports`) are skipped. */

const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as {
  readFileSync(path: string, encoding: string): string
}
// vitest runs from the frontend root (vitest.config.ts lives there)
const root = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()

/** A file under the frontend root, as text. */
export function readSheet(path: string): string {
  return fs.readFileSync(`${root}/${path}`, 'utf8')
}

export interface Block {
  prelude: string
  body: string
}

export interface Rule {
  /** the `@media` condition the rule sits in, null at the top level */
  media: string | null
  selectors: string[]
  decls: Record<string, string>
}

/** Splits on `sep` outside parentheses, brackets and quotes. */
export function splitTop(text: string, sep: string): string[] {
  const out: string[] = []
  let depth = 0
  let quote: string | null = null
  let start = 0
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i]
    if (quote) {
      if (c === quote && text[i - 1] !== '\\') quote = null
    } else if (c === '"' || c === "'") quote = c
    else if (c === '(' || c === '[') depth += 1
    else if (c === ')' || c === ']') depth -= 1
    else if (c === sep && depth === 0) {
      out.push(text.slice(start, i))
      start = i + 1
    }
  }
  out.push(text.slice(start))
  return out.map((s) => s.trim()).filter(Boolean)
}

/** The top-level blocks of a stylesheet, in source order. */
export function blocks(css: string): Block[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const out: Block[] = []
  let i = 0
  while (i < text.length) {
    const open = text.indexOf('{', i)
    if (open < 0) break
    let depth = 1
    let j = open + 1
    while (j < text.length && depth > 0) {
      if (text[j] === '{') depth += 1
      else if (text[j] === '}') depth -= 1
      j += 1
    }
    // an `@import ...;` or `@charset` statement before the block is not its prelude
    const prelude = text.slice(i, open).split(';').at(-1)!.trim().replace(/\s+/g, ' ')
    out.push({ prelude, body: text.slice(open + 1, j - 1) })
    i = j
  }
  return out
}

function declarations(body: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of splitTop(body, ';')) {
    const colon = part.indexOf(':')
    if (colon < 0) continue
    out[part.slice(0, colon).trim()] = part.slice(colon + 1).trim().replace(/\s+/g, ' ')
  }
  return out
}

/** Every style rule, with the `@media` condition it sits in. */
export function rules(css: string): Rule[] {
  const out: Rule[] = []
  const walk = (list: Block[], media: string | null) => {
    for (const { prelude, body } of list) {
      if (prelude.startsWith('@media')) walk(blocks(body), prelude.slice('@media'.length).trim())
      else if (prelude.startsWith('@')) continue
      else
        out.push({
          media,
          selectors: splitTop(prelude, ',').map((s) => s.replace(/\s+/g, ' ')),
          decls: declarations(body),
        })
    }
  }
  walk(blocks(css), null)
  return out
}

/** `44px` -> 44. NaN for anything that is not a plain pixel length. */
export function px(value: string | undefined): number {
  const m = /^(-?[\d.]+)px$/.exec((value ?? '').trim())
  return m ? Number(m[1]) : Number.NaN
}

/** The (classes + attributes + pseudo-classes) count of a selector without
 * ids, which is all these stylesheets use to win a tie: enough to say which
 * of two rules applies whatever their load order. */
export function classWeight(selector: string): number {
  return (selector.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+/g) ?? []).length
}
