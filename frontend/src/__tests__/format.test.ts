/** format.ts: les petites fonctions que chaque ligne du flux traverse. */
import { describe, expect, it } from 'vitest'
import { badge, flagEmoji, formatAge, type T } from '../format'
import { translate } from '../i18n'
import { makeEvent } from './helpers'

/** Un `t` deterministe, cale sur l'anglais. */
const t: T = (key, vars) => translate('en', key, vars)

describe('formatAge: chaque palier a son unite', () => {
  it('moins de 5 s: "a l instant", pas un compteur qui gigote', () => {
    expect(formatAge(t, 3)).toBe('just now')
  })

  it('un age legerement negatif (horloges pas encore alignees) reste "a l instant", jamais un age negatif', () => {
    expect(formatAge(t, -2)).toBe('just now')
  })

  it('secondes de 5 a 59', () => {
    expect(formatAge(t, 42)).toBe('42 s ago')
    expect(formatAge(t, 59.9)).toBe('59 s ago')
  })

  it('minutes de 1 a 59', () => {
    expect(formatAge(t, 60)).toBe('1 min ago')
    expect(formatAge(t, 3599)).toBe('59 min ago')
  })

  it('heures de 1 a 23', () => {
    expect(formatAge(t, 3600)).toBe('1 h ago')
    expect(formatAge(t, 86399)).toBe('23 h ago')
  })

  it('jours au-dela de 24 h', () => {
    expect(formatAge(t, 86400)).toBe('1 d ago')
    expect(formatAge(t, 86400 * 12)).toBe('12 d ago')
  })

  it('une valeur non finie rend une chaine vide, pas NaN a l ecran', () => {
    expect(formatAge(t, Number.NaN)).toBe('')
    expect(formatAge(t, Number.POSITIVE_INFINITY)).toBe('')
  })
})

describe('flagEmoji: un vrai drapeau ou rien', () => {
  it('un code ISO2 valide rend le drapeau du pays', () => {
    expect(flagEmoji('JP')).toBe('🇯🇵')
    expect(flagEmoji('ID')).toBe('🇮🇩')
  })

  it('la casse ne change pas le pays', () => {
    expect(flagEmoji('fr')).toBe('🇫🇷')
  })

  it('pas de code = pas de drapeau, jamais un drapeau approximatif', () => {
    expect(flagEmoji(null)).toBeNull()
    expect(flagEmoji('')).toBeNull()
  })

  it('une longueur invalide rend null plutot qu un faux drapeau', () => {
    expect(flagEmoji('F')).toBeNull()
    expect(flagEmoji('FRA')).toBeNull()
  })

  // Constat (non corrige ici, voir le rapport): deux caracteres NON
  // alphabetiques ('12', '??') passent le seul controle existant (longueur 2)
  // et produisent des points de code hors de la plage des indicateurs
  // regionaux, donc un caractere parasite au lieu de null. Le serveur
  // n'envoyant que de l'ISO2 ou null, le cas est theorique; il est documente
  // sans etre grave dans un test.
})

describe('badge: la pastille de gauche', () => {
  it('un evenement avec magnitude affiche la magnitude et son type en majuscules', () => {
    expect(badge(makeEvent({ magnitude: 5.83, mag_type: 'mw' }))).toEqual({
      value: '5.8',
      unit: 'MW',
    })
  })

  it('une magnitude sans type retombe sur l unite generique MAG', () => {
    expect(badge(makeEvent({ magnitude: 4.5, mag_type: null }))).toEqual({
      value: '4.5',
      unit: 'MAG',
    })
  })

  it('un evenement sans magnitude affiche le pictogramme de son type d alea', () => {
    expect(badge(makeEvent({ kind: 'flood', magnitude: null, mag_type: null }))).toEqual({
      value: '💧',
      unit: '',
    })
  })
})
