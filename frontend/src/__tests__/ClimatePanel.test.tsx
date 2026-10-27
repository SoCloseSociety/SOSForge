/** The climate panel shows what `/api/climate` returned, and nothing else.
 *
 * The owner's rule is "rien doit etre fake": no placeholder number, no
 * default value, no fetch time dressed as the data's date. Each test below
 * guards one way the panel could lie: a value without the date of its own
 * dataset, a late or preliminary value said by colour only, a dead source
 * hidden behind its last number, a failed request drawn as zeros.
 *
 * Fixtures are real. `LIVE` is the response of the running backend captured
 * on 2026-10-27 at 12:56 UTC, verbatim (after the PR #5 review fixes: the
 * ENSO synopsis is the synopsis, the Antarctic extent is a lower bound with no
 * trend, every baseline names its `kind`). `NEVER` and `KEPT` are the exact
 * output of the backend's own serializer (`ClimateMonitor.snapshot`) after a
 * 503 before any success, and after a success then a 500 -- the two failure
 * paths of backend/tests/test_climate.py, with httpx's real error text.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CLIMATE_REFRESH_MS,
  ClimatePanel,
  type ClimateIndicator,
  type ClimateResponse,
} from '../components/ClimatePanel'
import { LANGS, translate } from '../i18n'
import { useStore } from '../store'
import { resetStore } from './helpers'

const LIVE: ClimateResponse = JSON.parse(String.raw`{"server_time":"2026-10-27T12:56:15.815714+00:00","indicators":[{"id":"global_air_temperature","label":"Global surface air temperature","available":true,"value":15.054,"unit":"°C","as_of":"2026-10-25","status":"preliminary","anomaly":0.949,"baseline":{"kind":"day_1991_2020","label":"1991-2020 average for this day","value":14.106},"previous":{"as_of":"2026-10-24","value":15.167,"anomaly":1.024},"trend":"down","lower_bound":false,"stale":false,"max_age_days":7,"detail":{},"source":{"name":"Copernicus Climate Change Service (C3S) / ECMWF, Climate Pulse (ERA5)","url":"https://pulse.climate.copernicus.eu/","data_url":"https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_2t_global.csv","licence":"CC BY 4.0 -- generated using Copernicus Climate Change Service information"},"state":{"last_ok":"2026-10-27T12:55:27.833214+00:00","last_attempt":"2026-10-27T12:55:23.100923+00:00","last_error":null,"errors":0,"poll_seconds":10800.0}},{"id":"sea_surface_temperature","label":"Sea surface temperature (60°S-60°N)","available":true,"value":20.902,"unit":"°C","as_of":"2026-10-25","status":"final","anomaly":0.733,"baseline":{"kind":"day_1991_2020","label":"1991-2020 average for this day","value":20.169},"previous":{"as_of":"2026-10-24","value":20.903,"anomaly":0.731},"trend":"up","lower_bound":false,"stale":false,"max_age_days":7,"detail":{},"source":{"name":"Copernicus Climate Change Service (C3S) / ECMWF, Climate Pulse (ERA5)","url":"https://pulse.climate.copernicus.eu/","data_url":"https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_sst_60S-60N_ocean.csv","licence":"CC BY 4.0 -- generated using Copernicus Climate Change Service information"},"state":{"last_ok":"2026-10-27T12:55:28.833390+00:00","last_attempt":"2026-10-27T12:55:27.833220+00:00","last_error":null,"errors":0,"poll_seconds":10800.0}},{"id":"co2_mauna_loa","label":"CO₂ at Mauna Loa (daily)","available":true,"value":427.18,"unit":"ppm","as_of":"2026-10-26","status":null,"anomaly":2.26,"baseline":{"kind":"day_year_earlier","label":"same day a year earlier","as_of":"2025-10-26","value":424.92},"previous":{"as_of":"2026-10-25","value":427.13},"trend":"up","lower_bound":false,"stale":false,"max_age_days":7,"detail":{},"source":{"name":"NOAA Global Monitoring Laboratory","url":"https://gml.noaa.gov/ccgg/trends/","data_url":"https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_daily_mlo.csv","licence":"freely available, credit NOAA GML"},"state":{"last_ok":"2026-10-27T12:55:32.538058+00:00","last_attempt":"2026-10-27T12:55:28.833396+00:00","last_error":null,"errors":0,"poll_seconds":21600.0}},{"id":"arctic_sea_ice_extent","label":"Arctic sea ice extent","available":true,"value":6.717,"unit":"million km²","as_of":"2026-10-17","status":null,"anomaly":-0.852,"baseline":{"kind":"day_1991_2020","label":"1991-2020 average for this day","value":7.569,"years":30},"previous":{"as_of":"2026-10-16","value":6.731,"missing_million_km2":0.0},"trend":"down","lower_bound":false,"stale":true,"max_age_days":4,"detail":{"missing_million_km2":0.0},"source":{"name":"NSIDC Sea Ice Index, version 4","url":"https://nsidc.org/sea-ice-today","data_url":"https://noaadata.apps.nsidc.org/NOAA/G02135/north/daily/data/N_seaice_extent_daily_v4.0.csv","licence":"free use with citation (Fetterer et al., NSIDC G02135)"},"state":{"last_ok":"2026-10-27T12:55:38.139544+00:00","last_attempt":"2026-10-27T12:55:32.538062+00:00","last_error":null,"errors":0,"poll_seconds":21600.0}},{"id":"antarctic_sea_ice_extent","label":"Antarctic sea ice extent","available":true,"value":16.204,"unit":"million km²","as_of":"2026-10-17","status":null,"anomaly":-2.059,"baseline":{"kind":"day_1991_2020","label":"1991-2020 average for this day","value":18.263,"years":30},"previous":{"as_of":"2026-10-16","value":16.225,"missing_million_km2":0.0},"trend":null,"lower_bound":true,"stale":true,"max_age_days":4,"detail":{"missing_million_km2":0.382},"source":{"name":"NSIDC Sea Ice Index, version 4","url":"https://nsidc.org/sea-ice-today","data_url":"https://noaadata.apps.nsidc.org/NOAA/G02135/south/daily/data/S_seaice_extent_daily_v4.0.csv","licence":"free use with citation (Fetterer et al., NSIDC G02135)"},"state":{"last_ok":"2026-10-27T12:55:39.563175+00:00","last_attempt":"2026-10-27T12:55:38.139553+00:00","last_error":null,"errors":0,"poll_seconds":21600.0}},{"id":"nino34_weekly","label":"Niño 3.4 sea surface temperature (weekly)","available":true,"value":30.1,"unit":"°C","as_of":"2026-10-21","status":null,"anomaly":3.4,"baseline":{"kind":"week_1991_2020","label":"1991-2020 weekly average","value":26.7},"previous":{"as_of":"2026-10-14","value":30.0,"anomaly":3.3},"trend":"up","lower_bound":false,"stale":false,"max_age_days":14,"detail":{"week":"centred on as_of"},"source":{"name":"NOAA Climate Prediction Center","url":"https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/","data_url":"https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for","licence":"public domain (US government work)"},"state":{"last_ok":"2026-10-27T12:55:45.431491+00:00","last_attempt":"2026-10-27T12:55:39.563180+00:00","last_error":null,"errors":0,"poll_seconds":43200.0}},{"id":"enso_status","label":"ENSO alert status","available":true,"value":"El Niño Advisory","unit":null,"as_of":"2026-10-08","status":null,"anomaly":null,"baseline":null,"previous":null,"trend":null,"lower_bound":false,"stale":false,"max_age_days":40,"detail":{"synopsis":"El Niño continues to strengthen, with a strong-to-very strong El Niño likely through January-March 2027 (remaining greater than an 83% chance).","next_issue":"2026-11-12"},"source":{"name":"NOAA Climate Prediction Center, ENSO diagnostic discussion","url":"https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml","data_url":"https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml","licence":"public domain (US government work)"},"state":{"last_ok":"2026-10-27T12:55:46.730519+00:00","last_attempt":"2026-10-27T12:55:45.431496+00:00","last_error":null,"errors":0,"poll_seconds":43200.0}}]}`)

const NEVER: ClimateIndicator = JSON.parse(String.raw`{"id":"global_air_temperature","label":"Global surface air temperature","available":false,"value":null,"unit":null,"as_of":null,"status":null,"anomaly":null,"baseline":null,"previous":null,"trend":null,"stale":false,"max_age_days":7,"detail":{},"source":{"name":"Copernicus Climate Change Service (C3S) / ECMWF, Climate Pulse (ERA5)","url":"https://pulse.climate.copernicus.eu/","data_url":"https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_2t_global.csv","licence":"CC BY 4.0 -- generated using Copernicus Climate Change Service information"},"state":{"last_ok":null,"last_attempt":"2026-10-27T10:02:20+00:00","last_error":"HTTPStatusError: Server error '503 Service Unavailable' for url 'https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_2t_global.csv'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503","errors":1,"poll_seconds":10800.0}}`)

const KEPT: ClimateIndicator = JSON.parse(String.raw`{"id":"global_air_temperature","label":"Global surface air temperature","available":false,"value":15.054,"unit":"°C","as_of":"2026-10-25","status":"preliminary","anomaly":0.949,"baseline":{"label":"1991-2020 average for this day","value":14.106},"previous":{"as_of":"2026-10-24","value":15.167,"anomaly":1.024},"trend":"down","stale":false,"max_age_days":7,"detail":{},"source":{"name":"Copernicus Climate Change Service (C3S) / ECMWF, Climate Pulse (ERA5)","url":"https://pulse.climate.copernicus.eu/","data_url":"https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_2t_global.csv","licence":"CC BY 4.0 -- generated using Copernicus Climate Change Service information"},"state":{"last_ok":"2026-10-27T10:13:49.571610+00:00","last_attempt":"2026-10-27T09:30:00+00:00","last_error":"HTTPStatusError: Server error '500 Internal Server Error' for url 'https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_2t_global.csv'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500","errors":1,"poll_seconds":10800.0}}`)

const en = (key: string, vars?: Record<string, string | number>) => translate('en', key, vars)

/** A fetch that answers at once with the given payload. */
const answering = (body: ClimateResponse) => vi.fn(async () => body)

