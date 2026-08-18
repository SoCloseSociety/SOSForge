/** Forecast tracks are the only lines on this map that show the future. Drawing
 * one wrong would put a storm where it is not going. */
import { describe, expect, it } from 'vitest'
import { forecastTracks } from '../tracks'
import { makeEvent } from './helpers'

const TRACK = [
  { tau: 0, lat: 20.4, lon: -163.4, wind_kt: 70, category: 1 },
  { tau: 24, lat: 21.6, lon: -166.2, wind_kt: 60, category: 0 },
  { tau: 48, lat: 23.3, lon: -171.6, wind_kt: 85, category: 2 },
]

describe('drawing a forecast', () => {
  it('draws one line through the forecast positions, plus a point at each', () => {
    const storm = makeEvent({ id: 'nhc:cp012026', kind: 'cyclone', forecast_track: TRACK })
    const fc = forecastTracks([storm])

    const lines = fc.features.filter((f) => f.geometry.type === 'LineString')
    const points = fc.features.filter((f) => f.geometry.type === 'Point')
    expect(lines).toHaveLength(1)
    expect(points).toHaveLength(3)
    expect((lines[0].geometry as GeoJSON.LineString).coordinates[0]).toEqual([-163.4, 20.4])
  })

  it('keeps the lead time on each point: a position without its hour says nothing', () => {
    const storm = makeEvent({ id: 's', kind: 'cyclone', forecast_track: TRACK })
    const points = forecastTracks([storm]).features.filter((f) => f.geometry.type === 'Point')
    expect(points.map((p) => p.properties!.tau)).toEqual([0, 24, 48])
  })
})

describe('what it refuses to draw', () => {
  it('a storm with no forecast', () => {
    expect(forecastTracks([makeEvent({ id: 's', kind: 'cyclone' })]).features).toHaveLength(0)
  })

  it('a single point: a track needs two positions to be a track', () => {
    const storm = makeEvent({ id: 's', kind: 'cyclone', forecast_track: [TRACK[0]] })
    expect(forecastTracks([storm]).features).toHaveLength(0)
  })

  it('points missing coordinates', () => {
    const broken = makeEvent({
      id: 's',
      kind: 'cyclone',
      forecast_track: [{ tau: 0 }, { tau: 24 }],
    })
    expect(forecastTracks([broken]).features).toHaveLength(0)
  })

  it('anything that is not a cyclone', () => {
    const quake = makeEvent({ id: 'q', kind: 'earthquake', forecast_track: TRACK })
    expect(forecastTracks([quake]).features).toHaveLength(0)
  })
})
