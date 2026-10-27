/** The place report shows what `/api/place` returned, and nothing else.
 *
 * Owner rule: "rien ne doit être fake". Each test below guards one way the
 * panel could lie: a number drawn for a block that has none, a stale reading
 * shown as current because the browser clock or a forgotten tab says so, a
 * late answer for another place painted over this one, a source hidden
 * behind a fold.
 *
 * Fixtures. There is no `/api/place` to capture yet (the backend is written
 * in parallel), so `fixtures/place_built_*.json` are NOT dev-server bodies.
 * They follow the contract's shape and every value in them was read or
 * computed from a verbatim upstream capture of 2026-10-27 by the scratch
 * builder named in their `_built_from` key (MET Norway, AWC METAR, Open-Meteo
 * S3 cassettes, CoastWatch, GDO, WRI Aqueduct, INFORM, USGS, NCEI, GVP, and
 * SOSForge's own zoned /api/events). A block with no capture for its place
 * is `unavailable` / `budget`, the contract's cold-block state, and carries
 * no number. They are to be replaced by real bodies once the backend runs.
 * `metno_weathericons_legend.csv` is MET Norway's legend, verbatim (the
 * `weathericon/2.0/legends` endpoint answers 404; the locationforecast
 * documentation links this file instead).
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  PLACE_BLOCK_IDS,
  PLACE_PARTIAL_RETRIES,
  PLACE_REFRESH_MS,
  PLACE_TICK_MS,
  PlaceReport,
  fetchPlace,
  isPlaceResponse,
  roundCoord,
  skyLabel,
  type PlaceQuery,
  type PlaceResponse,
} from '../components/PlaceReport'
import { translate } from '../i18n'
import { useStore } from '../store'
import samuiJson from './fixtures/place_built_kosamui.json'
import samuiFrJson from './fixtures/place_built_kosamui_fr.json'
import parisJson from './fixtures/place_built_paris.json'
import midpacificJson from './fixtures/place_built_midpacific.json'
import legend from './fixtures/metno_weathericons_legend.csv?raw'
import { resetStore } from './helpers'

const SAMUI = samuiJson as unknown as PlaceResponse
const SAMUI_FR = samuiFrJson as unknown as PlaceResponse
const PARIS = parisJson as unknown as PlaceResponse
const MIDPACIFIC = midpacificJson as unknown as PlaceResponse

const en = (key: string, vars?: Record<string, string | number>) => translate('en', key, vars)

/** A copy, so no test can leak a mutation into another. */
const copy = (body: PlaceResponse): PlaceResponse => structuredClone(body)

/** A fetch that answers at once with the given payload. */
const answering = (body: PlaceResponse) =>
  vi.fn<(query: PlaceQuery, signal: AbortSignal) => Promise<PlaceResponse>>(async () => copy(body))

/** Answers the first call, then never again: a panel left open whose
 * refetches do not come back. */
const onceThenSilent = (body: PlaceResponse) =>
  vi
    .fn<(query: PlaceQuery, signal: AbortSignal) => Promise<PlaceResponse>>()
    .mockResolvedValueOnce(copy(body))
    .mockImplementation(() => new Promise<PlaceResponse>(() => {}))

/** A fetch that never answers. */
const silent = () =>
  vi.fn<(query: PlaceQuery, signal: AbortSignal) => Promise<PlaceResponse>>(
    () => new Promise<PlaceResponse>(() => {}),
  )

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

function section(container: HTMLElement, id: string): HTMLElement {
  const found = container.querySelector<HTMLElement>(`[data-section="${id}"]`)
  if (!found) throw new Error(`no section ${id}`)
  return found
}

function rows(container: HTMLElement, id: string): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(`[data-block="${id}"]`)]
}

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

const SAMUI_PROPS = { lat: 9.512, lon: 100.013, name: 'Ko Samui' }
const PARIS_PROPS = { lat: 48.857, lon: 2.352, name: 'Paris' }
const MIDPACIFIC_PROPS = { lat: 0, lon: -160, name: 'Mid-Pacific' }