/** A fetch that never answers: the panel stays on what it was given. */
const silent = () => vi.fn(() => new Promise<ClimateResponse>(() => {}))

/** Lets the fetch promise settle and React commit what it set. */
async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

/** The card of one indicator, found by its visible name. */
function card(container: HTMLElement, id: string): HTMLElement {
  const items = [...container.querySelectorAll<HTMLElement>('.climate-list > li')]
  const name = en(`climate.ind.${id}`)
  const found = items.find((item) => within(item).queryByRole('heading', { name }))
  if (!found) throw new Error(`no card for ${id}`)
  return found
}

beforeEach(() => {
  resetStore()
  useStore.getState().setLang('en')
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('the live payload', () => {
  it('renders every indicator with the date of its own dataset and its linked source', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    expect(screen.getByRole('heading', { level: 2, name: en('climate.title') })).toBeInTheDocument()
    const items = container.querySelectorAll('.climate-list > li')
    expect(items).toHaveLength(LIVE.indicators.length)
    expect(LIVE.indicators).toHaveLength(7)

    for (const indicator of LIVE.indicators) {
      const item = card(container, indicator.id)
      // the dataset's date, machine-readable, next to the value
      const stamp = item.querySelector(`.climate-asof time[datetime="${indicator.as_of}"]`)
      expect(stamp, `${indicator.id} as_of`).not.toBeNull()
      // the source, visible and linked to the agency's page
      const link = within(item).getByRole('link', { name: indicator.source.name })
      expect(link).toHaveAttribute('href', indicator.source.url)
      expect(link).toHaveAttribute('target', '_blank')
      expect(link.getAttribute('rel')?.split(' ').sort()).toEqual(['noopener', 'noreferrer'])
    }
  })

  it('shows the values the API sent, with the anomaly signed explicitly', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const air = card(container, 'global_air_temperature')
    expect(within(air).getByText('15.054')).toBeInTheDocument()
    expect(within(air).getByText('+0.95 °C vs baseline')).toBeInTheDocument()

    const arctic = card(container, 'arctic_sea_ice_extent')
    expect(within(arctic).getByText('6.717')).toBeInTheDocument()
    expect(within(arctic).getByText('-0.85 million km² vs baseline')).toBeInTheDocument()

    const enso = card(container, 'enso_status')
    expect(within(enso).getByText('El Niño Advisory')).toBeInTheDocument()
  })

  it('gives every trend arrow words, never the glyph alone', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const arctic = card(container, 'arctic_sea_ice_extent')
    const trend = arctic.querySelector('.climate-trend')
    expect(trend?.querySelector('[aria-hidden="true"]')?.textContent).toBe('↓')
    expect(trend).toHaveTextContent('falling since Oct 16, 2026')

    const co2 = card(container, 'co2_mauna_loa')
    expect(co2.querySelector('.climate-trend')).toHaveTextContent('rising since Oct 25, 2026')
  })

  it('formats numbers in the active language', async () => {
    useStore.getState().setLang('fr')
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const items = [...container.querySelectorAll<HTMLElement>('.climate-list > li')]
    const air = items.find((item) =>
      within(item).queryByRole('heading', {
        name: translate('fr', 'climate.ind.global_air_temperature'),
      }),
    )!
    expect(within(air).getByText('15,054')).toBeInTheDocument()
  })
})

