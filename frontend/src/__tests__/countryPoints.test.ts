/**
 * The page's country rule and the place report's port of it
 * (backend/app/place/geo.py) must agree: both suites assert the same points,
 * read from the backend's fixture.
 */
import { describe, expect, it } from 'vitest'

import { countriesIn, decode, type CountryTable } from '../countries'
import table from '../data/countries.json'

const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as {
  readFileSync(path: string, encoding: string): string
}
const root = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
const { points } = JSON.parse(
  fs.readFileSync(`${root}/../backend/tests/fixtures/place/country_points.json`, 'utf8'),
) as { points: { name: string; lat: number; lon: number; codes: string[] }[] }

const shapes = decode(table as CountryTable)

describe('the shared country points', () => {
  it('holds the cases the rule turns on', () => {
    expect(points.map((p) => p.name)).toEqual(
      expect.arrayContaining([
        'Ko Tao',
        'Maseru',
        'Saint-Denis, Reunion',
        'mid-Pacific',
        'Clipperton',
        'off Clipperton',
        'Bouvet',
      ]),
    )
  })

  it.each(points)('$name', (point) => {
    expect(countriesIn(shapes, point.lat, point.lon)).toEqual(point.codes)
  })
})