beforeEach(() => {
  resetStore()
  useStore.getState().setLang('en')
  setVisibility('visible')
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('the fixtures themselves', () => {
  it('are full reports for the props the tests give', () => {
    for (const body of [SAMUI, SAMUI_FR, PARIS, MIDPACIFIC]) {
      expect(isPlaceResponse(body)).toBe(true)
      expect(Object.keys(body.blocks).sort()).toEqual([...PLACE_BLOCK_IDS].sort())
    }
    expect(SAMUI.query).toMatchObject({ lat: roundCoord(9.512), lon: roundCoord(100.013) })
    expect(PARIS.query).toMatchObject({ lat: roundCoord(48.857), lon: roundCoord(2.352) })
    expect(roundCoord(-0.001)).toBe(0)
    expect(Object.is(roundCoord(-0.001), -0)).toBe(false)
  })
})

describe('what a block says about itself', () => {
  it('an unavailable block never renders a digit', async () => {
    let checked = 0
    for (const [body, props] of [
      [SAMUI, SAMUI_PROPS],
      [PARIS, PARIS_PROPS],
      [MIDPACIFIC, MIDPACIFIC_PROPS],
    ] as const) {
      const { container, unmount } = render(<PlaceReport {...props} fetcher={answering(body)} />)
      await flush()
      for (const id of PLACE_BLOCK_IDS) {
        if (body.blocks[id].status !== 'unavailable') continue
        const found = rows(container, id)
        expect(found.length, `${id} has a row`).toBeGreaterThan(0)
        for (const row of found) {
          expect(row.textContent, id).toContain(en('place.unavailable', { message: body.blocks[id].error!.message }))
          expect(row.textContent, id).not.toMatch(/\d/)
          checked += 1
        }
      }
      unmount()
    }
    // nine at Ko Samui, fourteen in Paris, thirteen at sea: the test ran
    expect(checked).toBeGreaterThan(30)
  })

  it('a section whose blocks are all not applicable reads as one muted line', async () => {
    const { container } = render(<PlaceReport {...PARIS_PROPS} fetcher={answering(PARIS)} />)
    await flush()

    const sea = section(container, 'sea')
    const message = PARIS.blocks['sea.waves'].error!.message
    expect(message).toBe('Inland: the nearest ocean coastline is 150 km away.')
    const lines = sea.querySelectorAll('.place-na')
    expect(lines).toHaveLength(1)
    expect(lines[0]).toHaveTextContent(`○${en('place.na', { message })}`)
    expect(lines[0].querySelector('.state-glyph')).toHaveAttribute('aria-hidden', 'true')
    // no values, no fold: the line is the whole section, with its sources
    expect(sea.querySelector('[data-block]')).toBeNull()
    expect(within(sea).queryByRole('button')).toBeNull()
  })

  it('a not-applicable block inside a live section is one line too', async () => {
    const { container } = render(<PlaceReport {...MIDPACIFIC_PROPS} fetcher={answering(MIDPACIFIC)} />)
    await flush()

    const [drought] = rows(container, 'drought')
    expect(drought.dataset.status).toBe('not_applicable')
    // the dataset's name, then its one status line
    expect(drought.querySelector('.place-status-name')).toHaveTextContent(en('place.block.drought'))
    expect(drought.querySelectorAll('p')).toHaveLength(1)
    expect(drought).toHaveTextContent(
      en('place.na', { message: MIDPACIFIC.blocks.drought.error!.message }),
    )
  })

  it('a field the source does not publish is said in words, never 0', async () => {
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(SAMUI)} />)
    await flush()

    // MET Norway publishes no gust outside the Nordic area
    expect(SAMUI.blocks.wind.missing).toEqual([{ field: 'gust_ms', reason: 'not_in_source' }])
    const [wind] = rows(container, 'wind')
    expect(wind).toHaveTextContent(`${en('place.label.gust')}: ${en('place.missing')}`)
  })
})

describe('sources and provenance', () => {
  it('ends every section with its linked sources, visible without expanding', async () => {
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(SAMUI)} />)
    await flush()

    const sections = [...container.querySelectorAll<HTMLElement>('[data-section]')]
    expect(sections.map((s) => s.dataset.section)).toEqual([
      'alerts',
      'now',
      'today',
      'wind',
      'forecast',
      'sea',
      'air',
      'risk',
      'history',
    ])
    for (const s of sections) {
      const line = s.querySelector<HTMLElement>(':scope > .place-sources')
      expect(line, s.dataset.section).not.toBeNull()
      expect(line).toBeVisible()
      expect(s.lastElementChild).toBe(line)
    }
    const air = section(container, 'air')
    const link = within(air).getByRole('link', { name: 'CAMS global atmospheric composition via Open-Meteo' })
    expect(link).toBeVisible()
    expect(link).toHaveAttribute('href', 'https://open-meteo.com/')
    expect(link.getAttribute('rel')?.split(' ').sort()).toEqual(['noopener', 'noreferrer'])
  })

  it('keeps the raw reason and our own read time behind "Show more"', async () => {
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(SAMUI)} />)
    await flush()

    const sea = section(container, 'sea')
    const reason = SAMUI.blocks['sea.level'].error!.reason
    const shown = within(sea).getByText(`budget: ${reason}`)
    expect(shown).not.toBeVisible()

    const now = section(container, 'now')
    const read = now.querySelector<HTMLElement>('[data-detail-block="weather_now"] time.place-read')!
    expect(read.getAttribute('datetime')).toBe(SAMUI.blocks.weather_now.fetched_at)
    expect(read.textContent).toMatch(/^read by SOSForge .+ ago$/)
    expect(read).not.toBeVisible()
    // the raw METAR, the attribution and the licence too
    expect(within(now).getByText(SAMUI.blocks.weather_now.data!.raw as string)).not.toBeVisible()

    const button = within(sea).getByRole('button', { name: en('more.show') })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(shown).toBeVisible()
    fireEvent.click(within(now).getByRole('button', { name: en('more.show') }))
    expect(read).toBeVisible()
  })
})