describe('freshness said in words', () => {
  it('marks a preliminary value and a late dataset as text', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    // Copernicus: the last days are PRELIMINARY in the CSV itself
    const air = card(container, 'global_air_temperature')
    expect(within(air).getByText(en('climate.preliminary'))).toBeInTheDocument()

    // NSIDC stopped at 2026-10-17, ten days before the capture, max_age_days 4
    const arctic = card(container, 'arctic_sea_ice_extent')
    expect(within(arctic).getByText(en('climate.stale', { n: '4 days' }))).toBeInTheDocument()
    const antarctic = card(container, 'antarctic_sea_ice_extent')
    expect(within(antarctic).getByText(en('climate.stale', { n: '4 days' }))).toBeInTheDocument()

    // a final, on-time value carries neither
    const sst = card(container, 'sea_surface_temperature')
    expect(sst.querySelector('.climate-flags')).toBeNull()
  })
})

describe('an unavailable indicator', () => {
  it('shows the server error, and no value it never had', async () => {
    const payload: ClimateResponse = { server_time: LIVE.server_time, indicators: [NEVER] }
    const { container } = render(<ClimatePanel fetchClimate={answering(payload)} />)
    await flush()

    const item = card(container, 'global_air_temperature')
    expect(item).toHaveTextContent(en('climate.unavailable'))
    expect(item).toHaveTextContent(
      "HTTPStatusError: Server error '503 Service Unavailable' for url",
    )
    expect(item.querySelector('.climate-value')).toBeNull()
    expect(item.querySelector('.climate-asof')).toBeNull()
  })

  it('keeps a value it still has, greyed, with its own date AND the error', async () => {
    const payload: ClimateResponse = { server_time: LIVE.server_time, indicators: [KEPT] }
    const { container } = render(<ClimatePanel fetchClimate={answering(payload)} />)
    await flush()

    const item = card(container, 'global_air_temperature')
    expect(item.querySelector('.climate-reading.is-kept .climate-value')?.textContent).toBe(
      '15.054',
    )
    expect(item.querySelector('.climate-asof time[datetime="2026-10-25"]')).not.toBeNull()
    expect(item).toHaveTextContent(en('climate.kept'))
    expect(item).toHaveTextContent("Server error '500 Internal Server Error'")
  })
})

