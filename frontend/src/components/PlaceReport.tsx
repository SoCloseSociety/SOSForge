/** Place report: everything SOSForge can say about one place, read from
 * `GET /api/place` -- the weather now and ahead, the sea, the air, the
 * standing risks and the hazard history, next to the alerts under way.
 *
 * The rule this panel keeps is the product's own: it never lies about
 * freshness, and it never invents a number.
 *
 * - every value on screen is one the API returned. Before the first
 *   response the panel says it is loading; a failed request says so with the
 *   real error text. Nothing is ever drawn as 0 for "unknown";
 * - each block of the report comes from one dataset and carries that
 *   dataset's own date (`as_of`). Ages are computed on the SERVER clock:
 *   `server_time` plus the time elapsed since the response arrived, so a
 *   wrong clock in the browser cannot make a stale reading look current;
 * - a block older than its dataset's normal lag (`max_age_s`) turns Late on
 *   the panel's own tick, even if the server said it was fresh when it
 *   answered. A panel left open for hours never shows an old METAR as current;
 * - `unavailable` and `not_applicable` are said in words, with a glyph. An
 *   unavailable block draws no number and no dash that could pass for one;
 * - every section ends with its sources, linked, always visible. The long
 *   provenance (attribution, licence, exact dates, our own read time, the
 *   server's raw reason) is behind "Show more".
 *
 * Privacy: the report is built only for a place the reader searched or
 * clicked. Never mount it with the device position: the page promises that
 * the position never leaves the device, and this endpoint sends the point to
 * about fifteen third parties.
 *
 * Self-contained, like ClimatePanel: the fetch and a first payload can be
 * injected through props, so a test needs no network.
 */
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { distanceKm } from '../arrival'
import {
  SEVERITY_META,
  SOURCE_LABEL,
  countryName,
  formatAge,
  severityLabel,
  type T,
} from '../format'
import type { Lang } from '../i18n'
import { useStore } from '../store'
import type { Severity, SosEvent } from '../types'
import './PlaceReport.css'

// ------------------------------------------------------------------ contract

/** Every block of `/api/place`, in the contract's order. The server always
 * sends all of them. */
export const PLACE_BLOCK_IDS = [
  'weather_now',
  'forecast_hourly',
  'forecast_daily',
  'wind',
  'sea.waves',
  'sea.level',
  'sea.temperature',
  'sea.buoy',
  'air',
  'pollen',
  'drought',
  'fire.hotspots',
  'fire.danger',
  'risk.water',
  'risk.country',
  'history.earthquakes',
  'history.significant_earthquakes',
  'history.tsunamis',
  'history.eruptions',
  'history.volcanoes',
  'history.cyclones',
  'alerts_now',
] as const

export type PlaceBlockId = (typeof PLACE_BLOCK_IDS)[number]
export type PlaceStatus = 'ok' | 'unavailable' | 'not_applicable'
export type PlaceNature =
  | 'observation'
  | 'forecast'
  | 'model'
  | 'monitoring'
  | 'static'
  | 'catalogue'
  | 'live'
export type PlaceAsOfKind =
  | 'observed'
  | 'issued'
  | 'model_run'
  | 'valid_for'
  | 'period_start'
  | 'through'
  | 'published'
  | 'live'

export interface PlaceSource {
  name: string
  url: string
  attribution?: string | null
  licence?: string | null
}

export interface PlaceError {
  code: string
  /** raw, English, for operators: shown behind "Show more" only */
  reason: string
  /** one sentence in the requested language, shown as is */
  message: string
}

export interface PlaceMissing {
  field: string
  reason: string
}

export interface PlaceBlock<D = Record<string, unknown>> {
  status: PlaceStatus
  nature: PlaceNature
  /** the DATASET's time, never our fetch; null unless status is ok */
  as_of: string | null
  as_of_kind: PlaceAsOfKind
  stale: boolean
  max_age_s: number | null
  /** our last read of the upstream, shown apart from as_of */
  fetched_at: string | null
  /** one source, or several (ISC-GEM rows add a second attribution) */
  source: PlaceSource | PlaceSource[] | null
  error: PlaceError | null
  missing: PlaceMissing[]
  data: D | null
}

export interface PlaceQuery {
  lat: number
  lon: number
  radius_km: number
  lang: Lang
}

export interface PlaceInfo {
  country_code: string | null
  country_codes: string[]
  at_sea: boolean
  coast_km: number | null
}

export interface PlaceResponse {
  api: number
  server_time: string
  query: PlaceQuery
  place: PlaceInfo
  complete: boolean
  retry_after_s: number | null
  counts: { ok: number; unavailable: number; not_applicable: number }
  blocks: Record<PlaceBlockId, PlaceBlock>
}

// The data of each block, as far as the panel reads it. Every field may be
// missing or null: a field the source does not publish is never filled in.

type Num = number | null | undefined

interface WeatherNowData {
  station?: { icao?: string; distance_km?: Num; elevation_m?: Num } | null
  raw?: string | null
  temp_c?: Num
  dewpoint_c?: Num
  rh_pct?: Num
  wind_ms?: Num
  wind_from_deg?: Num
  calm?: boolean
  wind_variable?: boolean
  gust_ms?: Num
  visibility_km?: Num
  visibility_at_least?: boolean
  qnh_hpa?: Num
  wx?: string | null
  clouds?: { cover: string; base_ft: Num }[]
  flight_category?: string | null
  derived?: string[]
}

interface HourStep {
  time: string
  temp_c?: Num
  feels_like_c?: Num
  rh_pct?: Num
  dewpoint_c?: Num
  pressure_hpa?: Num
  cloud_pct?: Num
  fog_pct?: Num
  precip_mm?: Num
  symbol?: string | null
  uv_clear_sky?: Num
}

interface HourlyData {
  model_elevation_m?: Num
  hours?: HourStep[]
}

interface DayStep {
  date: string
  start: string
  end: string
  temp_max_c?: Num
  temp_min_c?: Num
  precip_mm?: Num
  symbol?: string | null
  blocks?: number
  complete: boolean
}

interface DailyData {
  days?: DayStep[]
}

interface WindData {
  hours?: { time: string; wind_ms?: Num; wind_from_deg?: Num; gust_ms?: Num }[]
  max_48h?: { wind_ms?: Num; time?: string | null; gust_ms?: Num; gust_time?: string | null } | null
  fallback?: { from: string; code: string; reason: string } | null
}

interface Cell {
  lat?: Num
  lon?: Num
  distance_km?: Num
}

interface WavesData {
  cell?: Cell | null
  hours?: { time: string; height_m?: Num; period_s?: Num; from_deg?: Num }[]
  max_48h?: { height_m?: Num; time?: string | null } | null
}

interface LevelData {
  cell?: Cell | null
  datum?: string
  hours?: { time: string; level_m?: Num; current_ms?: Num; current_to_deg?: Num }[]
  extremes?: { time: string; level_m?: Num; type: 'high' | 'low' }[]
}

interface SstData {
  cell?: Cell | null
  sst_c?: Num
  ice?: boolean | null
  day?: string | null
}

interface BuoyData {
  station?: { id?: string; name?: string | null; type?: string | null; distance_km?: Num } | null
  wave_height_m?: Num
  dominant_period_s?: Num
  mean_wave_from_deg?: Num
  wind_ms?: Num
  wind_from_deg?: Num
  gust_ms?: Num
  water_temp_c?: Num
  air_temp_c?: Num
  pressure_hpa?: Num
  tide_m?: Num
}

interface AirNow {
  time?: string
  pm2_5_ugm3?: Num
  pm10_ugm3?: Num
  o3_ugm3?: Num
  no2_ugm3?: Num
  so2_ugm3?: Num
  co_ugm3?: Num
  dust_ugm3?: Num
  aod_550?: Num
  uv_index?: Num
  uv_index_clear_sky?: Num
}

interface AirData {
  cell?: Cell | null
  now?: AirNow | null
  aqi?: {
    eu?: { band?: string | null; dominant?: string | null; scale?: string } | null
    us?: { value?: Num; category?: string | null; dominant?: string | null; scale?: string } | null
  } | null
}

interface PollenData {
  cell?: Cell | null
  now?: Record<string, number | string | null> | null
}

interface DroughtData {
  cell?: Cell | null
  spi_3?: { value?: Num; period_start?: string | null; class?: string | null } | null
  soil_moisture_anomaly?: { value?: Num; period_start?: string | null } | null
  agri_risk?: { class?: Num; label?: string | null; period_start?: string | null } | null
}

interface HotspotsData {
  window_start?: string
  window_end?: string
  within_km?: Record<string, number>
  nearest?: {
    distance_km?: Num
    time?: string
    frp_mw?: Num
    confidence?: string | null
    satellite?: string | null
  } | null
}

interface FireDangerData {
  days?: { date: string; class?: string | null; fwi_min?: Num; fwi_max?: Num }[]
  horizon_end?: string | null
}

interface WaterIndicator {
  raw?: Num
  cat?: Num
  label?: string | null
  scale?: string | null
}

interface WaterData {
  basin_pfaf_id?: Num
  riverine_flood?: WaterIndicator | null
  coastal_flood?: WaterIndicator | null
  drought_risk?: WaterIndicator | null
  water_stress?: WaterIndicator | null
}

interface CountryRiskData {
  iso3?: string
  release?: string
  inform?: Num
  hazard_exposure?: Num
  natural_hazard?: Num
  vulnerability?: Num
  lack_of_coping_capacity?: Num
  hazards?: Record<string, number | null>
  country_basis?: string
}

interface QuakeRow {
  id: string
  time: string
  mag?: Num
  mag_type?: string | null
  depth_km?: Num
  distance_km?: Num
  bearing_deg?: Num
  url?: string | null
}

interface DecadeCount {
  decade: number
  count: number
  early?: boolean
  partial?: boolean
}

interface QuakesData {
  radius_km?: Num
  min_magnitude?: Num
  since_year?: Num
  total?: Num
  by_decade?: DecadeCount[]
  strongest?: QuakeRow[]
  latest?: QuakeRow[]
}

interface PartialDate {
  year: number
  month?: number | null
  day?: number | null
}

interface SignificantRow {
  date: PartialDate
  mag?: Num
  vei?: Num
  name?: string | null
  deaths?: Num
  deaths_total?: Num
  damage_musd?: Num
  tsunami?: boolean
  distance_km?: Num
}

interface SignificantData {
  total?: Num
  events?: SignificantRow[]
}

interface TsunamisData {
  sources?: {
    total?: Num
    kept?: Num
    excluded_doubtful?: Num
    events?: {
      date: PartialDate
      cause?: string | null
      validity?: string | null
      max_height_m?: Num
      deaths?: Num
      deaths_total?: Num
      distance_km?: Num
      name?: string | null
    }[]
  } | null
  runups?: {
    within_km?: Num
    total?: Num
    top?: { date: PartialDate; location?: string | null; height_m?: Num; distance_km?: Num }[]
  } | null
}

