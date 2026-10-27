/** The place report shows what `/api/place` returned, and nothing else.
 *
 * Owner rule: "rien ne doit être fake". Each test below guards one way the
 * panel could lie: a number drawn for a block that has none, a stale reading
 * shown as current because the browser clock or a forgotten tab says so, a
 * late answer for another place painted over this one, a source hidden
 * behind a fold, a warning that may cover the place said to be far away or
 * not said at all.
 *
 * Fixtures. `fixtures/place_*.json` are REAL `/api/place` bodies, saved byte
 * for byte: the SOSForge backend (branch worktree-wf_b765ef57-e77-8 at
 * 2b096d7) driven in process by FastAPI's TestClient on 2026-10-27, live
 * sources running and every upstream the real one. Each has a `.meta.json`
 * sidecar (request, status, headers, time, sha256), checked below. `_cold`
 * is a first view (blocks still loading answer `budget`), `place_lima_429`
 * a refusal of the route itself. Where a test needs a state the captures do
 * not hold (a sources list with nobody down, an old server's shape), it
 * changes ONE field of a real body and says so.
 *
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
  PlaceHttpError,
  PlaceReport,
  distanceToBoxKm,
  fetchPlace,
  isPlaceResponse,
  parseRetryAfter,
  roundCoord,
  skyLabel,
  type PlaceQuery,
  type PlaceResponse,
} from '../components/PlaceReport'
import { distanceKm } from '../arrival'
import { SOURCE_LABEL, severityLabel } from '../format'
import { translate, type Lang } from '../i18n'
import { useStore } from '../store'
import type { SosEvent } from '../types'
import samuiRaw from './fixtures/place_kosamui.json?raw'
import samuiFrRaw from './fixtures/place_kosamui_fr.json?raw'
import samuiColdRaw from './fixtures/place_kosamui_cold.json?raw'
import parisRaw from './fixtures/place_paris.json?raw'
import midpacificRaw from './fixtures/place_midpacific.json?raw'
import kathmanduRaw from './fixtures/place_kathmandu.json?raw'
import athensRaw from './fixtures/place_athens.json?raw'
import pastoRaw from './fixtures/place_pasto.json?raw'
import tokyoColdRaw from './fixtures/place_tokyo_cold.json?raw'
import limaRefusedRaw from './fixtures/place_lima_429.json?raw'
import samuiMeta from './fixtures/place_kosamui.json.meta.json'
import samuiFrMeta from './fixtures/place_kosamui_fr.json.meta.json'
import samuiColdMeta from './fixtures/place_kosamui_cold.json.meta.json'
import parisMeta from './fixtures/place_paris.json.meta.json'
import midpacificMeta from './fixtures/place_midpacific.json.meta.json'
import kathmanduMeta from './fixtures/place_kathmandu.json.meta.json'
import athensMeta from './fixtures/place_athens.json.meta.json'
import pastoMeta from './fixtures/place_pasto.json.meta.json'
import tokyoColdMeta from './fixtures/place_tokyo_cold.json.meta.json'
import limaRefusedMeta from './fixtures/place_lima_429.json.meta.json'
import legend from './fixtures/metno_weathericons_legend.csv?raw'
import appSource from '../App.tsx?raw'
import { resetStore } from './helpers'

// Read from disk: vitest stubs every `.css` import to an empty string, `?raw`
// included, and the project carries no Node typings (contrast.test.ts does
// the same).
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as {
  readFileSync(path: string, encoding: string): string
}
const root = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
const placeCss = fs.readFileSync(`${root}/src/components/PlaceReport.css`, 'utf8')

const parse = (raw: string) => JSON.parse(raw) as PlaceResponse

const SAMUI = parse(samuiRaw)
const SAMUI_FR = parse(samuiFrRaw)
const SAMUI_COLD = parse(samuiColdRaw)
const PARIS = parse(parisRaw)
const MIDPACIFIC = parse(midpacificRaw)
const KATHMANDU = parse(kathmanduRaw)
const ATHENS = parse(athensRaw)
const PASTO = parse(pastoRaw)
const TOKYO_COLD = parse(tokyoColdRaw)

const CAPTURES: [string, string, { url: string; status: number; bytes: number; sha256: string }][] = [
  ['kosamui', samuiRaw, samuiMeta],
  ['kosamui_fr', samuiFrRaw, samuiFrMeta],
  ['kosamui_cold', samuiColdRaw, samuiColdMeta],
  ['paris', parisRaw, parisMeta],
  ['midpacific', midpacificRaw, midpacificMeta],
  ['kathmandu', kathmanduRaw, kathmanduMeta],
  ['athens', athensRaw, athensMeta],
  ['pasto', pastoRaw, pastoMeta],
  ['tokyo_cold', tokyoColdRaw, tokyoColdMeta],
  ['lima_429', limaRefusedRaw, limaRefusedMeta],
]

const en = (key: string, vars?: Record<string, string | number>) => translate('en', key, vars)

/** A copy, so no test can leak a mutation into another. */
const copy = (body: PlaceResponse): PlaceResponse => structuredClone(body)