describe('the request itself', () => {
  it('says it is loading before the first response, and shows no number', () => {
    const { container } = render(<ClimatePanel fetchClimate={silent()} />)

    expect(screen.getByRole('status')).toHaveTextContent(en('climate.loading'))
    expect(container.textContent).not.toMatch(/\d/)
  })

  it('shows the real error of a failed fetch, and no number at all', async () => {
    // what Chrome rejects with when the API is unreachable
    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    const { container } = render(<ClimatePanel fetchClimate={failing} />)
    await flush()

    expect(screen.getByText(en('climate.error'))).toBeInTheDocument()
    expect(screen.getByText('TypeError: Failed to fetch')).toBeInTheDocument()
    expect(container.querySelector('.climate-list')).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
    expect(container.textContent).not.toMatch(/\d/)
  })

  it('refuses a payload that is not the climate contract', async () => {
    const wrong = vi.fn(async () => ({ detail: 'Not Found' }) as unknown as ClimateResponse)
    const { container } = render(<ClimatePanel fetchClimate={wrong} />)
    await flush()

    expect(screen.getByText(en('climate.error'))).toBeInTheDocument()
    expect(container.querySelector('.climate-list')).toBeNull()
  })

  it('draws initial data at once, then fetches on mount and every ten minutes until unmount', async () => {
    vi.useFakeTimers()
    const fetcher = answering(LIVE)
    const { container, unmount } = render(
      <ClimatePanel initialData={LIVE} fetchClimate={fetcher} />,
    )
    expect(container.querySelectorAll('.climate-list > li')).toHaveLength(7)
    expect(screen.queryByRole('status')).toBeNull()
    expect(fetcher).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(CLIMATE_REFRESH_MS - 1)
    })
    expect(fetcher).toHaveBeenCalledTimes(1)
    await act(async () => {
      vi.advanceTimersByTime(1)
    })
    expect(fetcher).toHaveBeenCalledTimes(2)

    unmount()
    await act(async () => {
      vi.advanceTimersByTime(CLIMATE_REFRESH_MS * 3)
    })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('aborts the request in flight on unmount', () => {
    const fetcher = silent()
    const { unmount } = render(<ClimatePanel fetchClimate={fetcher} />)
    const signal = (fetcher.mock.calls[0] as unknown as [AbortSignal])[0]
    expect(signal.aborted).toBe(false)
    unmount()
    expect(signal.aborted).toBe(true)
  })

  it('keeps the last indicators when a refresh fails, and says so with the error', async () => {
    vi.useFakeTimers()
    const fetcher = vi
      .fn<(signal: AbortSignal) => Promise<ClimateResponse>>()
      .mockResolvedValueOnce(LIVE)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const { container } = render(<ClimatePanel fetchClimate={fetcher} />)
    await flush()
    expect(container.querySelectorAll('.climate-list > li')).toHaveLength(7)

    await act(async () => {
      vi.advanceTimersByTime(CLIMATE_REFRESH_MS)
    })
    await flush()

    const banner = container.querySelector('.climate-fetch-error')
    expect(banner).toHaveTextContent('Refresh failed.')
    expect(banner).toHaveTextContent('TypeError: Failed to fetch')
    // the time quoted is the server's, from the last good payload
    expect(banner?.querySelector(`time[datetime="${LIVE.server_time}"]`)).not.toBeNull()
    expect(container.querySelectorAll('.climate-list > li')).toHaveLength(7)
  })
})