interface VolcanoesData {
  total?: Num
  volcanoes?: {
    number?: Num
    name?: string
    type?: string | null
    last_eruption_year?: Num
    continuing?: boolean
    elevation_m?: Num
    country?: string | null
    distance_km?: Num
  }[]
}

interface CycloneRow {
  sid?: string
  name?: string | null
  season?: Num
  max_wind_within_radius_kt?: Num
  lifetime_max_wind_kt?: Num
  wind_source?: string | null
  closest_km?: Num
  closest_time?: string | null
  nature_at_closest?: string | null
  provisional?: boolean
}

interface CyclonesData {
  catalogue_since_season?: Num
  total_storms?: Num
  tropical?: Num
  post_tropical_only?: Num
  strongest?: CycloneRow[]
  latest?: CycloneRow[]
  by_decade?: DecadeCount[]
  file_modified?: string | null
}

interface AlertsData {
  radius_km?: Num
  total?: Num
  events?: SosEvent[]
  unplaced_in_country?: Num
  sources_down?: string[]
}

export type PlaceFetcher = (query: PlaceQuery, signal: AbortSignal) => Promise<PlaceResponse>

/** A report left open refreshes this often while the page is visible. The
 * server's own TTLs still govern every upstream, and a fully cached report
 * spends no rate token. */
export const PLACE_REFRESH_MS = 5 * 60 * 1000

/** The ages on screen are recomputed this often. */
export const PLACE_TICK_MS = 30 * 1000

/** `complete: false` is retried after `retry_after_s`, this many times at
 * most, then left to the five-minute refresh. */
export const PLACE_PARTIAL_RETRIES = 2

const STATUSES: ReadonlySet<string> = new Set(['ok', 'unavailable', 'not_applicable'])

/** The coordinates as the server echoes them: two decimals, and never -0. */
export function roundCoord(value: number): number {
  const rounded = Math.round(value * 100) / 100
  return rounded === 0 ? 0 : rounded
}

/** The minimum a payload must have before anything is drawn from it: the
 * server's clock and EVERY block. A proxy error page or a half-deployed API
 * must surface as an error, never as a panel of empty sections. */
export function isPlaceResponse(body: unknown): body is PlaceResponse {
  if (typeof body !== 'object' || body === null) return false
  const { server_time: serverTime, blocks, query } = body as {
    server_time?: unknown
    blocks?: unknown
    query?: unknown
  }
  if (typeof serverTime !== 'string' || Number.isNaN(Date.parse(serverTime))) return false
  if (typeof query !== 'object' || query === null) return false
  const { lat, lon } = query as { lat?: unknown; lon?: unknown }
  if (typeof lat !== 'number' || typeof lon !== 'number') return false
  if (typeof blocks !== 'object' || blocks === null) return false
  return PLACE_BLOCK_IDS.every((id) => {
    const item = (blocks as Record<string, unknown>)[id]
    return (
      typeof item === 'object' &&
      item !== null &&
      STATUSES.has((item as { status?: unknown }).status as string)
    )
  })
}

/** The URL of one report. The coordinates go out already rounded: the
 * server rounds them anyway, and a finer point would only identify the
 * reader better. */
export function placeUrl(query: PlaceQuery): string {
  const params = new URLSearchParams({
    lat: String(query.lat),
    lon: String(query.lon),
    radius_km: String(query.radius_km),
    lang: query.lang,
  })
  return `/api/place?${params.toString()}`
}

/** The default fetch.
 *
 * `cache: 'no-store'`, never the browser's revalidation: with `no-cache` and
 * an ETag the browser asks, gets a 304, and hands back the CACHED body with
 * its OLD `server_time` -- which every age on screen is computed from. The
 * same trap is documented in main.py for the legacy `/api/events`. */
export async function fetchPlace(query: PlaceQuery, signal: AbortSignal): Promise<PlaceResponse> {
  const response = await fetch(placeUrl(query), {
    signal,
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    let detail = ''
    try {
      const body: unknown = await response.json()
      const inner = (body as { detail?: unknown } | null)?.detail
      if (inner && typeof inner === 'object') {
        const { code, reason } = inner as { code?: unknown; reason?: unknown }
        detail = [code, reason].filter((part) => typeof part === 'string' && part).join(': ')
      } else if (typeof inner === 'string') {
        detail = inner
      }
    } catch {
      detail = ''
    }
    const retry = response.headers.get('Retry-After')
    throw new Error(
      `HTTP ${response.status}${detail ? ` ${detail}` : ''}${retry ? ` (Retry-After: ${retry} s)` : ''}`,
    )
  }
  const body: unknown = await response.json()
  if (!isPlaceResponse(body)) throw new Error('unexpected response from /api/place')
  return body
}

function sameQuery(a: PlaceQuery, b: PlaceQuery, withLang: boolean): boolean {
  const close = (x: number, y: number) => Math.abs(x - y) < 1e-6
  return (
    close(a.lat, b.lat) &&
    close(a.lon, b.lon) &&
    close(a.radius_km, b.radius_km) &&
    (!withLang || a.lang === b.lang)
  )
}

/** The real error text, as close to what failed as the browser gives it. */
function errorText(reason: unknown): string {
  if (reason instanceof Error) {
    const named = reason.name && reason.name !== 'Error' ? `${reason.name}: ` : ''
    return `${named}${reason.message}`.trim() || reason.name || String(reason)
  }
  return String(reason)
}

// ------------------------------------------------------------------ formats

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/

interface Formats {
  n: (value: number, digits?: number) => string
  duration: (seconds: number) => string
  day: (iso: string) => string
  weekday: (date: string) => string
  dateTime: (iso: string) => string
  time: (iso: string) => string
  /** HH:MM in UTC, for a list whose zone is said once */
  clock: (iso: string) => string
  partial: (date: PartialDate, t: T) => string
}

function makeFormats(lang: string): Formats {
  const numbers = new Map<number, Intl.NumberFormat>()
  const number = (digits: number) => {
    let format = numbers.get(digits)
    if (!format) {
      format = new Intl.NumberFormat(lang, { maximumFractionDigits: digits })
      numbers.set(digits, format)
    }
    return format
  }
  const unit = (name: 'second' | 'minute' | 'hour' | 'day') =>
    new Intl.NumberFormat(lang, { style: 'unit', unit: name, unitDisplay: 'short' })
  const seconds = unit('second')
  const minutes = unit('minute')
  const hours = unit('hour')
  const days = unit('day')
  // A dataset date is a calendar day in UTC: read and written in UTC, or a
  // reader west of Greenwich would see the day before.
  const day = new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeZone: 'UTC' })
  const weekday = new Intl.DateTimeFormat(lang, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  })
  // Times in UTC, said so, on a 24-hour clock: the place's own time zone
  // is not known here, and a reader's would be a guess about someone else.
  const dateTime = new Intl.DateTimeFormat(lang, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: 'UTC',
    timeZoneName: 'short',
  })
  const time = new Intl.DateTimeFormat(lang, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: 'UTC',
    timeZoneName: 'short',
  })
  const clock = new Intl.DateTimeFormat(lang, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: 'UTC',
  })
  const month = new Intl.DateTimeFormat(lang, { month: 'long', year: 'numeric', timeZone: 'UTC' })
  const valid = (iso: string) => !Number.isNaN(Date.parse(iso))
  return {
    n: (value, digits = 1) => number(digits).format(value),
    duration: (s) => {
      const abs = Math.max(0, s)
      if (abs < 60) return seconds.format(Math.floor(abs))
      if (abs < 3600) return minutes.format(Math.floor(abs / 60))
      if (abs < 86400) return hours.format(Math.floor(abs / 3600))
      return days.format(Math.floor(abs / 86400))
    },
    // Anything that is not the expected shape is shown as the server sent
    // it: a raw string is honest, a guessed date is not.
    day: (iso) => (valid(iso) ? day.format(new Date(iso)) : iso),
    weekday: (date) => (DAY_RE.test(date) ? weekday.format(new Date(`${date}T00:00:00Z`)) : date),
    dateTime: (iso) => (valid(iso) ? dateTime.format(new Date(iso)) : iso),
    time: (iso) => (valid(iso) ? time.format(new Date(iso)) : iso),
    clock: (iso) => (valid(iso) ? clock.format(new Date(iso)) : iso),
    partial: (date, t) => {
      const { year, month: m, day: d } = date
      if (!Number.isInteger(year)) return '?'
      if (year < 0) return t('place.history.bce', { year: -year })
      if (year < 1000 || !m) return String(year)
      const at = new Date(Date.UTC(year, m - 1, d || 1))
      return d ? day.format(at) : month.format(at)
    },
  }
}

const isNum = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

/** A translation that exists, or the server's own text. */
function orRaw(t: T, key: string, raw: string): string {
  const value = t(key)
  return value === key ? raw : value
}

/** A translated sentence with React nodes in its `{slots}`, so each language
 * keeps its own word order around a `<time>` or a link. */
function rich(template: string, slots: Record<string, ReactNode>): ReactNode[] {
  return template.split(/\{(\w+)\}/).map((part, index) =>
    index % 2 === 1 ? <span key={index}>{slots[part] ?? `{${part}}`}</span> : part,
  )
}

function capitalize(text: string): string {
  return text ? text.charAt(0).toLocaleUpperCase() + text.slice(1) : text
}

// ------------------------------------------------------------------ symbols

const SIMPLE_SKY: Record<string, string> = {
  clearsky: 'clear',
  fair: 'fair',
  partlycloudy: 'partly_cloudy',
  cloudy: 'cloudy',
  fog: 'fog',
}

/** MET Norway's `symbol_code` in words. The code is built from parts --
 * intensity, precipitation, showers, thunder, then `_day` / `_night` /
 * `_polartwilight` -- and the legend spells some of them its own way
 * (`lightssleetshowersandthunder`), which the API sends as is. Returns null
 * for a code it does not know, and the caller shows the raw code. */
export function skyLabel(t: T, symbol: string | null | undefined): string | null {
  if (!symbol) return null
  const base = symbol.trim().replace(/_(day|night|polartwilight)$/, '')
  const simple = SIMPLE_SKY[base]
  if (simple) return capitalize(t(`place.sky.${simple}`))
  const match = /^(lights?|heavy)?(rain|sleet|snow)(showers)?(andthunder)?$/.exec(base)
  if (!match) return null
  const [, intensity, kind, showers, thunder] = match
  let words = t(`place.sky.${kind}`)
  if (showers) words = t('place.sky.showers', { kind: words })
  if (intensity) words = t(`place.sky.${intensity.startsWith('light') ? 'light' : 'heavy'}`, { what: words })
  if (thunder) words = t('place.sky.thunder', { what: words })
  return capitalize(words)
}