describe('time, on the server clock', () => {
  it('computes ages from server_time, not from the browser clock', async () => {
    vi.useFakeTimers()
    // a browser clock years off: a skewed laptop, a dead CMOS battery
    vi.setSystemTime(new Date('2031-06-01T00:00:00Z'))
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={onceThenSilent(SAMUI)} />)
    await flush()

    // METAR VTSM 271300Z, report served at 13:27:30Z
    expect(SAMUI.blocks.weather_now.as_of).toBe('2026-10-27T13:00:00Z')
    expect(SAMUI.server_time).toBe('2026-10-27T13:27:30Z')
    const [now] = rows(container, 'weather_now')
    expect(now.querySelector('time.place-asof')).toHaveTextContent('observed 27 min ago')
    expect(now).not.toHaveTextContent(en('place.stale'))
    // the time elapsed since arrival is added, on the panel's own tick
    await act(async () => {
      vi.advanceTimersByTime(10 * 60 * 1000)
    })
    expect(now.querySelector('time.place-asof')).toHaveTextContent('observed 37 min ago')
  })

  it('turns a block Late on its own tick once its age passes max_age_s', async () => {
    vi.useFakeTimers()
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={onceThenSilent(SAMUI)} />)
    await flush()

    const block = SAMUI.blocks.weather_now
    expect(block.stale).toBe(false)
    expect(block.max_age_s).toBe(3 * 3600)
    const [now] = rows(container, 'weather_now')
    expect(now).not.toHaveTextContent(en('place.stale'))

    // 27.5 min old at arrival: 2 h 32 min later it is still within 3 h ...
    await act(async () => {
      vi.advanceTimersByTime((2 * 60 + 32) * 60 * 1000)
    })
    expect(now).not.toHaveTextContent(en('place.stale'))
    // ... and one tick past three hours it is Late, with a glyph and words
    await act(async () => {
      vi.advanceTimersByTime(PLACE_TICK_MS * 2)
    })
    const late = now.querySelector('.place-late')
    expect(late).toHaveTextContent(`▲${en('place.stale')}`)
    expect(late?.querySelector('.state-glyph')).toHaveAttribute('aria-hidden', 'true')
    // and the header stops counting it as up to date (with alerts_now, whose
    // five minutes ran out long before)
    expect(container.querySelector('.place-summary')).toHaveTextContent(en('place.summary.late', { n: 2 }))
  })

  it('says when alerts were checked, and turns Late after five minutes without a refetch', async () => {
    vi.useFakeTimers()
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={onceThenSilent(SAMUI)} />)
    await flush()

    const [alerts] = rows(container, 'alerts_now')
    expect(alerts).toHaveTextContent('checked 0 sec ago')
    // no event within 300 km, and every source answered: then, and only
    // then, "nothing under way"
    expect(SAMUI.blocks.alerts_now.data).toMatchObject({ total: 0, sources_down: [] })
    expect(alerts).toHaveTextContent(en('place.alerts.none', { km: 300 }))

    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000 + PLACE_TICK_MS)
    })
    expect(alerts).toHaveTextContent('checked 5 min ago')
    expect(alerts.querySelector('.place-late')).toHaveTextContent(en('place.stale'))
  })
})