describe('show more', () => {
  it('toggles aria-expanded and reveals the long detail', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const enso = card(container, 'enso_status')
    const button = within(enso).getByRole('button', { name: en('more.show') })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    const detail = document.getElementById(button.getAttribute('aria-controls')!)!
    expect(detail).not.toBeVisible()
    const synopsis = within(enso).getByText(/El Niño continues to strengthen/, {
      selector: '.climate-synopsis',
    })
    expect(synopsis).not.toBeVisible()

    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(button).toHaveTextContent(en('more.hide'))
    expect(detail).toBeVisible()
    expect(synopsis).toBeVisible()
    expect(within(detail).getByText(en('climate.detail.next'))).toBeVisible()
    expect(detail.querySelector('time[datetime="2026-11-12"]')).not.toBeNull()
    expect(within(detail).getByText('public domain (US government work)')).toBeVisible()
    // our read of the file is labelled as OUR read, never as the data's date
    expect(within(detail).getByText(en('climate.detail.lastok'))).toBeVisible()
    expect(detail.querySelector('time[datetime="2026-10-27T12:55:46.730519+00:00"]')).not.toBeNull()

    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(detail).not.toBeVisible()
  })

  it('shows the baseline, the previous reading and the data file once opened', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const co2 = card(container, 'co2_mauna_loa')
    fireEvent.click(within(co2).getByRole('button', { name: en('more.show') }))
    expect(co2).toHaveTextContent('same day a year earlier: 424.92 ppm (Oct 26, 2025)')
    expect(co2).toHaveTextContent('427.13 ppm on Oct 25, 2026')
    const file = within(co2).getByRole('link', {
      name: 'https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_daily_mlo.csv',
    })
    expect(file).toHaveAttribute('rel', 'noopener noreferrer')

    const antarctic = card(container, 'antarctic_sea_ice_extent')
    fireEvent.click(within(antarctic).getByRole('button', { name: en('more.show') }))
    expect(antarctic).toHaveTextContent(
      `0.382 million km²: ${en('climate.detail.missing.lower')}`,
    )
  })
})

