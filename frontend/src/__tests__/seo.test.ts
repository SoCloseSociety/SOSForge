/** The static text never states how many sources there are (review,
 * 2026-10-27): seo.json said "Twenty-six" in five languages, copied by the
 * prerender into every page, while /api/sources listed 32. The live count is
 * in the footer, read from the server; a number frozen in the HTML is a claim
 * that goes stale the day a source is added or dropped. */
import { describe, expect, it } from 'vitest'
import seo from '../seo.json'

const COUNT = [
  /\d+\s*(official|public)?\s*(sources?|feeds?)/i, // en
  /\b(nineteen|twenty|thirty|forty)\b/i,
  /\d+\s*(sources|flux)|\b(dix-neuf|vingt|trente|quarante)\b/i, // fr
  /\d+\s*fuentes|\b(diecinueve|veinte|veinti\w*|treinta|cuarenta)\b/i, // es
  /\d+\s*(の|つの)?\s*(公式|情報源|フィード)/, // ja
  /\d+\s*(sumber|umpan)|\b(sembilan belas|\w+ puluh)\b/i, // id
]

describe('seo.json', () => {
  for (const [lang, entry] of Object.entries(seo)) {
    for (const [key, text] of Object.entries(entry as Record<string, string>)) {
      it(`${lang}.${key} states no fixed number of sources`, () => {
        for (const pattern of COUNT) expect(text).not.toMatch(pattern)
      })
    }
  }
})