describe('alerts now', () => {
  it('shows the most severe events with a glyph and a word, never the colour alone', async () => {
    const { container } = render(<PlaceReport {...PARIS_PROPS} fetcher={answering(PARIS)} />)
    await flush()

    const alerts = section(container, 'alerts')
    const pills = alerts.querySelectorAll('.place-pill')
    expect(pills).toHaveLength(3)
    for (const pill of pills) {
      expect(pill).toHaveClass('sev', 'sev-info')
      expect(pill.querySelector('.glyph')).toHaveAttribute('aria-hidden', 'true')
      expect(pill).toHaveTextContent(en('sev.info'))
    }
    // events are under way: "nothing under way" is never said
    expect(alerts).not.toHaveTextContent(en('place.alerts.none', { km: 300 }))
  })

  it('names the sources that could not be checked instead of saying "nothing"', async () => {
    const body = copy(SAMUI)
    // the shape /api/sources gives a degraded source, applied to this report
    ;(body.blocks.alerts_now.data as { sources_down: string[] }).sources_down = ['gdacs']
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(body)} />)
    await flush()

    const [alerts] = rows(container, 'alerts_now')
    expect(alerts).not.toHaveTextContent(en('place.alerts.none', { km: 300 }))
    expect(alerts).toHaveTextContent(en('place.alerts.down', { sources: 'GDACS' }))
  })
})

describe('the forecast', () => {
  it('labels the day under way as the rest of the day, never as the day', async () => {
    const { container } = render(<PlaceReport {...MIDPACIFIC_PROPS} fetcher={onceThenSilent(MIDPACIFIC)} />)
    await flush()

    // at -160 deg the solar day starts at the 12:00 UTC step; by 13:27 it
    // is under way and only three of its four blocks are ahead
    const days = (MIDPACIFIC.blocks.forecast_daily.data as { days: { complete: boolean; blocks: number }[] }).days
    expect(days[0]).toMatchObject({ complete: false, blocks: 3 })
    const today = section(container, 'today')
    const items = today.querySelectorAll('.place-day')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveAttribute('data-partial', 'true')
    expect(items[0]).toHaveTextContent(en('place.day.partial', { time: '18:00 UTC' }))
    expect(items[0]).toHaveTextContent(en('place.day.range', { min: '27 °C', max: '29.3 °C' }))
    expect(items[0]).not.toHaveTextContent('max ')
    expect(items[1]).not.toHaveAttribute('data-partial')
    expect(items[1]).toHaveTextContent(en('place.day.maxmin', { max: '30 °C', min: '28.4 °C' }))
  })

  it('names every MET Norway symbol of the legend in the legend’s own words', () => {
    const lines = legend.trim().split('\n').slice(1)
    expect(lines.length).toBeGreaterThan(40)
    for (const line of lines) {
      const [id, english, , , , variants] = line.split(',')
      const base = id.trim()
      const codes = variants.trim() === '1' ? ['_day', '_night', '_polartwilight'].map((v) => base + v) : [base]
      for (const code of codes) {
        expect(skyLabel(en, code), code).toBe(english)
      }
    }
    expect(skyLabel(en, 'nonsense')).toBeNull()
  })
})

