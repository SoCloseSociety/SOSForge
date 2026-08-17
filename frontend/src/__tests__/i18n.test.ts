/** i18n: la regle est qu'une chaine manquante ne rend JAMAIS du vide, et
 * qu'aucune des cinq langues ne peut prendre du retard sur les autres sans
 * qu'un test le crie. */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { detectLang, translate, type Lang } from '../i18n'
// La source brute du module i18n, servie par Vite: c'est le meme fichier que
// celui qui est compile, il ne peut pas mentir.
import source from '../i18n.ts?raw'

const STORAGE_KEY = 'sosforge.lang'

describe('interpolation', () => {
  it('remplace {n} par la valeur fournie', () => {
    expect(translate('fr', 'kpi.tracked.sub', { n: 42 })).toBe('42 sur la dernière heure')
  })

  it('laisse le placeholder intact si la variable n est pas fournie', () => {
    expect(translate('en', 'footer.clients', {})).toBe('{n} client(s) connected')
  })
})

describe('replis', () => {
  it('retombe sur l anglais quand la langue demandee n a pas la cle', () => {
    // Aucun dictionnaire reel n'a de trou (le test de parite plus bas le
    // garantit), donc on force le trou avec une langue inconnue: le chemin de
    // repli DICTS[lang] -> DICTS.en est exactement le meme.
    expect(translate('xx' as Lang, 'app.live')).toBe('LIVE')
  })

  it('retombe sur la cle elle-meme quand elle n existe nulle part: jamais du vide', () => {
    expect(translate('fr', 'cle.inexistante')).toBe('cle.inexistante')
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
    // Retire le stub pose sur l'instance pour retrouver le comportement jsdom
    delete (navigator as unknown as Record<string, unknown>).languages
    if (originalLanguages) {
      Object.defineProperty(Object.getPrototypeOf(navigator), 'languages', originalLanguages)
    }
  })

  it('la langue memorisee prime sur celle du navigateur', () => {
    localStorage.setItem(STORAGE_KEY, 'ja')
    stubLanguages(['fr-FR', 'en-US'])
    expect(detectLang()).toBe('ja')
  })

  it('une langue memorisee invalide est ignoree au profit du navigateur', () => {
    localStorage.setItem(STORAGE_KEY, 'de')
    stubLanguages(['id-ID', 'en-US'])
    expect(detectLang()).toBe('id')
  })

  it('sans langue memorisee, la premiere langue supportee du navigateur gagne', () => {
    stubLanguages(['pt-BR', 'es-419', 'en-US'])
    expect(detectLang()).toBe('es')
  })

  it('sans aucune langue supportee, l anglais: un tracker de catastrophes est lu par n importe qui', () => {
    stubLanguages(['de-DE', 'pt-BR'])
    expect(detectLang()).toBe('en')
  })
})

describe('parite des cinq dictionnaires', () => {
  // Les dictionnaires sont prives au module (choix assume: pas d'API publique
  // pour iterer les cles). On analyse donc la source elle-meme (import ?raw).
  function dictKeys(name: string): string[] {
    const block = source.match(new RegExp(`const ${name}: Dict = \\{([\\s\\S]*?)\\n\\}`))
    if (!block) throw new Error(`dictionnaire ${name} introuvable dans i18n.ts`)
    return [...block[1].matchAll(/^\s*'([^']+)':/gm)].map((m) => m[1])
  }

  const reference = dictKeys('en')

  it('le dictionnaire anglais de reference est bien extrait (garde-fou du parseur)', () => {
    expect(reference.length).toBeGreaterThan(40)
    expect(reference).toContain('app.live')
    expect(reference).toContain('footer.basemap')
  })

  it.each(['fr', 'es', 'ja', 'id'])(
    'le dictionnaire %s a exactement les memes cles que l anglais: aucune traduction oubliee',
    (name) => {
      // C'est CE test qui sonnera quand une fonctionnalite ajoutera une chaine
      // dans une langue et pas dans les quatre autres.
      expect(dictKeys(name).sort()).toEqual([...reference].sort())
    },
  )

  it('aucun dictionnaire ne contient de cle dupliquee', () => {
    for (const name of ['fr', 'en', 'es', 'ja', 'id']) {
      const keys = dictKeys(name)
      expect(new Set(keys).size, `doublon dans ${name}`).toBe(keys.length)
    }
  })
})
