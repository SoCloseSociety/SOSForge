/** Clipperton and Bouvet (adversarial review of the place report): the
 * 1:10m table draws them, and Natural Earth files them under FR and NO.
 * Under those codes Near me counted every France-wide Meteoalarm warning --
 * the metropolitan departments, 10,700 km away -- for a reader at
 * Clipperton, and Norway's for one at Bouvet. They have their own codes now
 * (CP, BV), and the geocoder's own answer for them (FR, NO, measured on
 * /api/geocode) no longer brings the parent back. */
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { countriesAt, countriesIn, decode, type CountryTable } from '../countries'
import table from '../data/countries.json'
import { countryName } from '../format'
import { LANGS } from '../i18n'
import { splitByScope } from '../scope'
import { useCountries } from '../useCountries'
import { makeEvent } from './helpers'

const shapes = decode(table as CountryTable)
const CLIPPERTON = { lat: 10.29, lon: -109.22 }
const BOUVET = { lat: -54.42, lon: 3.36 }

// the orange Brittany storm warning of 2026-10-22 (the backend test reads it
// from the verbatim Meteoalarm France feed), as /api/events serves it: no
// position, the country only
const BRITTANY = makeEvent({
  id: 'meteoalarm:2.49.0.0.250.0.FR.20261022160032.170031',
  source: 'meteoalarm',
  kind: 'storm',
  severity: 'moderate',
  place: "Côtes d'Armor, Finistère, Morbihan",
  title: "Côtes d'Armor, Finistère, Morbihan",
  country: 'france',
  country_code: 'FR',
  lat: null,
  lon: null,
  depth_km: null,
  magnitude: null,
  mag_type: null,
  region: null,
  ongoing: true,
})

describe('Clipperton and Bouvet have their own codes', () => {
  it('the table names CP and BV, not FR and NO, on the island and off it', () => {
    expect(countriesIn(shapes, CLIPPERTON.lat, CLIPPERTON.lon)).toEqual(['CP'])
    expect(countriesIn(shapes, 10.29, -108.5)).toEqual(['CP']) // 77 km off: the nearest country
    expect(countriesIn(shapes, BOUVET.lat, BOUVET.lon)).toEqual(['BV'])
  })

  it('a France-wide warning with no position is not matched at Clipperton', () => {
    const at = splitByScope([BRITTANY], CLIPPERTON, countriesIn(shapes, CLIPPERTON.lat, CLIPPERTON.lon))
    expect(at.near).toEqual([])
    const paris = splitByScope([BRITTANY], { lat: 48.86, lon: 2.35 }, countriesIn(shapes, 48.86, 2.35))
    expect(paris.near.map((e) => e.id)).toEqual([BRITTANY.id])
  })

  it("the geocoder's FR and NO for them do not bring the parent's warnings back", async () => {
    await countriesAt(0, 0) // the table is loaded
    const clipperton = renderHook(() => useCountries(CLIPPERTON, 'FR'))
    const bouvet = renderHook(() => useCountries(BOUVET, 'NO'))
    await act(async () => {})
    expect(clipperton.result.current).toEqual(['CP'])
    expect(bouvet.result.current).toEqual(['BV'])
    expect(splitByScope([BRITTANY], CLIPPERTON, clipperton.result.current).near).toEqual([])
  })

  it('their names render in every language of the page', () => {
    for (const { code } of LANGS) {
      for (const iso2 of ['CP', 'BV']) {
        const name = countryName(code, iso2)
        expect(name).toBeTruthy()
        expect(name).not.toBe(iso2)
      }
    }
    expect(countryName('en', 'CP')).toBe('Clipperton Island')
    expect(countryName('fr', 'BV')).toBe('Île Bouvet')
  })
})