type Fetcher = (query: PlaceQuery, signal: AbortSignal) => Promise<PlaceResponse>

/** A fetch that answers at once with the given payload. */
const answering = (body: PlaceResponse) => vi.fn<Fetcher>(async () => copy(body))

/** Answers the first call, then never again: a panel left open whose
 * refetches do not come back. */
const onceThenSilent = (body: PlaceResponse) =>
  vi
    .fn<Fetcher>()
    .mockResolvedValueOnce(copy(body))
    .mockImplementation(() => new Promise<PlaceResponse>(() => {}))

/** A fetch that never answers. */
const silent = () => vi.fn<Fetcher>(() => new Promise<PlaceResponse>(() => {}))

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

function openFold(scope: HTMLElement) {
  fireEvent.click(within(scope).getByRole('button', { name: en('more.show') }))
}

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

function alertsData(body: PlaceResponse) {
  return body.blocks.alerts_now.data as {
    total: number
    events: SosEvent[]
    unplaced_in_country: number
    sources_down: string[]
    radius_km: number
  }
}

const SAMUI_PROPS = { lat: 9.512, lon: 100.013, name: 'Ko Samui' }
const PARIS_PROPS = { lat: 48.857, lon: 2.352, name: 'Paris' }
const MIDPACIFIC_PROPS = { lat: 0, lon: -160, name: 'Mid-Pacific' }
const KATHMANDU_PROPS = { lat: 27.7, lon: 85.32, name: 'Kathmandu' }
const ATHENS_PROPS = { lat: 37.98, lon: 23.73, name: 'Athens' }
const PASTO_PROPS = { lat: 1.21, lon: -77.28, name: 'Pasto' }
const TOKYO_PROPS = { lat: 35.68, lon: 139.69, name: 'Tokyo' }