function Sky({ t, symbol }: { t: T; symbol: string | null | undefined }) {
  if (!symbol) return null
  const label = skyLabel(t, symbol)
  return <span className="place-sky">{label ?? <span className="place-token">{symbol}</span>}</span>
}

// ------------------------------------------------------------------ clock and status

interface Clock {
  /** the server's clock now: server_time plus the time since arrival */
  serverNow: number
}

function ageSeconds(block: PlaceBlock, clock: Clock): number | null {
  if (!block.as_of) return null
  const at = Date.parse(block.as_of)
  return Number.isNaN(at) ? null : (clock.serverNow - at) / 1000
}

/** Late: the server said so when it answered, or the dataset's normal lag
 * has passed since, on the panel's own tick. */
export function isLate(block: PlaceBlock, clock: Clock): boolean {
  if (block.status !== 'ok') return false
  if (block.stale) return true
  const age = ageSeconds(block, clock)
  return isNum(block.max_age_s) && age !== null && age > block.max_age_s
}

function sourcesOf(block: PlaceBlock): PlaceSource[] {
  const { source } = block
  if (!source) return []
  const list = Array.isArray(source) ? source : [source]
  return list.filter((item) => item && typeof item.name === 'string' && item.name)
}

/** The ISC-GEM rows of `history.earthquakes` are CC BY-SA 3.0: the block says
 * so in a source entry of its own, or in its licence. */
function carriesShareAlike(block: PlaceBlock): boolean {
  return sourcesOf(block).some(
    (source) => /ISC-GEM/i.test(source.name) || /BY-SA/i.test(source.licence ?? ''),
  )
}

interface Ctx {
  t: T
  fmt: Formats
  lang: Lang
  clock: Clock
  report: PlaceResponse
}

function AsOf({ block, ctx }: { block: PlaceBlock; ctx: Ctx }) {
  const { t, fmt, clock } = ctx
  if (block.status !== 'ok' || !block.as_of) return null
  const iso = block.as_of
  const age = ageSeconds(block, clock)
  let text: string
  switch (block.as_of_kind) {
    case 'observed':
      text = t('place.asof.observed', { age: fmt.duration(age ?? 0) })
      break
    case 'issued':
      text = t('place.asof.issued', { date: fmt.dateTime(iso) })
      break
    case 'model_run':
      text = t('place.asof.model_run', { date: fmt.dateTime(iso) })
      break
    case 'valid_for':
      // the day an OBSERVATION analysis stands for is not a forecast
      text =
        block.nature === 'observation'
          ? t('place.asof.analysis', { date: fmt.day(iso) })
          : t('place.asof.valid_for', { date: fmt.day(iso) })
      break
    case 'period_start':
      text = t('place.asof.period_start', { date: fmt.day(iso) })
      break
    case 'through':
      text = t('place.asof.through', { date: fmt.day(iso) })
      break
    case 'published':
      text = t('place.asof.published', { date: fmt.day(iso) })
      break
    default:
      text = t('place.asof.live')
  }
  const late = isLate(block, clock)
  return (
    <span className="place-chips">
      <time className="place-asof" dateTime={iso}>
        {text}
      </time>
      {late ? <Late t={t} /> : null}
    </span>
  )
}

function Late({ t }: { t: T }) {
  return (
    <span className="state state-att place-late">
      <span className="state-glyph" aria-hidden="true">
        ▲
      </span>
      <span>{t('place.stale')}</span>
    </span>
  )
}

/** The status line of a block that has no values to show. */
function StatusLine({ block, t }: { block: PlaceBlock; t: T }) {
  const message = block.error?.message || t('place.unavailable.unknown')
  if (block.status === 'not_applicable') {
    return (
      <p className="state place-na">
        <span className="state-glyph" aria-hidden="true">
          ○
        </span>
        <span>{t('place.na', { message })}</span>
      </p>
    )
  }
  return (
    <p className="state state-att place-unavailable">
      <span className="state-glyph" aria-hidden="true">
        ▲
      </span>
      <span>{t('place.unavailable', { message })}</span>
    </p>
  )
}

/** One block's visible row: its values when ok, its status otherwise. */
function BlockRow({
  id,
  block,
  ctx,
  children,
  chip = true,
  named = true,
}: {
  id: PlaceBlockId
  block: PlaceBlock
  ctx: Ctx
  children?: ReactNode
  chip?: boolean
  /** name the dataset above its status, where a section holds several */
  named?: boolean
}) {
  const ok = block.status === 'ok' && block.data !== null
  return (
    <div className="place-block" data-block={id} data-status={block.status}>
      {ok ? (
        <>
          {children}
          {chip ? <AsOf block={block} ctx={ctx} /> : null}
          {block.error ? (
            <p className="state state-att place-refresh-failed">
              <span className="state-glyph" aria-hidden="true">
                ▲
              </span>
              <span>{ctx.t('place.refresh_failed', { message: block.error.message })}</span>
            </p>
          ) : null}
        </>
      ) : (
        <>
          {named ? <span className="place-status-name">{orRaw(ctx.t, `place.block.${id}`, id)}</span> : null}
          <StatusLine block={block} t={ctx.t} />
        </>
      )}
    </div>
  )
}

function Missing({ t }: { t: T }) {
  return <span className="place-missing">{t('place.missing')}</span>
}