describe('the request', () => {
  it('fetchPlace asks with cache: "no-store" and the rounded point', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify(SAMUI), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      )
    const controller = new AbortController()
    const body = await fetchPlace({ lat: 9.51, lon: 100.01, radius_km: 300, lang: 'en' }, controller.signal)

    expect(body.server_time).toBe(SAMUI.server_time)
    const [url, init] = spy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/place?lat=9.51&lon=100.01&radius_km=300&lang=en')
    expect(init.cache).toBe('no-store')
    expect(init.signal).toBe(controller.signal)
  })

  it('sends the rounded point to the fetcher', async () => {
    const fetcher = answering(SAMUI)
    render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()
    expect(fetcher.mock.calls[0][0]).toEqual({ lat: 9.51, lon: 100.01, radius_km: 300, lang: 'en' })
  })

  it('says it is loading before the first response, and shows no number', () => {
    const { container } = render(<PlaceReport {...SAMUI_PROPS} name="Ko Samui" fetcher={silent()} />)
    expect(screen.getByRole('status')).toHaveTextContent(en('place.loading'))
    expect(container.textContent).not.toMatch(/\d/)
  })

  it('refuses a payload without the full contract and says why', async () => {
    const broken = copy(SAMUI) as unknown as { blocks: Record<string, unknown> }
    delete broken.blocks['history.cyclones']
    const fetcher = vi.fn(async () => broken as unknown as PlaceResponse)
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()

    expect(container).toHaveTextContent(en('place.error', { reason: 'unexpected response from /api/place' }))
    expect(container.querySelector('[data-section]')).toBeNull()
  })

  it('shows the real error of a failed fetch, and no number', async () => {
    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={failing} />)
    await flush()
    expect(container).toHaveTextContent(en('place.error', { reason: 'TypeError: Failed to fetch' }))
    expect(container.querySelector('[data-section]')).toBeNull()
    expect(container.textContent).not.toMatch(/\d/)
  })

  it('drops a response whose query is not the current place', async () => {
    // the server (or a cache) answers for Ko Samui while Paris is asked
    const { container } = render(<PlaceReport {...PARIS_PROPS} fetcher={answering(SAMUI)} />)
    await flush()

    expect(container.querySelector('[data-section]')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent(en('place.loading'))
    expect(container).not.toHaveTextContent('VTSM')
  })

  it('aborts the previous request on a place change, and a late answer never paints over the new place', async () => {
    let answerSamui: (body: PlaceResponse) => void = () => {}
    const fetcher = vi
      .fn<(query: PlaceQuery, signal: AbortSignal) => Promise<PlaceResponse>>()
      .mockImplementationOnce(() => new Promise<PlaceResponse>((resolve) => (answerSamui = resolve)))
      .mockImplementationOnce(async () => copy(PARIS))
      .mockImplementation(() => new Promise<PlaceResponse>(() => {}))
    const { container, rerender } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    const first = fetcher.mock.calls[0][1]

    rerender(<PlaceReport {...PARIS_PROPS} fetcher={fetcher} />)
    await flush()
    expect(first.aborted).toBe(true)
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Paris')
    expect(container).toHaveTextContent('LFPB')

    // Ko Samui's answer finally lands: it is ignored
    await act(async () => {
      answerSamui(copy(SAMUI))
      await Promise.resolve()
    })
    expect(container).toHaveTextContent('LFPB')
    expect(container).not.toHaveTextContent('VTSM')
  })

  it('aborts the request in flight on unmount', () => {
    const fetcher = silent()
    const { unmount } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    const signal = fetcher.mock.calls[0][1]
    expect(signal.aborted).toBe(false)
    unmount()
    expect(signal.aborted).toBe(true)
  })

  it('refetches on a language change, and shows the server’s message in that language', async () => {
    const fetcher = vi
      .fn<(query: PlaceQuery, signal: AbortSignal) => Promise<PlaceResponse>>()
      .mockResolvedValueOnce(copy(SAMUI))
      .mockResolvedValueOnce(copy(SAMUI_FR))
      .mockImplementation(() => new Promise<PlaceResponse>(() => {}))
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(1)

    await act(async () => {
      useStore.getState().setLang('fr')
    })
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls[1][0]).toEqual({ lat: 9.51, lon: 100.01, radius_km: 300, lang: 'fr' })
    const [cyclones] = rows(container, 'history.cyclones')
    expect(cyclones).toHaveTextContent(
      translate('fr', 'place.unavailable', { message: SAMUI_FR.blocks['history.cyclones'].error!.message }),
    )
    expect(SAMUI_FR.blocks['history.cyclones'].error!.message).toBe(
      'Le catalogue est téléchargé pour la première fois.',
    )
  })

  it('retries a partial report after retry_after_s, at most twice', async () => {
    vi.useFakeTimers()
    expect(SAMUI.complete).toBe(false)
    expect(SAMUI.retry_after_s).toBe(5)
    const fetcher = answering(SAMUI)
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(container.querySelector('.place-partial')).toHaveTextContent(en('place.partial'))

    for (let n = 1; n <= PLACE_PARTIAL_RETRIES; n += 1) {
      await act(async () => {
        vi.advanceTimersByTime(4999)
      })
      expect(fetcher).toHaveBeenCalledTimes(n)
      await act(async () => {
        vi.advanceTimersByTime(1)
      })
      await flush()
      expect(fetcher).toHaveBeenCalledTimes(n + 1)
    }
    // still partial: left to the five-minute refresh
    await act(async () => {
      vi.advanceTimersByTime(60 * 1000)
    })
    expect(fetcher).toHaveBeenCalledTimes(1 + PLACE_PARTIAL_RETRIES)
  })

  it('refreshes every five minutes while the page is visible, never while it is hidden', async () => {
    vi.useFakeTimers()
    const fetcher = answering(SAMUI)
    const { unmount } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()
    // the mount fetch and its two partial retries
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 1000)
    })
    await flush()
    const settled = 1 + PLACE_PARTIAL_RETRIES
    expect(fetcher).toHaveBeenCalledTimes(settled)

    setVisibility('hidden')
    await act(async () => {
      vi.advanceTimersByTime(PLACE_REFRESH_MS * 2)
    })
    expect(fetcher).toHaveBeenCalledTimes(settled)

    // seen again after more than five minutes: it catches up at once
    await act(async () => {
      setVisibility('visible')
    })
    expect(fetcher).toHaveBeenCalledTimes(settled + 1)

    unmount()
    await act(async () => {
      vi.advanceTimersByTime(PLACE_REFRESH_MS * 3)
    })
    expect(fetcher).toHaveBeenCalledTimes(settled + 1)
  })
})