beforeEach(() => {
  resetStore()
  useStore.getState().setLang('en')
  setVisibility('visible')
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

describe('the fixtures themselves', () => {
  it('are the captured bodies, byte for byte, as their sidecars record them', async () => {
    for (const [name, raw, meta] of CAPTURES) {
      expect(await sha256(raw), name).toBe(meta.sha256)
      expect(new TextEncoder().encode(raw).length, name).toBe(meta.bytes)
    }
  })

  it('are full reports for the props the tests give', () => {
    const pairs: [PlaceResponse, { lat: number; lon: number }][] = [
      [SAMUI, SAMUI_PROPS],
      [SAMUI_FR, SAMUI_PROPS],
      [SAMUI_COLD, SAMUI_PROPS],
      [PARIS, PARIS_PROPS],
      [MIDPACIFIC, MIDPACIFIC_PROPS],
      [KATHMANDU, KATHMANDU_PROPS],
      [ATHENS, ATHENS_PROPS],
      [PASTO, PASTO_PROPS],
      [TOKYO_COLD, TOKYO_PROPS],
    ]
    for (const [body, props] of pairs) {
      expect(isPlaceResponse(body)).toBe(true)
      expect(Object.keys(body.blocks)).toEqual([...PLACE_BLOCK_IDS])
      expect(body.query).toMatchObject({ lat: roundCoord(props.lat), lon: roundCoord(props.lon) })
    }
    expect(roundCoord(-0.001)).toBe(0)
    expect(Object.is(roundCoord(-0.001), -0)).toBe(false)
  })
})

describe('what a block says about itself', () => {
  it('an unavailable block draws no number of its own, only the server’s sentence', async () => {
    let checked = 0
    for (const [body, props] of [
      [SAMUI, SAMUI_PROPS],
      [SAMUI_COLD, SAMUI_PROPS],
      [PARIS, PARIS_PROPS],
      [MIDPACIFIC, MIDPACIFIC_PROPS],
    ] as const) {
      const { container, unmount } = render(<PlaceReport {...props} fetcher={answering(body)} />)
      await flush()
      for (const id of PLACE_BLOCK_IDS) {
        if (body.blocks[id].status !== 'unavailable') continue
        const found = rows(container, id)
        expect(found.length, `${id} has a row`).toBeGreaterThan(0)
        const sentence = en('place.unavailable', { message: body.blocks[id].error!.message })
        for (const row of found) {
          expect(row.textContent, id).toContain(sentence)
          // the server's sentence may hold a number ("within 100 km"); the
          // row around it holds none
          expect(row.textContent!.replace(sentence, ''), id).not.toMatch(/\d/)
          checked += 1
        }
      }
      unmount()
    }
    // three at Ko Samui, eleven on its first view, four in Paris, three at sea
    expect(checked).toBeGreaterThan(20)
  })

  it('a section whose blocks are all not applicable reads as one muted line', async () => {
    const { container } = render(<PlaceReport {...PARIS_PROPS} fetcher={answering(PARIS)} />)
    await flush()

    const sea = section(container, 'sea')
    const message = PARIS.blocks['sea.waves'].error!.message
    expect(message).toBe('The nearest ocean coastline is 150 km away.')
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
    expect(drought).toHaveTextContent(en('place.na', { message: MIDPACIFIC.blocks.drought.error!.message }))
  })

  it('a field the source does not publish is said in words, never 0', async () => {
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(SAMUI)} />)
    await flush()

    // MET Norway publishes no gust outside the Nordic area
    expect(SAMUI.blocks.wind.missing).toEqual([{ field: 'gust_ms', reason: 'not_in_source' }])
    const [wind] = rows(container, 'wind')
    expect(wind).toHaveTextContent(`${en('place.label.gust')}: ${en('place.missing')}`)
  })

  it('an unreadable dataset time is shown as sent, never as an age of zero', async () => {
    const body = copy(SAMUI)
    body.blocks.weather_now.as_of = '2026-10-27 15h00 local'
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(body)} />)
    await flush()

    const [now] = rows(container, 'weather_now')
    const chip = now.querySelector('time.place-asof')!
    expect(chip).toHaveTextContent(en('place.asof.raw', { date: '2026-10-27 15h00 local' }))
    expect(chip.textContent).not.toMatch(/\b0 sec\b/)
  })

  it('a dataset-time kind this panel does not know is not called "live feed"', async () => {
    const body = copy(SAMUI)
    const sst = body.blocks['sea.temperature']
    ;(sst as { as_of_kind: string }).as_of_kind = 'composite_window'
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(body)} />)
    await flush()

    const [row] = rows(container, 'sea.temperature')
    const chip = row.querySelector('time.place-asof')!
    expect(chip).not.toHaveTextContent(en('place.asof.live'))
    expect(chip.textContent).toMatch(/^dataset time Oct 26, 12:00 UTC$/)
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
    expect(SAMUI.blocks.air.source).toMatchObject({
      name: 'CAMS global via Open-Meteo',
      url: 'https://open-meteo.com/en/docs/air-quality-api',
    })
    const link = within(air).getByRole('link', { name: 'CAMS global via Open-Meteo' })
    expect(link).toBeVisible()
    expect(link).toHaveAttribute('href', 'https://open-meteo.com/en/docs/air-quality-api')
    expect(link.getAttribute('rel')?.split(' ').sort()).toEqual(['noopener', 'noreferrer'])
  })

  it('credits ISC-GEM, its link and its licence, from the source the server nests under `also`', async () => {
    for (const [body, props] of [
      [KATHMANDU, KATHMANDU_PROPS],
      [PARIS, PARIS_PROPS],
    ] as const) {
      const source = body.blocks['history.earthquakes'].source as {
        also?: { name: string; url: string; attribution: string; licence: string }[]
      }
      const [isc] = source.also!
      expect(isc.name).toBe('ISC-GEM Global Instrumental Earthquake Catalogue')
      const { container, unmount } = render(<PlaceReport {...props} fetcher={answering(body)} />)
      await flush()

      const history = section(container, 'history')
      // the credit and the link, visible next to the data
      const link = within(history).getByRole('link', { name: isc.name })
      expect(link).toBeVisible()
      expect(link).toHaveAttribute('href', isc.url)
      expect(history.querySelector(':scope > .place-sources')).toHaveTextContent(en('place.licence.sharealike'))
      // the attribution text and the licence behind "Show more"
      const attribution = within(history).getByText(isc.attribution, { exact: false })
      expect(attribution).not.toBeVisible()
      expect(attribution.closest('dd')).toHaveTextContent(isc.licence)
      openFold(history)
      expect(attribution).toBeVisible()
      unmount()
    }
  })

  it('keeps the raw reason and our own read time behind "Show more"', async () => {
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(SAMUI)} />)
    await flush()

    const sea = section(container, 'sea')
    const { code, reason } = SAMUI.blocks['sea.buoy'].error!
    const shown = within(sea).getByText(`${code}: ${reason}`)
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
    openFold(now)
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

    // METAR VTSM 271500Z, report served at 15:50:18Z
    expect(SAMUI.blocks.weather_now.as_of).toBe('2026-10-27T15:00:00Z')
    expect(SAMUI.server_time).toBe('2026-10-27T15:50:18Z')
    const [now] = rows(container, 'weather_now')
    expect(now.querySelector('time.place-asof')).toHaveTextContent('observed 50 min ago')
    expect(now).not.toHaveTextContent(en('place.stale'))
    // the time elapsed since arrival is added, on the panel's own tick
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000)
    })
    expect(now.querySelector('time.place-asof')).toHaveTextContent('observed 55 min ago')
  })

  it('a report handed in as `initial` keeps the age it had when it arrived', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2031-06-01T00:00:00Z'))
    // the parent received it five minutes ago
    const initial = { body: copy(SAMUI), arrived: Date.now() - 5 * 60 * 1000 }
    const { container } = render(<PlaceReport {...SAMUI_PROPS} initial={initial} fetcher={silent()} />)

    const [now] = rows(container, 'weather_now')
    expect(now.querySelector('time.place-asof')).toHaveTextContent('observed 55 min ago')
    expect(now.querySelector('time.place-asof')).not.toHaveTextContent('observed 50 min ago')
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

    // 50 min 18 s old at arrival: 2 h 9 min later it is still within 3 h ...
    await act(async () => {
      vi.advanceTimersByTime((2 * 60 + 9) * 60 * 1000)
    })
    expect(now).not.toHaveTextContent(en('place.stale'))
    // ... and two ticks later, past three hours, it is Late, with a glyph and words
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
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000 + PLACE_TICK_MS)
    })
    expect(alerts).toHaveTextContent('checked 5 min ago')
    expect(alerts.querySelector('.place-late')).toHaveTextContent(en('place.stale'))
  })
})