function missingField(block: PlaceBlock, field: string): boolean {
  return (block.missing ?? []).some((item) => item.field === field)
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="place-fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

// ------------------------------------------------------------------ units

function temp(fmt: Formats, value: Num): string | null {
  return isNum(value) ? `${fmt.n(value, 1)} °C` : null
}

function kmh(fmt: Formats, ms: Num): string | null {
  return isNum(ms) ? `${fmt.n(Math.round(ms * 3.6), 0)} km/h` : null
}

function km(fmt: Formats, value: Num): string | null {
  if (!isNum(value)) return null
  return `${fmt.n(value, value < 10 ? 1 : 0)}`
}

function metres(fmt: Formats, value: Num): string | null {
  return isNum(value) ? `${fmt.n(value, 2)} m` : null
}

function mm(fmt: Formats, value: Num): string | null {
  return isNum(value) ? `${fmt.n(value, 1)} mm` : null
}

function pct(fmt: Formats, value: Num): string | null {
  return isNum(value) ? `${fmt.n(value, 0)} %` : null
}

function ugm3(fmt: Formats, value: Num): string | null {
  return isNum(value) ? `${fmt.n(value, 1)} µg/m³` : null
}

/** A wind direction: an arrow for the eye (where the wind blows TO), the
 * words for everyone ("from 140°"). */
function WindFrom({ deg, ctx }: { deg: number; ctx: Ctx }) {
  return (
    <span className="place-wind-from">
      <span
        className="place-arrow"
        aria-hidden="true"
        style={{ transform: `rotate(${(deg + 180) % 360}deg)` }}
      >
        ↑
      </span>
      <span>{ctx.t('place.wind.from', { deg: Math.round(deg) })}</span>
    </span>
  )
}

/** "Show more", the same look as the other panels. */
function MoreButton({
  open,
  onToggle,
  controls,
  describedBy,
  t,
}: {
  open: boolean
  onToggle: () => void
  controls: string
  describedBy: string
  t: T
}) {
  return (
    <button
      type="button"
      className="place-more-btn"
      aria-expanded={open}
      aria-controls={controls}
      aria-describedby={describedBy}
      onClick={onToggle}
    >
      {open ? t('more.hide') : t('more.show')}
    </button>
  )
}

// ------------------------------------------------------------------ section frame

/** Provenance of one block, behind "Show more": the exact date and its kind,
 * our own read, the attribution, the licence, and the server's raw reason. */
function Provenance({ id, block, ctx }: { id: PlaceBlockId; block: PlaceBlock; ctx: Ctx }) {
  const { t, fmt, clock } = ctx
  const fetchedAt = block.fetched_at && !Number.isNaN(Date.parse(block.fetched_at)) ? block.fetched_at : null
  return (
    <div className="place-prov" data-detail-block={id}>
      <h4 className="place-prov-name">{orRaw(t, `place.block.${id}`, id)}</h4>
      <dl className="place-facts">
        {block.as_of ? (
          <Fact label={t('place.label.as_of')}>
            <time className="place-token" dateTime={block.as_of}>
              {block.as_of}
            </time>{' '}
            <span className="place-muted">({block.as_of_kind})</span>
          </Fact>
        ) : null}
        {fetchedAt ? (
          <Fact label={t('place.label.read')}>
            <time className="place-read" dateTime={fetchedAt}>
              {t('place.fetched', { age: fmt.duration((clock.serverNow - Date.parse(fetchedAt)) / 1000) })}
            </time>
          </Fact>
        ) : null}
        {isNum(block.max_age_s) ? (
          <Fact label={t('place.label.max_age')}>{fmt.duration(block.max_age_s)}</Fact>
        ) : null}
        {block.error ? (
          <Fact label={t('place.label.reason')}>
            <span className="place-token">
              {block.error.code}: {block.error.reason}
            </span>
          </Fact>
        ) : null}
        {(block.missing ?? []).map((item) => (
          <Fact key={item.field} label={item.field}>
            {t('place.missing')} <span className="place-muted">({item.reason})</span>
          </Fact>
        ))}
        {sourcesOf(block).map((source) => (
          <div className="place-fact place-fact-source" key={`${source.name}|${source.url}`}>
            <dt>{t('place.label.attribution')}</dt>
            <dd>
              {source.attribution || source.name}
              {source.licence ? (
                <>
                  {' · '}
                  <span className="place-muted">{source.licence}</span>
                </>
              ) : null}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** Every source of a section, linked, ALWAYS visible: Open-Meteo's licence
 * wants the link next to the data, so this line can never be folded away. */
function SourceLine({ ids, ctx, extra }: { ids: PlaceBlockId[]; ctx: Ctx; extra?: ReactNode }) {
  const seen = new Set<string>()
  const sources: PlaceSource[] = []
  for (const id of ids) {
    for (const source of sourcesOf(ctx.report.blocks[id])) {
      const key = `${source.name}|${source.url}`
      if (seen.has(key)) continue
      seen.add(key)
      sources.push(source)
    }
  }
  if (sources.length === 0) return null
  return (
    <p className="place-sources">
      <span>{ctx.t('place.source')}</span>{' '}
      {sources.map((source, index) => (
        <span key={`${source.name}|${source.url}`}>
          {index > 0 ? ', ' : null}
          {/^https?:\/\//.test(source.url) ? (
            <a href={source.url} target="_blank" rel="noopener noreferrer">
              {source.name}
            </a>
          ) : (
            <span>{source.name}</span>
          )}
        </span>
      ))}
      {extra ? <span className="place-sources-extra"> {extra}</span> : null}
    </p>
  )
}

function Section({
  id,
  title,
  ids,
  ctx,
  children,
  details,
  sourceExtra,
  className,
}: {
  id: string
  title: string
  ids: PlaceBlockId[]
  ctx: Ctx
  children: ReactNode
  details?: ReactNode
  sourceExtra?: ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const titleId = useId()
  const detailId = useId()
  const blocks = ids.map((blockId) => ctx.report.blocks[blockId])
  // A section whose blocks are ALL not applicable is one muted line.
  const allNa = blocks.length > 0 && blocks.every((block) => block.status === 'not_applicable')
  const messages = allNa
    ? [...new Set(blocks.map((block) => block.error?.message || ctx.t('place.unavailable.unknown')))]
    : []
  return (
    <section
      className={`place-section${allNa ? ' is-na' : ''}${className ? ` ${className}` : ''}`}
      data-section={id}
      aria-labelledby={titleId}
    >
      <h3 className="place-section-title" id={titleId}>
        {title}
      </h3>
      {allNa ? (
        messages.map((message) => (
          <p className="state place-na" key={message}>
            <span className="state-glyph" aria-hidden="true">
              ○
            </span>
            <span>{ctx.t('place.na', { message })}</span>
          </p>
        ))
      ) : (
        <>
          <div className="place-section-body">{children}</div>
          <MoreButton
            open={open}
            onToggle={() => setOpen((value) => !value)}
            controls={detailId}
            describedBy={titleId}
            t={ctx.t}
          />
          <div className="place-more" id={detailId} hidden={!open}>
            {details}
            {ids.map((blockId) => (
              <Provenance key={blockId} id={blockId} block={ctx.report.blocks[blockId]} ctx={ctx} />
            ))}
          </div>
        </>
      )}
      <SourceLine ids={ids} ctx={ctx} extra={sourceExtra} />
    </section>
  )
}

// ------------------------------------------------------------------ 2 alerts

const SEVERITY_ORDER: Record<Severity, number> = {
  extreme: 0,
  severe: 1,
  moderate: 2,
  minor: 3,
  info: 4,
}

function AlertItem({ event, ctx }: { event: SosEvent; ctx: Ctx }) {
  const { t, lang, report, clock } = ctx
  const meta = SEVERITY_META[event.severity] ?? SEVERITY_META.info
  const at = Date.parse(event.time)
  const placed = isNum(event.lat) && isNum(event.lon)
  const away = placed
    ? distanceKm(report.query.lat, report.query.lon, event.lat as number, event.lon as number)
    : null
  return (
    <li className="place-alert">
      <span className={`sev sev-${event.severity} place-pill`}>
        <span className="glyph" aria-hidden="true">
          {meta.glyph}
        </span>
        {severityLabel(t, event.severity)}
      </span>
      <span className="place-alert-title">{event.title || event.place}</span>
      <span className="place-alert-meta">
        {away !== null
          ? t('feed.distance', { km: Math.round(away).toLocaleString(lang) })
          : event.country_code
            ? t('feed.countrywide', {
                country: countryName(lang, event.country_code) ?? event.country_code,
              })
            : t('feed.noarea')}
        {Number.isNaN(at) ? null : (
          <>
            {' · '}
            <time dateTime={event.time}>{formatAge(t, (clock.serverNow - at) / 1000)}</time>
          </>
        )}
        {' · '}
        {SOURCE_LABEL[event.source] ?? event.source}
      </span>
    </li>
  )
}

function AlertsSection({ ctx }: { ctx: Ctx }) {
  const { t, fmt, clock } = ctx
  const block = ctx.report.blocks.alerts_now
  const data = (block.data ?? {}) as AlertsData
  const events = useMemo(
    () =>
      [...(data.events ?? [])].sort(
        (a, b) =>
          (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) ||
          Date.parse(b.time) - Date.parse(a.time),
      ),
    [data.events],
  )
  const radius = isNum(data.radius_km) ? data.radius_km : ctx.report.query.radius_km
  const down = data.sources_down ?? []
  const age = ageSeconds(block, clock)
  const unplaced = isNum(data.unplaced_in_country) ? data.unplaced_in_country : 0
  return (
    <Section
      id="alerts"
      title={t('place.section.alerts')}
      ids={['alerts_now']}
      ctx={ctx}
      details={
        events.length > 3 || unplaced > 0 ? (
          <>
            {events.length > 3 ? (
              <ul className="place-alerts">
                {events.slice(3).map((event) => (
                  <AlertItem key={event.id} event={event} ctx={ctx} />
                ))}
              </ul>
            ) : null}
            {unplaced > 0 ? <p className="place-line">{t('place.alerts.unplaced', { n: unplaced })}</p> : null}
          </>
        ) : null
      }
    >
      <BlockRow id="alerts_now" block={block} ctx={ctx} chip={false} named={false}>
        {events.length > 0 ? (
          <ul className="place-alerts">
            {events.slice(0, 3).map((event) => (
              <AlertItem key={event.id} event={event} ctx={ctx} />
            ))}
          </ul>
        ) : down.length === 0 ? (
          <p className="state state-ok place-line">
            <span className="state-glyph" aria-hidden="true">
              ●
            </span>
            <span>{t('place.alerts.none', { km: fmt.n(radius, 0) })}</span>
          </p>
        ) : null}
        {down.length > 0 ? (
          <p className="state state-att place-line">
            <span className="state-glyph" aria-hidden="true">
              ▲
            </span>
            <span>
              {t('place.alerts.down', {
                sources: down.map((name) => SOURCE_LABEL[name] ?? name).join(', '),
              })}
            </span>
          </p>
        ) : null}
        <span className="place-chips">
          {block.as_of ? (
            <time className="place-asof" dateTime={block.as_of}>
              {t('place.alerts.checked', { age: fmt.duration(age ?? 0) })}
            </time>
          ) : null}
          {isLate(block, clock) ? <Late t={t} /> : null}
        </span>
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ 3 now

const COVER_ORDER = ['FEW', 'SCT', 'BKN', 'OVC', 'OVX', 'VV']
const COVER_KEY: Record<string, string> = {
  FEW: 'few',
  SCT: 'scattered',
  BKN: 'broken',
  OVC: 'overcast',
}

function cloudLabel(t: T, data: WeatherNowData): string | null {
  const clouds = data.clouds ?? []
  if (clouds.length > 0) {
    const top = [...clouds].sort(
      (a, b) => COVER_ORDER.indexOf(b.cover) - COVER_ORDER.indexOf(a.cover),
    )[0]
    const key = COVER_KEY[top.cover]
    return key ? t(`place.clouds.${key}`) : top.cover
  }
  return data.raw && /\b(CLR|SKC|NSC|NCD|CAVOK)\b/.test(data.raw) ? t('place.clouds.clear') : null
}

function NowSection({ ctx }: { ctx: Ctx }) {
  const { t, fmt } = ctx
  const block = ctx.report.blocks.weather_now
  const data = (block.data ?? {}) as WeatherNowData
  const station = data.station ?? null
  const clouds = cloudLabel(t, data)
  let wind: ReactNode = null
  if (data.calm) wind = t('place.now.calm')
  else if (isNum(data.wind_ms)) {
    wind = (
      <>
        {kmh(fmt, data.wind_ms)}
        {data.wind_variable ? (
          <span>{`, ${t('place.now.variable')}`}</span>
        ) : isNum(data.wind_from_deg) ? (
          <>
            {', '}
            <WindFrom deg={data.wind_from_deg} ctx={ctx} />
          </>
        ) : null}
        {isNum(data.gust_ms) ? <span>{`, ${t('place.wind.gusts', { speed: kmh(fmt, data.gust_ms) ?? '' })}`}</span> : null}
      </>
    )
  }
  const details =
    block.status === 'ok' ? (
      <dl className="place-facts">
        {data.raw ? (
          <Fact label={t('place.label.raw')}>
            <span className="place-token">{data.raw}</span>
          </Fact>
        ) : null}
        {isNum(data.qnh_hpa) ? (
          <Fact label={t('place.label.qnh')}>{`${fmt.n(data.qnh_hpa, 1)} hPa`}</Fact>
        ) : null}
        {isNum(data.visibility_km) ? (
          <Fact label={t('place.label.visibility')}>
            {data.visibility_at_least
              ? t('place.now.vis_at_least', { km: fmt.n(data.visibility_km, 1) })
              : `${fmt.n(data.visibility_km, 1)} km`}
          </Fact>
        ) : null}
        {isNum(data.dewpoint_c) ? (
          <Fact label={t('place.label.dewpoint')}>{temp(fmt, data.dewpoint_c)}</Fact>
        ) : null}
        {isNum(data.rh_pct) && (data.derived ?? []).includes('rh_pct') ? (
          <Fact label={t('place.label.humidity')}>{t('place.now.computed')}</Fact>
        ) : null}
        {data.wx ? (
          <Fact label={t('place.label.weather')}>
            <span className="place-token">{data.wx}</span>
          </Fact>
        ) : null}
        {data.flight_category ? (
          <Fact label={t('place.label.flight_category')}>{data.flight_category}</Fact>
        ) : null}
        {isNum(station?.elevation_m) ? (
          <Fact label={t('place.label.elevation')}>{`${fmt.n(station.elevation_m, 0)} m`}</Fact>
        ) : null}
      </dl>
    ) : null
  return (
    <Section id="now" title={t('place.section.now')} ids={['weather_now']} ctx={ctx} details={details}>
      <BlockRow id="weather_now" block={block} ctx={ctx} chip={false} named={false}>
        <dl className="place-facts place-facts-main">
          {isNum(data.temp_c) ? <Fact label={t('place.label.temperature')}>{temp(fmt, data.temp_c)}</Fact> : null}
          {wind !== null ? <Fact label={t('place.label.wind')}>{wind}</Fact> : null}
          {isNum(data.rh_pct) ? <Fact label={t('place.label.humidity')}>{pct(fmt, data.rh_pct)}</Fact> : null}
          {clouds ? <Fact label={t('place.label.clouds')}>{clouds}</Fact> : null}
        </dl>
        <p className="place-line place-where">
          {station?.icao ? (
            <span className="place-token">
              {t('place.now.station', {
                station: station.icao,
                km: km(fmt, station.distance_km) ?? '?',
              })}
            </span>
          ) : null}{' '}
          <AsOf block={block} ctx={ctx} />
        </p>
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ 4 today

/** The day under way: the first day, incomplete, already started. Its max
 * and min cover only what is left of it, and it is labelled so. */
function dayUnderWay(days: DayStep[], serverNow: number): DayStep | null {
  const first = days[0]
  if (!first || first.complete) return null
  const end = Date.parse(first.end)
  return !Number.isNaN(end) && end - 24 * 3600 * 1000 <= serverNow ? first : null
}

function DayLine({ day, ctx, partial }: { day: DayStep; ctx: Ctx; partial: boolean }) {
  const { t, fmt } = ctx
  const max = temp(fmt, day.temp_max_c)
  const min = temp(fmt, day.temp_min_c)
  return (
    <li className="place-day" data-partial={partial ? 'true' : undefined}>
      <span className="place-day-name">
        {partial ? t('place.day.partial', { time: fmt.time(day.start) }) : fmt.weekday(day.date)}
      </span>
      <Sky t={t} symbol={day.symbol} />
      {max && min ? (
        <span className="place-day-temps">
          {partial ? t('place.day.range', { min, max }) : t('place.day.maxmin', { max, min })}
        </span>
      ) : null}
      {isNum(day.precip_mm) ? (
        <span className="place-day-precip">{t('place.day.precip', { mm: mm(fmt, day.precip_mm) ?? '' })}</span>
      ) : null}
    </li>
  )
}

function upcomingHours(hours: HourStep[], serverNow: number): HourStep[] {
  return hours.filter((hour) => {
    const at = Date.parse(hour.time)
    return !Number.isNaN(at) && at + 3600 * 1000 > serverNow
  })
}

function TodaySection({ ctx }: { ctx: Ctx }) {
  const { t, fmt, clock } = ctx
  const daily = ctx.report.blocks.forecast_daily
  const hourly = ctx.report.blocks.forecast_hourly
  const days = ((daily.data ?? {}) as DailyData).days ?? []
  const hourlyData = (hourly.data ?? {}) as HourlyData
  const under = dayUnderWay(days, clock.serverNow)
  const next = days.filter((day) => day.complete).slice(0, 2)
  const strip = upcomingHours(hourlyData.hours ?? [], clock.serverNow).slice(0, 12)
  return (
    <Section
      id="today"
      title={t('place.section.today')}
      ids={['forecast_daily', 'forecast_hourly']}
      ctx={ctx}
    >
      <BlockRow id="forecast_daily" block={daily} ctx={ctx}>
        <ul className="place-days">
          {under ? <DayLine day={under} ctx={ctx} partial /> : null}
          {next.map((day) => (
            <DayLine key={day.date} day={day} ctx={ctx} partial={false} />
          ))}
        </ul>
      </BlockRow>
      {/* one MET response feeds both blocks: its date is said once */}
      <BlockRow
        id="forecast_hourly"
        block={hourly}
        ctx={ctx}
        chip={daily.status !== 'ok' || hourly.as_of !== daily.as_of}
      >
        {strip.length > 0 ? <p className="place-line place-muted">{t('place.today.strip')}</p> : null}
        <ol className="place-strip">
          {strip.map((hour) => (
            <li key={hour.time} className="place-strip-hour">
              <time dateTime={hour.time}>{fmt.clock(hour.time)}</time>
              <span className="place-strip-temp">{temp(fmt, hour.temp_c) ?? ''}</span>
              {isNum(hour.precip_mm) && hour.precip_mm > 0 ? (
                <span className="place-strip-precip">{mm(fmt, hour.precip_mm)}</span>
              ) : null}
            </li>
          ))}
        </ol>
        {isNum(hourlyData.model_elevation_m) ? (
          <p className="place-line place-muted">
            {t('place.model.elevation', { m: fmt.n(hourlyData.model_elevation_m, 0) })}
          </p>
        ) : null}
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ tables

/** A small table that becomes stacked rows on a narrow card (CSS): each cell
 * carries its column name in `data-label` for that layout. */
function Table({
  caption,
  columns,
  rows,
}: {
  caption: string
  columns: string[]
  rows: { key: string; cells: ReactNode[] }[]
}) {
  return (
    <table className="place-table">
      <caption>{caption}</caption>
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column} scope="col">
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            {row.cells.map((cell, index) => (
              <td key={index} data-label={columns[index]}>
                <span>{cell}</span>
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ------------------------------------------------------------------ 5 wind

function WindSection({ ctx }: { ctx: Ctx }) {
  const { t, fmt } = ctx
  const block = ctx.report.blocks.wind
  const data = (block.data ?? {}) as WindData
  const max = data.max_48h ?? null
  const gustMissing = missingField(block, 'gust_ms') || !isNum(max?.gust_ms)
  const hours = data.hours ?? []
  return (
    <Section
      id="wind"
      title={t('place.section.wind')}
      ids={['wind']}
      ctx={ctx}
      details={
        block.status === 'ok' && hours.length > 0 ? (
          <Table
            caption={t('place.section.wind')}
            columns={[t('place.label.time'), t('place.label.wind'), t('place.label.gust')]}
            rows={hours.map((hour) => ({
              key: hour.time,
              cells: [
                <time key="t" dateTime={hour.time}>
                  {fmt.dateTime(hour.time)}
                </time>,
                <span key="w">
                  {kmh(fmt, hour.wind_ms) ?? <Missing t={t} />}
                  {isNum(hour.wind_from_deg) ? (
                    <>
                      {', '}
                      <WindFrom deg={hour.wind_from_deg} ctx={ctx} />
                    </>
                  ) : null}
                </span>,
                kmh(fmt, hour.gust_ms) ?? <Missing key="g" t={t} />,
              ],
            }))}
          />
        ) : null
      }
    >
      <BlockRow id="wind" block={block} ctx={ctx} named={false}>
        {max && isNum(max.wind_ms) ? (
          <p className="place-line place-headline">
            {t('place.wind.max', {
              speed: kmh(fmt, max.wind_ms) ?? '',
              time: max.time ? fmt.dateTime(max.time) : '',
            })}
          </p>
        ) : null}
        <p className="place-line">
          {gustMissing ? (
            <>
              {t('place.label.gust')}: <Missing t={t} />
            </>
          ) : (
            t('place.wind.gusts', { speed: kmh(fmt, max?.gust_ms) ?? '' })
          )}
        </p>
        {data.fallback ? (
          <p className="state state-att place-line">
            <span className="state-glyph" aria-hidden="true">
              ▲
            </span>
            <span>{t('place.wind.fallback', { from: data.fallback.from })}</span>
          </p>
        ) : null}
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ 6 forecast

function ForecastSection({ ctx }: { ctx: Ctx }) {
  const { t, fmt, clock } = ctx
  const daily = ctx.report.blocks.forecast_daily
  const hourly = ctx.report.blocks.forecast_hourly
  const days = ((daily.data ?? {}) as DailyData).days ?? []
  const week = days.filter((day) => day.complete).slice(0, 7)
  const hours = upcomingHours(((hourly.data ?? {}) as HourlyData).hours ?? [], clock.serverNow).slice(0, 48)
  const maxes = week.map((day) => day.temp_max_c).filter(isNum)
  const mins = week.map((day) => day.temp_min_c).filter(isNum)
  const rain = week.map((day) => day.precip_mm).filter(isNum)
  const headline =
    week.length > 0 && maxes.length === week.length && mins.length === week.length
      ? t('place.forecast.headline', {
          n: week.length,
          min: temp(fmt, Math.min(...mins)) ?? '',
          max: temp(fmt, Math.max(...maxes)) ?? '',
          mm:
            rain.length === week.length
              ? (mm(fmt, Math.round(rain.reduce((a, b) => a + b, 0) * 10) / 10) ?? '')
              : t('place.missing'),
        })
      : null
  return (
    <Section
      id="forecast"
      title={t('place.section.forecast')}
      ids={['forecast_daily', 'forecast_hourly']}
      ctx={ctx}
      details={
        <>
          {daily.status === 'ok' && week.length > 0 ? (
            <Table
              caption={t('place.forecast.days')}
              columns={[
                t('place.label.date'),
                t('place.label.sky'),
                t('place.label.max'),
                t('place.label.min'),
                t('place.label.precip'),
              ]}
              rows={week.map((day) => ({
                key: day.date,
                cells: [
                  fmt.weekday(day.date),
                  <Sky key="s" t={t} symbol={day.symbol} />,
                  temp(fmt, day.temp_max_c) ?? <Missing key="x" t={t} />,
                  temp(fmt, day.temp_min_c) ?? <Missing key="n" t={t} />,
                  mm(fmt, day.precip_mm) ?? <Missing key="p" t={t} />,
                ],
              }))}
            />
          ) : null}
          {hourly.status === 'ok' && hours.length > 0 ? (
            <>
              <Table
                caption={t('place.forecast.hours')}
                columns={[
                  t('place.label.time'),
                  t('place.label.temperature'),
                  t('place.label.feels_like'),
                  t('place.label.humidity'),
                  t('place.label.precip'),
                  t('place.label.cloud_cover'),
                  t('place.label.uv_clear_sky'),
                ]}
                rows={hours.map((hour) => ({
                  key: hour.time,
                  cells: [
                    <time key="t" dateTime={hour.time}>
                      {fmt.dateTime(hour.time)}
                    </time>,
                    temp(fmt, hour.temp_c) ?? <Missing key="a" t={t} />,
                    temp(fmt, hour.feels_like_c) ?? <Missing key="b" t={t} />,
                    pct(fmt, hour.rh_pct) ?? <Missing key="c" t={t} />,
                    mm(fmt, hour.precip_mm) ?? <Missing key="d" t={t} />,
                    pct(fmt, hour.cloud_pct) ?? <Missing key="e" t={t} />,
                    isNum(hour.uv_clear_sky) ? fmt.n(hour.uv_clear_sky, 1) : <Missing key="f" t={t} />,
                  ],
                }))}
              />
              <p className="place-line place-muted">{t('place.forecast.uv_clear')}</p>
              {missingField(hourly, 'precip_probability_pct') ? (
                <p className="place-line place-muted">
                  {t('place.label.precip_probability')}: <Missing t={t} />
                </p>
              ) : null}
            </>
          ) : null}
        </>
      }
    >
      <BlockRow id="forecast_daily" block={daily} ctx={ctx}>
        {headline ? <p className="place-line place-headline">{headline}</p> : null}
      </BlockRow>
      {hourly.status !== 'ok' ? <BlockRow id="forecast_hourly" block={hourly} ctx={ctx} /> : null}
    </Section>
  )
}

// ------------------------------------------------------------------ 7 sea

function Away({ cell, ctx }: { cell: Cell | null | undefined; ctx: Ctx }) {
  if (!cell || !isNum(cell.distance_km) || cell.distance_km <= 5) return null
  return (
    <span className="place-away">
      {ctx.t('place.sea.distance', { km: km(ctx.fmt, cell.distance_km) ?? '' })}
    </span>
  )
}

function currentStep<S extends { time: string }>(steps: S[], serverNow: number): S | null {
  let found: S | null = null
  for (const step of steps) {
    const at = Date.parse(step.time)
    if (Number.isNaN(at)) continue
    if (at <= serverNow) found = step
    else return found ?? step
  }
  return found
}

function SeaSection({ ctx }: { ctx: Ctx }) {
  const { t, fmt, clock } = ctx
  const blocks = ctx.report.blocks
  const waves = blocks['sea.waves']
  const level = blocks['sea.level']
  const sst = blocks['sea.temperature']
  const buoy = blocks['sea.buoy']
  const wavesData = (waves.data ?? {}) as WavesData
  const levelData = (level.data ?? {}) as LevelData
  const sstData = (sst.data ?? {}) as SstData
  const buoyData = (buoy.data ?? {}) as BuoyData
  const waveNow = currentStep(wavesData.hours ?? [], clock.serverNow)
  const nextExtremes = (levelData.extremes ?? [])
    .filter((item) => Date.parse(item.time) > clock.serverNow)
    .slice(0, 2)
  const levelNow = currentStep(levelData.hours ?? [], clock.serverNow)
  return (
    <Section
      id="sea"
      title={t('place.section.sea')}
      ids={['sea.waves', 'sea.temperature', 'sea.level', 'sea.buoy']}
      ctx={ctx}
      details={
        <>
          {level.status === 'ok' ? (
            <>
              <p className="place-line">{`${t('place.block.sea.level')}: ${t('place.sea.msl')}`}</p>
              {levelNow && isNum(levelNow.current_ms) ? (
                <p className="place-line">
                  {t('place.sea.current', {
                    speed: `${fmt.n(levelNow.current_ms, 2)} m/s`,
                    deg: isNum(levelNow.current_to_deg) ? Math.round(levelNow.current_to_deg) : '?',
                  })}
                </p>
              ) : null}
            </>
          ) : null}
          {waves.status === 'ok' && waveNow && (isNum(waveNow.period_s) || isNum(waveNow.from_deg)) ? (
            <dl className="place-facts">
              {isNum(waveNow.period_s) ? (
                <Fact label={t('place.label.period')}>{`${fmt.n(waveNow.period_s, 1)} s`}</Fact>
              ) : null}
              {isNum(waveNow.from_deg) ? (
                <Fact label={t('place.label.direction')}>
                  <WindFrom deg={waveNow.from_deg} ctx={ctx} />
                </Fact>
              ) : null}
            </dl>
          ) : null}
          <BlockRow id="sea.buoy" block={buoy} ctx={ctx}>
            <p className="place-line">
              {buoyData.station?.type && /buoy/i.test(buoyData.station.type)
                ? t('place.sea.buoy')
                : t('place.sea.station')}
              {buoyData.station?.id ? (
                <span className="place-token">{` ${buoyData.station.id}`}</span>
              ) : null}
              {buoyData.station?.name ? ` (${buoyData.station.name})` : null}
              {isNum(buoyData.station?.distance_km) ? (
                <span>{`, ${t('place.sea.distance', { km: km(fmt, buoyData.station.distance_km) ?? '' })}`}</span>
              ) : null}
            </p>
            <dl className="place-facts">
              {isNum(buoyData.wave_height_m) ? (
                <Fact label={t('place.label.waves')}>{metres(fmt, buoyData.wave_height_m)}</Fact>
              ) : null}
              {isNum(buoyData.water_temp_c) ? (
                <Fact label={t('place.label.water')}>{temp(fmt, buoyData.water_temp_c)}</Fact>
              ) : null}
              {isNum(buoyData.wind_ms) ? (
                <Fact label={t('place.label.wind')}>
                  {kmh(fmt, buoyData.wind_ms)}
                  {isNum(buoyData.wind_from_deg) ? (
                    <>
                      {', '}
                      <WindFrom deg={buoyData.wind_from_deg} ctx={ctx} />
                    </>
                  ) : null}
                </Fact>
              ) : null}
              {isNum(buoyData.gust_ms) ? (
                <Fact label={t('place.label.gust')}>{kmh(fmt, buoyData.gust_ms)}</Fact>
              ) : null}
              {isNum(buoyData.air_temp_c) ? (
                <Fact label={t('place.label.temperature')}>{temp(fmt, buoyData.air_temp_c)}</Fact>
              ) : null}
              {isNum(buoyData.pressure_hpa) ? (
                <Fact label={t('place.label.pressure')}>{`${fmt.n(buoyData.pressure_hpa, 1)} hPa`}</Fact>
              ) : null}
              {isNum(buoyData.tide_m) ? (
                <Fact label={t('place.label.tide')}>{metres(fmt, buoyData.tide_m)}</Fact>
              ) : null}
            </dl>
          </BlockRow>
        </>
      }
    >
      <BlockRow id="sea.waves" block={waves} ctx={ctx}>
        {waveNow && isNum(waveNow.height_m) ? (
          <p className="place-line place-headline">
            {t('place.sea.waves', {
              h: metres(fmt, waveNow.height_m) ?? '',
              max: metres(fmt, wavesData.max_48h?.height_m) ?? metres(fmt, waveNow.height_m) ?? '',
            })}{' '}
            <Away cell={wavesData.cell} ctx={ctx} />{' '}
            <span className="place-muted">{t('place.sea.model')}</span>
          </p>
        ) : null}
      </BlockRow>
      <BlockRow id="sea.temperature" block={sst} ctx={ctx}>
        {isNum(sstData.sst_c) ? (
          <p className="place-line place-headline">
            {t('place.sea.water', { t: temp(fmt, sstData.sst_c) ?? '' })}{' '}
            <Away cell={sstData.cell} ctx={ctx} />{' '}
            <span className="place-muted">{t('place.sea.satellite')}</span>
          </p>
        ) : null}
      </BlockRow>
      <BlockRow id="sea.level" block={level} ctx={ctx}>
        {nextExtremes.map((item) => (
          <p className="place-line" key={item.time}>
            {t('place.sea.next', {
              what: t(`place.sea.extreme.${item.type === 'low' ? 'low' : 'high'}`),
              time: fmt.dateTime(item.time),
              level: metres(fmt, item.level_m) ?? '',
            })}{' '}
            <Away cell={levelData.cell} ctx={ctx} />
          </p>
        ))}
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ 8 air

const POLLUTANT_LABEL: Record<string, string> = {
  pm2_5: 'PM2.5',
  pm10: 'PM10',
  o3: 'O₃',
  no2: 'NO₂',
  so2: 'SO₂',
  co: 'CO',
  dust: 'dust',
}

function AirSection({ ctx }: { ctx: Ctx }) {
  const { t, fmt } = ctx
  const air = ctx.report.blocks.air
  const pollen = ctx.report.blocks.pollen
  const data = (air.data ?? {}) as AirData
  const now = data.now ?? {}
  const eu = data.aqi?.eu ?? null
  const us = data.aqi?.us ?? null
  const pollenNow = ((pollen.data ?? {}) as PollenData).now ?? {}
  const pollutants: [string, Num][] = [
    ['PM2.5', now.pm2_5_ugm3],
    ['PM10', now.pm10_ugm3],
    ['O₃', now.o3_ugm3],
    ['NO₂', now.no2_ugm3],
    ['SO₂', now.so2_ugm3],
    ['CO', now.co_ugm3],
    [t('place.label.dust'), now.dust_ugm3],
  ]
  const dominant = (code: string | null | undefined) =>
    code ? (POLLUTANT_LABEL[code] ?? code) : null
  return (
    <Section
      id="air"
      title={t('place.section.air')}
      ids={['air', 'pollen']}
      ctx={ctx}
      details={
        <>
          {air.status === 'ok' ? (
            <dl className="place-facts">
              {pollutants.map(([label, value]) =>
                isNum(value) ? (
                  <Fact key={label} label={label}>
                    {ugm3(fmt, value)}
                  </Fact>
                ) : null,
              )}
              {isNum(now.aod_550) ? (
                <Fact label={t('place.label.aod')}>{fmt.n(now.aod_550, 2)}</Fact>
              ) : null}
              {isNum(now.uv_index_clear_sky) ? (
                <Fact label={t('place.label.uv_clear_sky')}>{fmt.n(now.uv_index_clear_sky, 1)}</Fact>
              ) : null}
              {us && isNum(us.value) ? (
                <Fact label={t('place.label.us_aqi')}>
                  {`${fmt.n(us.value, 0)}${us.category ? ` · ${orRaw(t, `place.air.us.${us.category}`, us.category)}` : ''}`}
                  {us.scale ? <span className="place-muted">{` (${us.scale})`}</span> : null}
                </Fact>
              ) : null}
              {eu?.scale ? <Fact label={t('place.label.eu_aqi')}>{eu.scale}</Fact> : null}
            </dl>
          ) : null}
          <BlockRow id="pollen" block={pollen} ctx={ctx}>
            <dl className="place-facts">
              {Object.entries(pollenNow)
                .filter(([key]) => key.endsWith('_grains_m3'))
                .map(([key, value]) => (
                  <Fact key={key} label={orRaw(t, `place.pollen.${key.replace('_grains_m3', '')}`, key)}>
                    {isNum(value) ? `${fmt.n(value, 1)} grains/m³` : <Missing t={t} />}
                  </Fact>
                ))}
            </dl>
          </BlockRow>
        </>
      }
    >
      <BlockRow id="air" block={air} ctx={ctx}>
        {eu?.band ? (
          <p className="place-line place-headline">
            {t('place.air.eu', { band: orRaw(t, `place.air.eu.${eu.band}`, eu.band) })}
            {dominant(eu.dominant) ? ` · ${t('place.air.dominant', { pollutant: dominant(eu.dominant) ?? '' })}` : null}
          </p>
        ) : isNum(now.pm2_5_ugm3) ? (
          <p className="place-line place-headline">{`PM2.5 ${ugm3(fmt, now.pm2_5_ugm3)}`}</p>
        ) : null}
        {isNum(now.uv_index) ? <p className="place-line">{t('place.air.uv', { n: fmt.n(now.uv_index, 1) })}</p> : null}
        <p className="place-line place-muted">{t('place.air.model')}</p>
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ 9 risk

const WATER_INDICATORS = ['riverine_flood', 'coastal_flood', 'drought_risk', 'water_stress'] as const

function RiskSection({ ctx }: { ctx: Ctx }) {
  const { t, fmt, lang, report } = ctx
  const blocks = report.blocks
  const hotspots = blocks['fire.hotspots']
  const danger = blocks['fire.danger']
  const drought = blocks.drought
  const water = blocks['risk.water']
  const country = blocks['risk.country']
  const hot = (hotspots.data ?? {}) as HotspotsData
  const dangerData = (danger.data ?? {}) as FireDangerData
  const dry = (drought.data ?? {}) as DroughtData
  const waterData = (water.data ?? {}) as WaterData
  const countryData = (country.data ?? {}) as CountryRiskData
  const within100 = hot.within_km?.['100']
  const today = dangerData.days?.[0] ?? null
  const countryLabel =
    countryName(lang, report.place?.country_code ?? null) ?? countryData.iso3 ?? report.place?.country_code ?? ''
  return (
    <Section
      id="risk"
      title={t('place.section.risk')}
      ids={['fire.danger', 'fire.hotspots', 'drought', 'risk.water', 'risk.country']}
      ctx={ctx}
      details={
        <>
          {hotspots.status === 'ok' && hot.nearest && isNum(hot.nearest.distance_km) ? (
            <p className="place-line">
              {t('place.fire.nearest', { km: km(fmt, hot.nearest.distance_km) ?? '' })}
              {hot.nearest.time ? (
                <>
                  {' · '}
                  <time dateTime={hot.nearest.time}>{fmt.dateTime(hot.nearest.time)}</time>
                </>
              ) : null}
              {hot.nearest.satellite ? ` · ${hot.nearest.satellite}` : null}
            </p>
          ) : null}
          {drought.status === 'ok' && dry.soil_moisture_anomaly && isNum(dry.soil_moisture_anomaly.value) ? (
            <p className="place-line">{t('place.drought.soil', { v: fmt.n(dry.soil_moisture_anomaly.value, 2) })}</p>
          ) : null}
          <BlockRow id="risk.water" block={water} ctx={ctx}>
            <p className="place-line place-muted">{t('place.risk.unit.basin')}</p>
            <dl className="place-facts">
              {WATER_INDICATORS.map((key) => {
                const item = waterData[key]
                if (!item) return null
                return (
                  <Fact key={key} label={t(`place.water.indicator.${key}`)}>
                    {item.scale ? orRaw(t, `place.water.scale.${item.scale}`, item.scale) : <Missing t={t} />}
                    {item.label ? <span className="place-token place-muted">{` (WRI: ${item.label})`}</span> : null}
                  </Fact>
                )
              })}
            </dl>
          </BlockRow>
          <BlockRow id="risk.country" block={country} ctx={ctx}>
            <p className="place-line place-muted">
              {t('place.risk.unit.country', { country: countryLabel })}
              {countryData.release ? ` · ${countryData.release}` : null}
            </p>
            <dl className="place-facts">
              {isNum(countryData.inform) ? (
                <Fact label={t('place.risk.inform')}>{`${fmt.n(countryData.inform, 1)} / 10`}</Fact>
              ) : null}
              {(['hazard_exposure', 'vulnerability', 'lack_of_coping_capacity'] as const).map((key) =>
                isNum(countryData[key]) ? (
                  <Fact key={key} label={t(`place.risk.${key}`)}>{`${fmt.n(countryData[key] as number, 1)} / 10`}</Fact>
                ) : null,
              )}
              {Object.entries(countryData.hazards ?? {}).map(([key, value]) => (
                <Fact key={key} label={orRaw(t, `place.risk.hazard.${key}`, key)}>
                  {isNum(value) ? `${fmt.n(value, 1)} / 10` : <Missing t={t} />}
                </Fact>
              ))}
            </dl>
          </BlockRow>
        </>
      }
    >
      <BlockRow id="fire.danger" block={danger} ctx={ctx}>
        {today ? (
          <p className="place-line place-headline">
            {t('place.fire.danger', {
              class: today.class ? orRaw(t, `place.fire.${today.class}`, today.class) : t('place.missing'),
            })}
          </p>
        ) : null}
      </BlockRow>
      <BlockRow id="fire.hotspots" block={hotspots} ctx={ctx}>
        {isNum(within100) ? (
          <p className="place-line">
            {within100 > 0
              ? t('place.fire.hotspots', {
                  n: fmt.n(within100, 0),
                  km: 100,
                  time: hot.window_end ? fmt.dateTime(hot.window_end) : '',
                })
              : t('place.fire.none', { km: 100, time: hot.window_end ? fmt.dateTime(hot.window_end) : '' })}
          </p>
        ) : null}
      </BlockRow>
      <BlockRow id="drought" block={drought} ctx={ctx}>
        {dry.spi_3?.class ? (
          <p className="place-line">
            {t('place.drought.spi', { class: orRaw(t, `place.drought.${dry.spi_3.class}`, dry.spi_3.class) })}
          </p>
        ) : null}
        {dry.agri_risk && isNum(dry.agri_risk.class) ? (
          <p className="place-line">
            {orRaw(t, `place.drought.agri.${dry.agri_risk.class}`, dry.agri_risk.label ?? String(dry.agri_risk.class))}
          </p>
        ) : null}
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ 10 history

function Decades({ rows, ctx }: { rows: DecadeCount[]; ctx: Ctx }) {
  const { t, fmt } = ctx
  if (rows.length === 0) return null
  return (
    <ul className="place-decades">
      {rows.map((row) => (
        <li key={row.decade}>
          <span className="place-decade">{t('place.history.decade', { decade: row.decade })}</span>{' '}
          <span className="place-count">{fmt.n(row.count, 0)}</span>
          {row.early ? <span className="place-muted">{` · ${t('place.history.incomplete')}`}</span> : null}
          {row.partial ? <span className="place-muted">{` · ${t('place.history.partial')}`}</span> : null}
        </li>
      ))}
    </ul>
  )
}

function QuakeList({ title, rows, ctx }: { title: string; rows: QuakeRow[]; ctx: Ctx }) {
  const { fmt, t } = ctx
  if (rows.length === 0) return null
  return (
    <>
      <h4 className="place-sub">{title}</h4>
      <ul className="place-list">
        {rows.map((row) => {
          const label = `M${isNum(row.mag) ? fmt.n(row.mag, 1) : '?'}${row.mag_type ? ` ${row.mag_type}` : ''}`
          return (
            <li key={row.id}>
              {row.url ? (
                <a href={row.url} target="_blank" rel="noopener noreferrer">
                  {label}
                </a>
              ) : (
                label
              )}
              {' · '}
              <time dateTime={row.time}>{fmt.day(row.time)}</time>
              {isNum(row.distance_km) ? ` · ${t('place.sea.distance', { km: km(fmt, row.distance_km) ?? '' })}` : null}
              {isNum(row.bearing_deg) ? ` (${Math.round(row.bearing_deg)}°)` : null}
              {isNum(row.depth_km) ? ` · ${t('place.history.depth', { km: fmt.n(row.depth_km, 0) })}` : null}
            </li>
          )
        })}
      </ul>
    </>
  )
}

function Deaths({ own, total, ctx }: { own: Num; total: Num; ctx: Ctx }) {
  const { t, fmt } = ctx
  return (
    <>
      {isNum(own) ? ` · ${t('place.history.deaths', { n: fmt.n(own, 0) })}` : null}
      {isNum(total) && total !== own ? (
        <span className="place-muted">{` · ${t('place.history.incl_linked', { n: fmt.n(total, 0) })}`}</span>
      ) : null}
    </>
  )
}

function HistorySection({ ctx }: { ctx: Ctx }) {
  const { t, fmt, report } = ctx
  const b = report.blocks
  const quakes = b['history.earthquakes']
  const significant = b['history.significant_earthquakes']
  const tsunamis = b['history.tsunamis']
  const eruptions = b['history.eruptions']
  const volcanoes = b['history.volcanoes']
  const cyclones = b['history.cyclones']
  const q = (quakes.data ?? {}) as QuakesData
  const s = (significant.data ?? {}) as SignificantData
  const ts = (tsunamis.data ?? {}) as TsunamisData
  const er = (eruptions.data ?? {}) as SignificantData
  const vo = (volcanoes.data ?? {}) as VolcanoesData
  const cy = (cyclones.data ?? {}) as CyclonesData
  const radius = isNum(q.radius_km) ? q.radius_km : report.query.radius_km
  const kmText = fmt.n(radius, 0)

  const line = (count: Num, label: string, sentence: () => string) =>
    isNum(count) ? (
      <p className="place-line">
        {count > 0 ? sentence() : t('place.history.none', { what: label, km: kmText })}
      </p>
    ) : null

  return (
    <Section
      id="history"
      title={t('place.section.history')}
      ids={[
        'history.earthquakes',
        'history.significant_earthquakes',
        'history.tsunamis',
        'history.eruptions',
        'history.volcanoes',
        'history.cyclones',
      ]}
      ctx={ctx}
      sourceExtra={carriesShareAlike(quakes) ? t('place.licence.sharealike') : null}
      details={
        <>
          {quakes.status === 'ok' ? (
            <>
              <QuakeList title={t('place.history.strongest')} rows={q.strongest ?? []} ctx={ctx} />
              <QuakeList title={t('place.history.latest')} rows={q.latest ?? []} ctx={ctx} />
              <Decades rows={q.by_decade ?? []} ctx={ctx} />
            </>
          ) : null}
          {significant.status === 'ok' && (s.events ?? []).length > 0 ? (
            <>
              <h4 className="place-sub">{t('place.history.label.significant')}</h4>
              <ul className="place-list">
                {(s.events ?? []).map((row, index) => (
                  <li key={`${row.date.year}-${index}`}>
                    {fmt.partial(row.date, t)}
                    {row.name ? ` · ${row.name}` : null}
                    {isNum(row.mag) ? ` · M${fmt.n(row.mag, 1)}` : null}
                    <Deaths own={row.deaths} total={row.deaths_total} ctx={ctx} />
                    {isNum(row.distance_km) ? ` · ${t('place.sea.distance', { km: km(fmt, row.distance_km) ?? '' })}` : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {tsunamis.status === 'ok' ? (
            <>
              {(ts.sources?.events ?? []).length > 0 ? (
                <>
                  <h4 className="place-sub">{t('place.history.label.tsunamis')}</h4>
                  <ul className="place-list">
                    {(ts.sources?.events ?? []).map((row, index) => (
                      <li key={`${row.date.year}-${index}`}>
                        {fmt.partial(row.date, t)}
                        {row.name ? ` · ${row.name}` : null}
                        {row.cause ? ` · ${orRaw(t, `place.history.cause.${row.cause}`, row.cause)}` : null}
                        {isNum(row.max_height_m) ? ` · ${metres(fmt, row.max_height_m)}` : null}
                        <Deaths own={row.deaths} total={row.deaths_total} ctx={ctx} />
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              {isNum(ts.sources?.excluded_doubtful) && (ts.sources?.excluded_doubtful ?? 0) > 0 ? (
                <p className="place-line place-muted">
                  {t('place.history.doubtful', { n: fmt.n(ts.sources?.excluded_doubtful as number, 0) })}
                </p>
              ) : null}
              {ts.runups && isNum(ts.runups.total) ? (
                <p className="place-line">
                  {t('place.history.runups', {
                    n: fmt.n(ts.runups.total, 0),
                    km: fmt.n(isNum(ts.runups.within_km) ? ts.runups.within_km : radius, 0),
                  })}
                </p>
              ) : null}
            </>
          ) : null}
          {eruptions.status === 'ok' && (er.events ?? []).length > 0 ? (
            <>
              <h4 className="place-sub">{t('place.history.label.eruptions')}</h4>
              <ul className="place-list">
                {(er.events ?? []).map((row, index) => (
                  <li key={`${row.date.year}-${index}`}>
                    {fmt.partial(row.date, t)}
                    {row.name ? ` · ${row.name}` : null}
                    {isNum(row.vei) ? ` · VEI ${fmt.n(row.vei, 0)}` : null}
                    <Deaths own={row.deaths} total={row.deaths_total} ctx={ctx} />
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {volcanoes.status === 'ok' && (vo.volcanoes ?? []).length > 0 ? (
            <>
              <h4 className="place-sub">{t('place.history.label.volcanoes')}</h4>
              <ul className="place-list">
                {(vo.volcanoes ?? []).map((row) => (
                  <li key={`${row.number ?? row.name}`}>
                    {row.name}
                    {row.type ? ` · ${row.type}` : null}
                    {row.continuing
                      ? ` · ${t('place.history.continuing')}`
                      : isNum(row.last_eruption_year)
                        ? ` · ${fmt.partial({ year: row.last_eruption_year }, t)}`
                        : null}
                    {isNum(row.distance_km) ? ` · ${t('place.sea.distance', { km: km(fmt, row.distance_km) ?? '' })}` : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {cyclones.status === 'ok' ? (
            <>
              {(cy.strongest ?? []).length > 0 ? (
                <>
                  <h4 className="place-sub">{t('place.history.strongest')}</h4>
                  <ul className="place-list">
                    {(cy.strongest ?? []).map((row, index) => (
                      <li key={row.sid ?? index}>
                        {row.name || row.sid}
                        {isNum(row.season) ? ` ${row.season}` : null}
                        {isNum(row.max_wind_within_radius_kt)
                          ? ` · ${t('place.history.wind_here', { kt: fmt.n(row.max_wind_within_radius_kt, 0) })}`
                          : null}
                        {row.wind_source ? <span className="place-muted">{` (${row.wind_source})`}</span> : null}
                        {isNum(row.closest_km) ? ` · ${t('place.sea.distance', { km: km(fmt, row.closest_km) ?? '' })}` : null}
                        {row.nature_at_closest && row.nature_at_closest !== 'TS'
                          ? ` · ${t('place.history.posttropical')}`
                          : null}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              {isNum(cy.post_tropical_only) && cy.post_tropical_only > 0 ? (
                <p className="place-line place-muted">
                  {`${fmt.n(cy.post_tropical_only, 0)} ${t('place.history.posttropical')}`}
                </p>
              ) : null}
              <Decades rows={cy.by_decade ?? []} ctx={ctx} />
            </>
          ) : null}
        </>
      }
    >
      <BlockRow id="history.earthquakes" block={quakes} ctx={ctx}>
        {line(q.total, t('place.history.label.quakes'), () =>
          t('place.history.quakes', {
            n: fmt.n(q.total as number, 0),
            mag: isNum(q.min_magnitude) ? fmt.n(q.min_magnitude, 1) : '?',
            km: kmText,
            year: isNum(q.since_year) ? q.since_year : '?',
          }),
        )}
      </BlockRow>
      <BlockRow id="history.significant_earthquakes" block={significant} ctx={ctx}>
        {line(s.total, t('place.history.label.significant'), () =>
          t('place.history.significant', { n: fmt.n(s.total as number, 0), km: kmText }),
        )}
      </BlockRow>
      <BlockRow id="history.tsunamis" block={tsunamis} ctx={ctx}>
        {line(ts.sources?.kept, t('place.history.label.tsunamis'), () =>
          t('place.history.tsunamis', { n: fmt.n(ts.sources?.kept as number, 0), km: kmText }),
        )}
      </BlockRow>
      <BlockRow id="history.eruptions" block={eruptions} ctx={ctx}>
        {line(er.total, t('place.history.label.eruptions'), () =>
          t('place.history.eruptions', { n: fmt.n(er.total as number, 0), km: kmText }),
        )}
      </BlockRow>
      <BlockRow id="history.volcanoes" block={volcanoes} ctx={ctx}>
        {line(vo.total, t('place.history.label.volcanoes'), () =>
          t('place.history.volcanoes', { n: fmt.n(vo.total as number, 0), km: kmText }),
        )}
      </BlockRow>
      <BlockRow id="history.cyclones" block={cyclones} ctx={ctx}>
        {line(cy.tropical, t('place.history.label.cyclones'), () =>
          t('place.history.cyclones', {
            n: fmt.n(cy.tropical as number, 0),
            km: kmText,
            year: isNum(cy.catalogue_since_season) ? cy.catalogue_since_season : '?',
          }),
        )}
      </BlockRow>
    </Section>
  )
}

// ------------------------------------------------------------------ the panel

export interface PlaceReportProps {
  lat: number
  lon: number
  /** the name the reader searched or clicked, shown as the title */
  name: string
  /** scopes `history.*` and `alerts_now` only; 10..300, the server default 300 */
  radiusKm?: number
  /** replaces the network call (tests, another origin). Read through a ref:
   * an inline arrow from the parent does not restart the fetch. */
  fetcher?: PlaceFetcher
  /** a report already in hand, drawn at once if it is for these props */
  initial?: PlaceResponse
}

interface Held {
  body: PlaceResponse
  /** Date.now() when it arrived: ages run from server_time plus this delta */
  arrived: number
}

export function PlaceReport({ lat, lon, name, radiusKm, fetcher = fetchPlace, initial }: PlaceReportProps) {
  // Stable slices only (lesson 1): a function and a primitive.
  const t = useStore((s) => s.t)
  const lang = useStore((s) => s.lang)
  const fmt = useMemo(() => makeFormats(lang), [lang])
  const titleId = useId()

  const qLat = roundCoord(lat)
  const qLon = roundCoord(lon)
  const qRadius = radiusKm ?? 300
  const query = useMemo<PlaceQuery>(
    () => ({ lat: qLat, lon: qLon, radius_km: qRadius, lang }),
    [qLat, qLon, qRadius, lang],
  )

  const [held, setHeld] = useState<Held | null>(() =>
    initial && isPlaceResponse(initial) && sameQuery(initial.query, query, false)
      ? { body: initial, arrived: Date.now() }
      : null,
  )
  const [failure, setFailure] = useState<{ query: PlaceQuery; text: string } | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const fetcherRef = useRef(fetcher)
  useEffect(() => {
    fetcherRef.current = fetcher
  }, [fetcher])

  useEffect(() => {
    let alive = true
    let controller: AbortController | null = null
    let retryTimer: number | undefined
    let retries = 0
    let lastStart = 0

    const load = (retry: boolean) => {
      // A request still running is superseded, never stacked.
      controller?.abort()
      window.clearTimeout(retryTimer)
      if (!retry) retries = 0
      const current = new AbortController()
      controller = current
      lastStart = Date.now()
      let pending: Promise<PlaceResponse>
      try {
        pending = fetcherRef.current(query, current.signal)
      } catch (reason) {
        pending = Promise.reject(reason)
      }
      pending.then(
        (body) => {
          if (!alive || current.signal.aborted) return
          if (!isPlaceResponse(body)) {
            setFailure({ query, text: 'unexpected response from /api/place' })
            return
          }
          // A late answer for another place never paints over this one.
          if (!sameQuery(body.query, query, true)) return
          const arrived = Date.now()
          setHeld({ body, arrived })
          setFailure(null)
          setNow(arrived)
          if (!body.complete && retries < PLACE_PARTIAL_RETRIES) {
            retries += 1
            const wait = isNum(body.retry_after_s) && body.retry_after_s > 0 ? body.retry_after_s : 5
            retryTimer = window.setTimeout(() => load(true), wait * 1000)
          }
        },
        (reason: unknown) => {
          if (!alive || current.signal.aborted) return
          setFailure({ query, text: errorText(reason) })
        },
      )
    }

    load(false)
    const refresh = window.setInterval(() => {
      if (document.visibilityState === 'visible') load(false)
    }, PLACE_REFRESH_MS)
    // A tab hidden through a refresh catches up as soon as it is seen again.
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastStart >= PLACE_REFRESH_MS) {
        load(false)
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      alive = false
      controller?.abort()
      window.clearTimeout(retryTimer)
      window.clearInterval(refresh)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [query])

  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), PLACE_TICK_MS)
    return () => window.clearInterval(tick)
  }, [])

  // What is drawn: only a report for THIS place (the language may lag one
  // fetch behind: only error.message changes with it).
  const report = held && sameQuery(held.body.query, query, false) ? held.body : null
  const error = failure && sameQuery(failure.query, query, false) ? failure.text : null
  const serverNow = report ? Date.parse(report.server_time) + Math.max(0, now - (held?.arrived ?? now)) : now
  const clock = useMemo<Clock>(() => ({ serverNow }), [serverNow])
  const ctx = useMemo<Ctx | null>(
    () => (report ? { t, fmt, lang, clock, report } : null),
    [t, fmt, lang, clock, report],
  )

  const summary = useMemo(() => {
    if (!report || !ctx) return null
    let ok = 0
    let late = 0
    let unavailable = 0
    let na = 0
    for (const id of PLACE_BLOCK_IDS) {
      const block = report.blocks[id]
      if (block.status === 'not_applicable') na += 1
      else if (block.status === 'unavailable') unavailable += 1
      else if (isLate(block, ctx.clock)) late += 1
      else ok += 1
    }
    return { ok, late, unavailable, na }
  }, [report, ctx])

  return (
    <section className="place-report" aria-labelledby={titleId} data-testid="place-report">
      <header className="place-head">
        <p className="place-overline">{t('place.title')}</p>
        <h2 className="place-name" id={titleId}>
          {name}
        </h2>
        {summary ? (
          <p className="place-summary">
            {t('place.summary', { ok: summary.ok, unavailable: summary.unavailable, na: summary.na })}
            {summary.late > 0 ? `, ${t('place.summary.late', { n: summary.late })}` : null}
          </p>
        ) : null}
        {report && !report.complete ? (
          <p className="state state-att place-partial" role="status">
            <span className="state-glyph" aria-hidden="true">
              ◷
            </span>
            <span>{t('place.partial')}</span>
          </p>
        ) : null}
        {error ? (
          <p className="state state-att place-error">
            <span className="state-glyph" aria-hidden="true">
              ✕
            </span>
            <span>
              {report
                ? rich(t('place.error.kept', { reason: error }), {
                    time: <time dateTime={report.server_time}>{fmt.dateTime(report.server_time)}</time>,
                  })
                : t('place.error', { reason: error })}
            </span>
          </p>
        ) : null}
        {!report && !error ? (
          <p className="place-loading" role="status">
            {t('place.loading')}
          </p>
        ) : null}
      </header>

      {ctx ? (
        <div className="place-sections">
          <AlertsSection ctx={ctx} />
          <NowSection ctx={ctx} />
          <TodaySection ctx={ctx} />
          <WindSection ctx={ctx} />
          <ForecastSection ctx={ctx} />
          <SeaSection ctx={ctx} />
          <AirSection ctx={ctx} />
          <RiskSection ctx={ctx} />
          <HistorySection ctx={ctx} />
        </div>
      ) : null}

      <p className="place-disclaimer">{t('place.disclaimer')}</p>
    </section>
  )
}