describe('the PR #5 review findings', () => {
  it('shows a lower bound on the card itself, with no trend it cannot know', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    // 16.204 with 0.382 million km² of the area missing: a minimum, said
    // before "Show more", and no "falling" for a 0.021 change inside it
    const antarctic = card(container, 'antarctic_sea_ice_extent')
    const flag = antarctic.querySelector('.climate-flag.is-lower-bound')
    expect(flag).not.toBeNull()
    expect(flag).toBeVisible()
    expect(flag).toHaveTextContent(en('climate.lowerbound', { amount: '0.382 million km²' }))
    expect(antarctic.querySelector('.climate-trend')).toBeNull()

    // nothing missing in the north: no flag, the trend stays
    const arctic = card(container, 'arctic_sea_ice_extent')
    expect(arctic.querySelector('.climate-flag.is-lower-bound')).toBeNull()
    expect(arctic.querySelector('.climate-trend')).not.toBeNull()
  })

  it('shows the ENSO synopsis, not the whole discussion', async () => {
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const enso = card(container, 'enso_status')
    const synopsis = enso.querySelector('.climate-synopsis')
    expect(synopsis?.textContent).toBe(
      'El Niño continues to strengthen, with a strong-to-very strong El Niño likely through January-March 2027 (remaining greater than an 83% chance).',
    )
    expect(enso).not.toHaveTextContent('ncep.list')
  })

  it('translates the baseline description', async () => {
    useStore.getState().setLang('fr')
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const items = [...container.querySelectorAll<HTMLElement>('.climate-list > li')]
    const co2 = items.find((item) =>
      within(item).queryByRole('heading', { name: translate('fr', 'climate.ind.co2_mauna_loa') }),
    )!
    expect(co2).toHaveTextContent(translate('fr', 'climate.baseline.day_year_earlier'))
    expect(co2).not.toHaveTextContent('same day a year earlier')
  })
})

describe('the recheck of 0cc11f6', () => {
  // "untuk hari ini" said TODAY next to an Antarctic reading of Oct 17; the
  // baseline is the reading's own calendar day, in every language
  it('never calls the baseline day "today"', () => {
    const today = /today|aujourd|hoy|hari ini|今日|本日|ce jour/i
    for (const { code } of LANGS) {
      expect(translate(code, 'climate.baseline.day_1991_2020')).not.toMatch(today)
    }
  })

  it('shows the stale Antarctic reading against its own day, in Indonesian', async () => {
    useStore.getState().setLang('id')
    const { container } = render(<ClimatePanel fetchClimate={answering(LIVE)} />)
    await flush()

    const items = [...container.querySelectorAll<HTMLElement>('.climate-list > li')]
    const antarctic = items.find((item) =>
      within(item).queryByRole('heading', {
        name: translate('id', 'climate.ind.antarctic_sea_ice_extent'),
      }),
    )!
    expect(antarctic).toHaveTextContent('rata-rata 1991-2020 untuk tanggal ini')
    expect(antarctic).not.toHaveTextContent('hari ini')
  })
})
