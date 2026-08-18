/** i18n: the rule is that a missing string NEVER renders as emptiness, and
 * that none of the five languages can fall behind the others without a test
 * shouting about it. */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { detectLang, translate, type Lang } from '../i18n'
// The raw source of the i18n module, served by Vite: it is the same file as
// the one that gets compiled, it cannot lie.
import source from '../i18n.ts?raw'

const STORAGE_KEY = 'sosforge.lang'

describe('interpolation', () => {
  it('replaces {n} with the provided value', () => {
    expect(translate('fr', 'kpi.tracked.sub', { n: 42 })).toBe('42 sur la dernière heure')
  })

  it('leaves the placeholder intact if the variable is not provided', () => {
    expect(translate('en', 'footer.clients', {})).toBe('{n} client(s) connected')
  })
})

describe('fallbacks', () => {
  it('falls back to English when the requested language lacks the key', () => {
    // No real dictionary has a hole (the parity test below guarantees it), so
    // we force the hole with an unknown language: the fallback path
    // DICTS[lang] -> DICTS.en is exactly the same.
    expect(translate('xx' as Lang, 'app.live')).toBe('LIVE')
  })

  it('falls back to the key itself when it exists nowhere: never emptiness', () => {
    expect(translate('fr', 'nonexistent.key')).toBe('nonexistent.key')
  })
})

describe('detectLang', () => {
  const originalLanguages = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(navigator),
    'languages',
  )

  function stubLanguages(languages: string[]) {
    Object.defineProperty(navigator, 'languages', { value: languages, configurable: true })
  }

  beforeEach(() => {
    localStorage.removeItem(STORAGE_KEY)
  })

  afterEach(() => {
    localStorage.removeItem(STORAGE_KEY)
    // Remove the stub set on the instance to restore jsdom behaviour
    delete (navigator as unknown as Record<string, unknown>).languages
    if (originalLanguages) {
      Object.defineProperty(Object.getPrototypeOf(navigator), 'languages', originalLanguages)
    }
  })

  it('the remembered language wins over the browser one', () => {
    localStorage.setItem(STORAGE_KEY, 'ja')
    stubLanguages(['fr-FR', 'en-US'])
    expect(detectLang()).toBe('ja')
  })

  it('an invalid remembered language is ignored in favour of the browser', () => {
    localStorage.setItem(STORAGE_KEY, 'de')
    stubLanguages(['id-ID', 'en-US'])
    expect(detectLang()).toBe('id')
  })

  it('without a remembered language, the first supported browser language wins', () => {
    stubLanguages(['pt-BR', 'es-419', 'en-US'])
    expect(detectLang()).toBe('es')
  })

  it('with no supported language at all, English: a disaster tracker is read by anyone', () => {
    stubLanguages(['de-DE', 'pt-BR'])
    expect(detectLang()).toBe('en')
  })
})

describe('parity of the five dictionaries', () => {
  // The dictionaries are private to the module (a deliberate choice: no
  // public API to iterate the keys). So we analyse the source itself
  // (?raw import).
  function dictKeys(name: string): string[] {
    const block = source.match(new RegExp(`const ${name}: Dict = \\{([\\s\\S]*?)\\n\\}`))
    if (!block) throw new Error(`dictionary ${name} not found in i18n.ts`)
    return [...block[1].matchAll(/^\s*'([^']+)':/gm)].map((m) => m[1])
  }

  const reference = dictKeys('en')

  it('the English reference dictionary is properly extracted (parser guard-rail)', () => {
    expect(reference.length).toBeGreaterThan(40)
    expect(reference).toContain('app.live')
    expect(reference).toContain('footer.basemap')
  })

  it.each(['fr', 'es', 'ja', 'id'])(
    'the %s dictionary has exactly the same keys as English: no translation forgotten',
    (name) => {
      // THIS is the test that will ring when a feature adds a string in one
      // language and not in the four others.
      expect(dictKeys(name).sort()).toEqual([...reference].sort())
    },
  )

  it('no dictionary contains a duplicated key', () => {
    for (const name of ['fr', 'en', 'es', 'ja', 'id']) {
      const keys = dictKeys(name)
      expect(new Set(keys).size, `duplicate in ${name}`).toBe(keys.length)
    }
  })
})
