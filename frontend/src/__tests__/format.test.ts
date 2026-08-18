/** format.ts: the small functions every feed row goes through. */
import { describe, expect, it } from 'vitest'
import { badge, flagEmoji, formatAge, type T } from '../format'
import { translate } from '../i18n'
import { makeEvent } from './helpers'

/** A deterministic `t`, pinned to English. */
const t: T = (key, vars) => translate('en', key, vars)

describe('formatAge: each tier has its unit', () => {
  it('under 5 s: "just now", not a jittery counter', () => {
    expect(formatAge(t, 3)).toBe('just now')
  })

  it('a slightly negative age (clocks not yet aligned) stays "just now", never a negative age', () => {
    expect(formatAge(t, -2)).toBe('just now')
  })

  it('seconds from 5 to 59', () => {
    expect(formatAge(t, 42)).toBe('42 s ago')
    expect(formatAge(t, 59.9)).toBe('59 s ago')
  })

  it('minutes from 1 to 59', () => {
    expect(formatAge(t, 60)).toBe('1 min ago')
    expect(formatAge(t, 3599)).toBe('59 min ago')
  })

  it('hours from 1 to 23', () => {
    expect(formatAge(t, 3600)).toBe('1 h ago')
    expect(formatAge(t, 86399)).toBe('23 h ago')
  })

  it('days beyond 24 h', () => {
    expect(formatAge(t, 86400)).toBe('1 d ago')
    expect(formatAge(t, 86400 * 12)).toBe('12 d ago')
  })

  it('a non-finite value renders an empty string, not NaN on screen', () => {
    expect(formatAge(t, Number.NaN)).toBe('')
    expect(formatAge(t, Number.POSITIVE_INFINITY)).toBe('')
  })
})

describe('flagEmoji: a real flag or nothing', () => {
  it('a valid ISO2 code renders the country flag', () => {
    expect(flagEmoji('JP')).toBe('🇯🇵')
    expect(flagEmoji('ID')).toBe('🇮🇩')
  })

  it('case does not change the country', () => {
    expect(flagEmoji('fr')).toBe('🇫🇷')
  })

  it('no code = no flag, never an approximate flag', () => {
    expect(flagEmoji(null)).toBeNull()
    expect(flagEmoji('')).toBeNull()
  })

  it('an invalid length renders null rather than a wrong flag', () => {
    expect(flagEmoji('F')).toBeNull()
    expect(flagEmoji('FRA')).toBeNull()
  })

  // Observation (not fixed here, see the report): two NON-alphabetic
  // characters ('12', '??') pass the only existing check (length 2) and
  // produce code points outside the regional-indicator range, hence a stray
  // character instead of null. Since the server only sends ISO2 or null, the
  // case is theoretical; it is documented without being carved into a test.
})

describe('badge: the left-hand badge', () => {
  it('an event with a magnitude shows the magnitude and its type in uppercase', () => {
    expect(badge(makeEvent({ magnitude: 5.83, mag_type: 'mw' }))).toEqual({
      value: '5.8',
      unit: 'MW',
    })
  })

  it('a magnitude without a type falls back to the generic MAG unit', () => {
    expect(badge(makeEvent({ magnitude: 4.5, mag_type: null }))).toEqual({
      value: '4.5',
      unit: 'MAG',
    })
  })

  it('an event without a magnitude shows the pictogram of its hazard type', () => {
    expect(badge(makeEvent({ kind: 'flood', magnitude: null, mag_type: null }))).toEqual({
      value: '💧',
      unit: '',
    })
  })
})