describe('alerts now', () => {
  it('says "nothing under way" only with no event, every source checked and no unplaced warning', async () => {
    // the capture had two sources down; here every source answered
    const body = copy(SAMUI)
    alertsData(body).sources_down = []
    expect(alertsData(body)).toMatchObject({ total: 0, events: [], unplaced_in_country: 0 })
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(body)} />)
    await flush()

    const [alerts] = rows(container, 'alerts_now')
    expect(alerts.querySelector('.state-ok')).toHaveTextContent(`●${en('place.alerts.none', { km: 300 })}`)
  })

  it('names the sources that could not be checked instead of saying "nothing"', async () => {
    const { sources_down: down } = alertsData(SAMUI)
    expect(down).toEqual(['aftershock', 'meteoalarm'])
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={answering(SAMUI)} />)
    await flush()

    const [alerts] = rows(container, 'alerts_now')
    expect(alerts).not.toHaveTextContent(en('place.alerts.none', { km: 300 }))
    expect(alerts).toHaveTextContent(
      en('place.alerts.down', { sources: down.map((name) => SOURCE_LABEL[name] ?? name).join(', ') }),
    )
  })

  it('shows the most severe events with a glyph and a word, never the colour alone', async () => {
    const { container } = render(<PlaceReport {...ATHENS_PROPS} fetcher={answering(ATHENS)} />)
    await flush()

    const alerts = section(container, 'alerts')
    const visible = alerts.querySelector('.place-section-body')!
    const pills = visible.querySelectorAll('.place-pill')
    expect(pills).toHaveLength(3)
    const top = alertsData(ATHENS).events.slice(0, 3)
    pills.forEach((pill, index) => {
      expect(pill).toHaveClass('sev', `sev-${top[index].severity}`)
      expect(pill.querySelector('.glyph')).toHaveAttribute('aria-hidden', 'true')
      expect(pill).toHaveTextContent(severityLabel(en, top[index].severity))
    })
    expect(alerts).not.toHaveTextContent(en('place.alerts.none', { km: 300 }))
  })

  it('says the country’s warnings without a position up front, next to the listed events', async () => {
    // Meteoalarm publishes no geometry: Greece's 20 warnings are counted, not listed
    expect(alertsData(ATHENS)).toMatchObject({ total: 42, unplaced_in_country: 20, sources_down: [] })
    const { container } = render(<PlaceReport {...ATHENS_PROPS} fetcher={answering(ATHENS)} />)
    await flush()

    const alerts = section(container, 'alerts')
    const line = alerts.querySelector<HTMLElement>('.place-unplaced')!
    expect(line).toBeVisible()
    expect(line).toHaveTextContent(`▲${en('place.alerts.unplaced_more', { n: '20', country: 'Greece' })}`)
    expect(alerts.querySelector('.place-more')).not.toContainElement(line)
  })

  it('never says "nothing under way" while warnings of the country carry no position', async () => {
    // Athens with its placed events taken out: what Paris looks like under a
    // Meteoalarm orange (0 placed of 454, tasks/todo.md)
    const body = copy(ATHENS)
    alertsData(body).events = []
    alertsData(body).total = 0
    const { container } = render(<PlaceReport {...ATHENS_PROPS} fetcher={answering(body)} />)
    await flush()

    const alerts = section(container, 'alerts')
    expect(alerts).not.toHaveTextContent(en('place.alerts.none', { km: 300 }))
    expect(alerts.querySelector('.state-ok')).toBeNull()
    const line = alerts.querySelector<HTMLElement>('.place-unplaced')!
    expect(line).toBeVisible()
    expect(line).toHaveTextContent(en('place.alerts.unplaced', { n: '20', country: 'Greece' }))
    // "more" only when some are listed
    expect(line).not.toHaveTextContent(' more ')
  })

  it('counts the events the server did not list', async () => {
    const { container } = render(<PlaceReport {...ATHENS_PROPS} fetcher={answering(ATHENS)} />)
    await flush()

    const alerts = section(container, 'alerts')
    expect(alertsData(ATHENS).events).toHaveLength(20)
    const fold = alerts.querySelector<HTMLElement>('.place-more')!
    expect(fold.querySelectorAll('.place-alert')).toHaveLength(17)
    expect(fold).toHaveTextContent(en('place.alerts.unlisted', { n: '22', km: '300' }))
  })

  it('measures an area warning to its area, never to its centre', async () => {
    const { container } = render(<PlaceReport {...PASTO_PROPS} fetcher={answering(PASTO)} />)
    await flush()
    const alerts = section(container, 'alerts')
    openFold(alerts)

    const items = [...alerts.querySelectorAll<HTMLElement>('.place-alert')]
    const events = alertsData(PASTO).events
    expect(items).toHaveLength(events.length)
    let covering = 0
    for (const item of items) {
      const event = events.find((e) => e.id === item.dataset.event)!
      const meta = item.querySelector('.place-alert-meta')!.textContent!
      if (event.bbox) {
        const gap = distanceToBoxKm(PASTO.query.lat, PASTO.query.lon, event.bbox)
        expect(meta).toContain(
          gap === 0 ? en('place.alerts.area_here') : en('place.alerts.area_away', { km: Math.round(gap) }),
        )
        if (gap === 0) covering += 1
      }
      // every distance said is within the section's 300 km
      for (const [, km] of meta.matchAll(/(\d[\d,]*) km/g)) {
        expect(Number(km.replace(/,/g, '')), meta).toBeLessThanOrEqual(300)
      }
    }
    // the Colombian landslide warnings: centre about 500 km away, area over Pasto
    const landslide = events.find((e) => e.title === 'Landslide warning' && e.bbox)!
    expect(distanceKm(PASTO.query.lat, PASTO.query.lon, landslide.lat!, landslide.lon!)).toBeGreaterThan(450)
    expect(covering).toBeGreaterThanOrEqual(6)
  })

  it('distanceToBoxKm: zero inside, the nearest edge outside, across the antimeridian too', () => {
    expect(distanceToBoxKm(1.21, -77.28, [-79.0091, 0.8685, -71.4535, 11.3478])).toBe(0)
    // due south of the box: the gap is along the meridian
    expect(distanceToBoxKm(0, 10, [9, 1, 11, 2])).toBeCloseTo(distanceKm(0, 10, 1, 10), 6)
    // a box from 170 E to 170 W holds 180 and -175
    expect(distanceToBoxKm(-17, 180, [170, -20, -170, -10])).toBe(0)
    expect(distanceToBoxKm(-17, -175, [170, -20, -170, -10])).toBe(0)
    // and 160 E is outside it, its west edge 10 degrees away
    expect(distanceToBoxKm(-17, 160, [170, -20, -170, -10])).toBeCloseTo(distanceKm(-17, 160, -17, 170), 6)
  })
})

describe('the forecast', () => {
  it('labels the day under way by the window its values cover', async () => {
    const { container } = render(<PlaceReport {...MIDPACIFIC_PROPS} fetcher={onceThenSilent(MIDPACIFIC)} />)
    await flush()

    // at -160 deg the solar day runs from 12:00 UTC; at 15:50 its 12-18
    // block is gone, the three left are 18, 00 and 06 UTC
    const days = (MIDPACIFIC.blocks.forecast_daily.data as { days: Record<string, unknown>[] }).days
    expect(days[0]).toMatchObject({
      start: '2026-10-27T12:00:00Z',
      end: '2026-10-28T12:00:00Z',
      blocks: 3,
      complete: false,
    })
    const today = section(container, 'today')
    const items = today.querySelectorAll('.place-day')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveAttribute('data-partial', 'true')
    expect(items[0].querySelector('.place-day-name')).toHaveTextContent(
      en('place.day.partial', { from: 'Oct 27, 18:00 UTC', to: 'Oct 28, 12:00 UTC' }),
    )
    // never the solar day's start, which is already past
    expect(items[0]).not.toHaveTextContent('12:00 UTC to')
    expect(items[0]).toHaveTextContent(en('place.day.range', { min: '27.4 °C', max: '29.3 °C' }))
    expect(items[0]).not.toHaveTextContent('max ')
    expect(items[1]).not.toHaveAttribute('data-partial')
    expect(items[1]).toHaveTextContent(en('place.day.maxmin', { max: '29.2 °C', min: '27.4 °C' }))
  })

  it('the backend’s own 10:36 Ko Samui case: one block left, 12:00 to 18:00 UTC', async () => {
    // the shape weather.py gives at 10:36Z (backend test_daily_view_and_the_
    // day_under_way): start yesterday 18:00, one block ahead
    const body = copy(MIDPACIFIC)
    const [first] = (body.blocks.forecast_daily.data as { days: Record<string, unknown>[] }).days
    Object.assign(first, { start: '2026-10-27T18:00:00Z', end: '2026-10-28T18:00:00Z', blocks: 1 })
    body.server_time = '2026-10-28T10:36:00Z'
    const { container } = render(<PlaceReport {...MIDPACIFIC_PROPS} fetcher={onceThenSilent(body)} />)
    await flush()

    const [item] = section(container, 'today').querySelectorAll('.place-day')
    expect(item.querySelector('.place-day-name')).toHaveTextContent(
      en('place.day.partial', { from: '12:00', to: '18:00 UTC' }),
    )
  })

  it('without a block count, says only where the window ends', async () => {
    const body = copy(MIDPACIFIC)
    const [first] = (body.blocks.forecast_daily.data as { days: Record<string, unknown>[] }).days
    delete first.blocks
    const { container } = render(<PlaceReport {...MIDPACIFIC_PROPS} fetcher={onceThenSilent(body)} />)
    await flush()

    const [item] = section(container, 'today').querySelectorAll('.place-day')
    expect(item.querySelector('.place-day-name')).toHaveTextContent(en('place.day.partial_until', { to: '12:00 UTC' }))
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

describe('air', () => {
  it('names the dominant pollutant the way the server codes it', async () => {
    const air = TOKYO_COLD.blocks.air.data as { aqi: { eu: { band: string; dominant: string } } }
    expect(air.aqi.eu).toMatchObject({ band: 'poor', dominant: 'nitrogen_dioxide' })
    const { container } = render(<PlaceReport {...TOKYO_PROPS} fetcher={onceThenSilent(TOKYO_COLD)} />)
    await flush()

    const [row] = rows(container, 'air')
    const headline = row.querySelector('.place-headline')!
    expect(headline).toHaveTextContent(
      `${en('place.air.eu', { band: en('place.air.eu.poor') })} · ${en('place.air.dominant', { pollutant: 'NO₂' })}`,
    )
    expect(container).not.toHaveTextContent('nitrogen_dioxide')
    expect(container).not.toHaveTextContent(/\bpm2_5\b/)
  })
})

describe('history', () => {
  /** Every NCEI cause code the backend emits (backend history.py CAUSES). */
  const CAUSES = [
    'unknown',
    'earthquake',
    'questionable_earthquake',
    'earthquake_and_landslide',
    'volcano_and_earthquake',
    'volcano_earthquake_and_landslide',
    'volcano',
    'volcano_and_landslide',
    'landslide',
    'meteorological',
    'explosion',
    'astronomical_tide',
  ]

  it('every tsunami cause the server sends has words in all five languages', async () => {
    for (const lang of ['fr', 'en', 'es', 'ja', 'id'] as Lang[]) {
      for (const cause of CAUSES) {
        const key = `place.history.cause.${cause}`
        expect(translate(lang, key), `${lang} ${cause}`).not.toBe(key)
      }
    }
    const body = copy(KATHMANDU)
    const events = (body.blocks['history.tsunamis'].data as { sources: { events: { cause: string }[] } }).sources
      .events
    expect(events.map((row) => row.cause)).toEqual(['landslide', 'earthquake'])
    // Krakatau 1883's code on the second row
    events[1].cause = 'volcano_and_earthquake'
    const { container } = render(<PlaceReport {...KATHMANDU_PROPS} fetcher={answering(body)} />)
    await flush()

    const history = section(container, 'history')
    expect(history).toHaveTextContent(`· ${en('place.history.cause.landslide')} ·`)
    expect(history).toHaveTextContent(`· ${en('place.history.cause.volcano_and_earthquake')} ·`)
    const list = [...history.querySelectorAll('.place-list')].find((ul) =>
      ul.previousElementSibling?.textContent === en('place.history.label.tsunamis'),
    )!
    for (const item of list.querySelectorAll('li')) expect(item.textContent).not.toContain('_')
  })

  it('calls a storm post-tropical only when the server says so', async () => {
    const { container, unmount } = render(<PlaceReport {...KATHMANDU_PROPS} fetcher={answering(KATHMANDU)} />)
    await flush()

    const cy = KATHMANDU.blocks['history.cyclones'].data as {
      strongest: { season: number; nature_at_closest: string; post_tropical: boolean }[]
    }
    expect(cy.strongest[0]).toMatchObject({ season: 1995, nature_at_closest: 'NR', post_tropical: false })
    expect(cy.strongest[3]).toMatchObject({ season: 1973, nature_at_closest: 'MX', post_tropical: false })
    const list = section(container, 'history')
      .querySelectorAll<HTMLElement>('.place-list')
    const storms = [...list].at(-1)!.querySelectorAll('li')
    expect(storms[0]).toHaveTextContent(en('place.history.nature.NR'))
    expect(storms[3]).toHaveTextContent(en('place.history.nature.MX'))
    expect(storms[1]).not.toHaveTextContent('closest approach')
    for (const storm of storms) expect(storm).not.toHaveTextContent(en('place.history.posttropical'))
    unmount()

    // Paris: three storms seen there only as extratropical, the server's verdict
    const paris = render(<PlaceReport {...PARIS_PROPS} fetcher={answering(PARIS)} />)
    await flush()
    const history = section(paris.container, 'history')
    const parisStorms = [...history.querySelectorAll('.place-list')].at(-1)!.querySelectorAll('li')
    expect(parisStorms).toHaveLength(3)
    for (const storm of parisStorms) expect(storm).toHaveTextContent(en('place.history.posttropical'))
    expect(history).toHaveTextContent(en('place.history.posttropical_only', { n: '3' }))
    paris.unmount()

    // a subtropical storm is counted as tropical: never "not a tropical cyclone"
    const body = copy(KATHMANDU)
    const rowsSs = (body.blocks['history.cyclones'].data as { strongest: Record<string, unknown>[] }).strongest
    rowsSs[0].nature_at_closest = 'SS'
    const ss = render(<PlaceReport {...KATHMANDU_PROPS} fetcher={answering(body)} />)
    await flush()
    const first = [...section(ss.container, 'history').querySelectorAll('.place-list')].at(-1)!.querySelector('li')!
    expect(first).toHaveTextContent(en('place.history.nature.SS'))
    expect(first).not.toHaveTextContent(en('place.history.posttropical'))
  })

  it('heads every list of the fold with what it holds, once', async () => {
    const { container } = render(<PlaceReport {...KATHMANDU_PROPS} fetcher={answering(KATHMANDU)} />)
    await flush()

    const headings = [...section(container, 'history').querySelectorAll('.place-more h4.place-sub')].map(
      (h) => h.textContent,
    )
    expect(headings).toEqual(
      expect.arrayContaining([
        en('place.history.quakes.strongest'),
        en('place.history.quakes.latest'),
        en('place.history.quakes.decades'),
        en('place.history.cyclones.strongest'),
        en('place.history.cyclones.decades'),
      ]),
    )
    expect(new Set(headings).size).toBe(headings.length)
    for (const list of section(container, 'history').querySelectorAll('.place-decades')) {
      expect(list.previousElementSibling?.tagName).toBe('H4')
    }
  })
})

describe('the request', () => {
  it('fetchPlace asks with cache: "no-store" and the rounded point', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(samuiRaw, { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const controller = new AbortController()
    const body = await fetchPlace({ lat: 9.51, lon: 100.01, radius_km: 300, lang: 'en' }, controller.signal)

    expect(body.server_time).toBe(SAMUI.server_time)
    const [url, init] = spy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/place?lat=9.51&lon=100.01&radius_km=300&lang=en')
    expect(init.cache).toBe('no-store')
    expect(init.signal).toBe(controller.signal)
  })

  it('fetchPlace reads the route’s real 429: its reason and its Retry-After', async () => {
    expect(limaRefusedMeta.status).toBe(429)
    const headers = limaRefusedMeta.headers as Record<string, string>
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(limaRefusedRaw, {
        status: 429,
        headers: { 'Content-Type': headers['content-type'], 'Retry-After': headers['retry-after'] },
      }),
    )
    const refused = await fetchPlace({ lat: -12.05, lon: -77.04, radius_km: 300, lang: 'en' }, new AbortController().signal)
      .then(() => null)
      .catch((reason: unknown) => reason)
    expect(refused).toBeInstanceOf(PlaceHttpError)
    expect((refused as PlaceHttpError).status).toBe(429)
    expect((refused as PlaceHttpError).retryAfterS).toBe(52)
    expect((refused as Error).message).toBe(
      'HTTP 429 rate_limited: too many new place reports from this address, try again shortly (Retry-After: 52 s)',
    )
    expect(parseRetryAfter('Wed, 28 Oct 2026 00:00:10 GMT', Date.parse('2026-10-28T00:00:00Z'))).toBe(10)
    expect(parseRetryAfter('soon')).toBeNull()
  })

  it('a refused first load says so aloud, and asks again after the wait the server named', async () => {
    vi.useFakeTimers()
    const refusal = new PlaceHttpError(
      'HTTP 429 rate_limited: too many new place reports from this address, try again shortly (Retry-After: 52 s)',
      429,
      52,
    )
    const fetcher = vi
      .fn<Fetcher>()
      .mockRejectedValueOnce(refusal)
      .mockResolvedValueOnce(copy(SAMUI))
      .mockImplementation(() => new Promise<PlaceResponse>(() => {}))
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()

    const error = container.querySelector('.place-error')!
    expect(error).toHaveAttribute('role', 'alert')
    expect(error).toHaveTextContent(en('place.error', { reason: refusal.message }))
    expect(error).toHaveTextContent(en('place.retry_after', { s: '52' }))
    await act(async () => {
      vi.advanceTimersByTime(51 * 1000)
    })
    expect(fetcher).toHaveBeenCalledTimes(1)
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(container.querySelector('.place-error')).toBeNull()
    expect(container).toHaveTextContent('VTSM')
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
    expect(screen.getByRole('alert')).toHaveTextContent('TypeError: Failed to fetch')
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
      .fn<Fetcher>()
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
      .fn<Fetcher>()
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
    const message = SAMUI_FR.blocks['sea.buoy'].error!.message
    expect(message).toBe('Aucune station à moins de 100 km n’a transmis depuis 3 heures.')
    const [buoy] = rows(container, 'sea.buoy')
    expect(buoy).toHaveTextContent(translate('fr', 'place.unavailable', { message }))
  })

  it('retries a partial report after retry_after_s, at most twice, then stops saying data is arriving', async () => {
    vi.useFakeTimers()
    expect(SAMUI_COLD.complete).toBe(false)
    expect(SAMUI_COLD.retry_after_s).toBe(5)
    const fetcher = answering(SAMUI_COLD)
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
    // still partial: left to the five-minute refresh, and the header says so
    await act(async () => {
      vi.advanceTimersByTime(60 * 1000)
    })
    expect(fetcher).toHaveBeenCalledTimes(1 + PLACE_PARTIAL_RETRIES)
    const notice = container.querySelector('.place-partial')!
    expect(notice).toHaveTextContent(en('place.partial.spent'))
    expect(notice).not.toHaveTextContent(en('place.partial'))
    expect(notice).toHaveAttribute('role', 'status')
  })

  it('a partial report followed by a complete one clears the notice', async () => {
    vi.useFakeTimers()
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(copy(SAMUI_COLD))
      .mockResolvedValueOnce(copy(SAMUI))
      .mockImplementation(() => new Promise<PlaceResponse>(() => {}))
    const { container } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()
    expect(container.querySelector('.place-partial')).not.toBeNull()
    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    await flush()
    expect(container.querySelector('.place-partial')).toBeNull()
  })

  it('refreshes every five minutes while the page is visible, never while it is hidden', async () => {
    vi.useFakeTimers()
    const fetcher = answering(SAMUI)
    const { unmount } = render(<PlaceReport {...SAMUI_PROPS} fetcher={fetcher} />)
    await flush()
    // a complete report: no partial retry
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 1000)
    })
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    await act(async () => {
      vi.advanceTimersByTime(PLACE_REFRESH_MS * 2)
    })
    expect(fetcher).toHaveBeenCalledTimes(1)

    // seen again after more than five minutes: it catches up at once
    await act(async () => {
      setVisibility('visible')
    })
    expect(fetcher).toHaveBeenCalledTimes(2)

    unmount()
    await act(async () => {
      vi.advanceTimersByTime(PLACE_REFRESH_MS * 3)
    })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})

describe('where the panel is mounted, and how its tables lay out', () => {
  it('the App slot says to mount it for a chosen place only, never with the device position', () => {
    const slot = appSource.slice(appSource.indexOf('PLACE REPORT SLOT'))
    expect(slot.slice(0, 900)).toContain('showing === "place" && place && <PlaceReport')
    expect(appSource).not.toMatch(/center\s*&&\s*<PlaceReport/)
  })

  it('tables stack on their SECTION’s width, which is a container', () => {
    expect(placeCss).toMatch(/\.place-report \.place-section \{[^}]*container: place-section \/ inline-size;/)
    expect(placeCss).toContain('@container place-section (max-width: 39.99rem)')
    expect(placeCss).toContain('@container place-section (max-width: 21.99rem)')
    expect(placeCss).not.toMatch(/@container place \(/)
  })
})
