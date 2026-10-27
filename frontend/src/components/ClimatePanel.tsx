/** Climate indicators: the state of the planet every hazard now happens
 * against, read from `GET /api/climate`.
 *
 * The rule this panel keeps is the product's own: it never lies about
 * freshness, and it never invents a number.
 *
 * - every value, baseline and date on screen is the one the API returned.
 *   There is no default, no placeholder, no "last known" figure baked in
 *   here: before the first response the panel says it is loading, and a
 *   failed request says so with the real error text, never with zeros;
 * - the date next to a value is `as_of`, the date the DATASET gives its last
 *   value. When SOSForge last read the file (`state.last_ok`) is shown apart,
 *   labelled as our read, behind "Show more";
 * - `stale` and `status: "preliminary"` are said in words, never by colour;
 * - an indicator the server marks `available: false` keeps whatever value it
 *   still carries, greyed, with its own date AND the server's last error.
 *
 * Self-contained: the fetch and the first payload can be injected through
 * props, so a test needs no network and the app can mount it anywhere.
 */
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import type { T } from '../format'
import { useStore } from '../store'
import './ClimatePanel.css'

export type ClimateTrend = 'up' | 'down' | 'flat'

export interface ClimateBaseline {
  /** Stable id of the reference ("day_1991_2020", "day_year_earlier",
   * "week_1991_2020"): what the panel translates. `label` is the server's
   * English fallback for a kind it does not know yet. */
  kind?: string
  label: string
  value: number | null
  as_of?: string
  years?: number
}

export interface ClimatePrevious {
  as_of: string
  value: number | string | null
  anomaly?: number | null
}

export interface ClimateSource {
  name: string
  url: string
  data_url: string | null
  licence: string | null
}

export interface ClimateState {
  last_ok: string | null
  last_attempt: string | null
  last_error: string | null
  errors: number
  poll_seconds: number | null
}

export interface ClimateIndicator {
  id: string
  label: string
  available: boolean
  value: number | string | null
  unit: string | null
  /** The date the DATASET gives its last value (YYYY-MM-DD), never our fetch time. */
  as_of: string | null
  status: 'preliminary' | 'final' | null
  anomaly: number | null
  baseline: ClimateBaseline | null
  previous: ClimatePrevious | null
  trend: ClimateTrend | null
  /** The value (and its anomaly) is a minimum: part of the area had no data. */
  lower_bound?: boolean
  stale: boolean
  max_age_days: number | null
  detail: Record<string, unknown>
  source: ClimateSource
  state: ClimateState
}

export interface ClimateResponse {
  server_time: string
  indicators: ClimateIndicator[]
}

export type ClimateFetcher = (signal: AbortSignal) => Promise<ClimateResponse>

/** The datasets change every few hours at most: ten minutes keeps the panel
 * well inside the fastest of them without hammering the API. */
export const CLIMATE_REFRESH_MS = 10 * 60 * 1000

/** The minimum a payload must have before anything is drawn from it. A
 * proxy error page or a half-deployed API must surface as an error, not as
 * an empty panel that looks like "no indicators". */
export function isClimateResponse(body: unknown): body is ClimateResponse {
  if (typeof body !== 'object' || body === null) return false
  const { indicators, server_time: serverTime } = body as {
    indicators?: unknown
    server_time?: unknown
  }
  return (
    typeof serverTime === 'string' &&
    Array.isArray(indicators) &&
    indicators.every(
      (item) =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as { id?: unknown }).id === 'string',
    )
  )
}

/** The default fetch: same origin, the Vite dev server proxies `/api`. */
export async function fetchClimate(signal: AbortSignal): Promise<ClimateResponse> {
  const response = await fetch('/api/climate', {
    signal,
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`)
  }
  const body: unknown = await response.json()
  if (!isClimateResponse(body)) throw new Error('unexpected response from /api/climate')
  return body
}

/** The real error text, as close to what failed as the browser gives it. */
function errorText(reason: unknown): string {
  if (reason instanceof Error) {
    const named = reason.name && reason.name !== 'Error' ? `${reason.name}: ` : ''
    return `${named}${reason.message}`.trim() || reason.name || String(reason)
  }
  return String(reason)
}

/** Every number and date in the active language. Built once per language. */
interface Formats {
  value: (n: number) => string
  signed: (n: number) => string
  count: (n: number) => string
  days: (n: number) => string
  hours: (n: number) => string
  years: (n: number) => string
  date: (iso: string) => string
  dateTime: (iso: string) => string
}

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/

function makeFormats(lang: string): Formats {
  const value = new Intl.NumberFormat(lang, { maximumFractionDigits: 3 })
  const signed = new Intl.NumberFormat(lang, {
    maximumFractionDigits: 2,
    signDisplay: 'exceptZero',
  })
  const count = new Intl.NumberFormat(lang, { maximumFractionDigits: 0 })
  const unit = (name: 'day' | 'hour' | 'year') =>
    new Intl.NumberFormat(lang, {
      style: 'unit',
      unit: name,
      unitDisplay: 'long',
      maximumFractionDigits: 1,
    })
  const days = unit('day')
  const hours = unit('hour')
  const years = unit('year')
  // A dataset date is a calendar day, not an instant: read and written in
  // UTC, or a reader west of Greenwich would see the day before.
  const date = new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeZone: 'UTC' })
  const dateTime = new Intl.DateTimeFormat(lang, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    timeZoneName: 'short',
  })
  return {
    value: (n) => value.format(n),
    signed: (n) => signed.format(n),
    count: (n) => count.format(n),
    days: (n) => days.format(n),
    hours: (n) => hours.format(n),
    years: (n) => years.format(n),
    // Anything that is not the expected shape is shown as the server sent
    // it: a raw string is honest, a guessed date is not.
    date: (iso) => {
      if (!DAY_RE.test(iso)) return iso
      const parsed = new Date(`${iso}T00:00:00Z`)
      return Number.isNaN(parsed.getTime()) ? iso : date.format(parsed)
    },
    dateTime: (iso) => {
      const parsed = new Date(iso)
      return Number.isNaN(parsed.getTime()) ? iso : dateTime.format(parsed)
    },
  }
}

/** A translated sentence with React nodes in its `{slots}`, so each language
 * keeps its own word order around a `<time>` or a link. */
function rich(template: string, slots: Record<string, ReactNode>): ReactNode[] {
  return template.split(/\{(\w+)\}/).map((part, index) =>
    index % 2 === 1 ? <span key={index}>{slots[part] ?? `{${part}}`}</span> : part,
  )
}

/** A translation that exists, or the server's own text. `t` returns the key
 * itself when no dictionary has it. */
function orServer(t: T, key: string, server: string): string {
  const value = t(key)
  return value === key ? server : value
}

const UNIT_KEYS: Record<string, string> = { 'million km²': 'climate.unit.mkm2' }

function unitLabel(t: T, unit: string | null | undefined): string {
  if (!unit) return ''
  const key = UNIT_KEYS[unit]
  return key ? orServer(t, key, unit) : unit
}

/** A number and its unit, kept together on one line when there is room. */
function withUnit(text: string, unit: string): string {
  return unit ? `${text} ${unit}` : text
}

function DayTime({ iso, fmt }: { iso: string; fmt: Formats }) {
  return DAY_RE.test(iso) ? <time dateTime={iso}>{fmt.date(iso)}</time> : <>{iso}</>
}

const TREND_GLYPH: Record<ClimateTrend, string> = { up: '↑', down: '↓', flat: '→' }

/** The direction against the previous reading the dataset itself gives. The
 * arrow is for the eye, the words are for everyone else. */
function Trend({
  trend,
  since,
  t,
  fmt,
}: {
  trend: ClimateTrend
  since: string | null
  t: T
  fmt: Formats
}) {
  const word = t(`climate.trend.${trend}`)
  const label = since ? t('climate.trend.since', { trend: word, date: fmt.date(since) }) : word
  return (
    <span className="climate-trend">
      <span className="climate-trend-glyph" aria-hidden="true">
        {TREND_GLYPH[trend]}
      </span>
      <span className="climate-sr-only">{label}</span>
    </span>
  )
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="climate-detail-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

/** The dataset-specific extras (`detail`): the ones whose meaning is known are
 * said in words, any other is shown as the server sent it rather than
 * dropped. */
function ExtraRows({
  detail,
  t,
  fmt,
}: {
  detail: Record<string, unknown>
  t: T
  fmt: Formats
}) {
  return (
    <>
      {Object.entries(detail).map(([key, value]) => {
        if (value === null || value === undefined || value === '') return null
        if (key === 'synopsis' && typeof value === 'string') {
          return (
            <DetailRow key={key} label={t('climate.detail.synopsis')}>
              <span className="climate-synopsis">{value}</span>
            </DetailRow>
          )
        }
        if (key === 'next_issue' && typeof value === 'string') {
          return (
            <DetailRow key={key} label={t('climate.detail.next')}>
              <DayTime iso={value} fmt={fmt} />
            </DetailRow>
          )
        }
        if (key === 'missing_million_km2' && typeof value === 'number') {
          const amount = withUnit(fmt.value(value), unitLabel(t, 'million km²'))
          return (
            <DetailRow key={key} label={t('climate.detail.missing')}>
              {value > 0 ? `${amount}: ${t('climate.detail.missing.lower')}` : amount}
            </DetailRow>
          )
        }
        if (key === 'week') {
          return (
            <DetailRow key={key} label={t('climate.detail.week')}>
              {value === 'centred on as_of' ? t('climate.detail.week.centred') : String(value)}
            </DetailRow>
          )
        }
        const text =
          typeof value === 'number'
            ? fmt.value(value)
            : typeof value === 'string' || typeof value === 'boolean'
              ? String(value)
              : JSON.stringify(value)
        return (
          <DetailRow key={key} label={key}>
            <span className="climate-token">{text}</span>
          </DetailRow>
        )
      })}
    </>
  )
}

function IndicatorCard({
  indicator,
  t,
  fmt,
}: {
  indicator: ClimateIndicator
  t: T
  fmt: Formats
}) {
  const [open, setOpen] = useState(false)
  const nameId = useId()
  const detailId = useId()

  const {
    available,
    value,
    as_of: asOf,
    anomaly,
    baseline,
    previous,
    trend,
    stale,
    status,
    lower_bound: lowerBound = false,
  } = indicator
  const source = indicator.source ?? null
  const state = indicator.state ?? null
  const detail = indicator.detail ?? {}
  const error = state?.last_error ?? null
  const unit = unitLabel(t, indicator.unit)

  const shown =
    typeof value === 'number' && Number.isFinite(value)
      ? fmt.value(value)
      : typeof value === 'string' && value !== ''
        ? value
        : null
  const isText = typeof value === 'string'
  const hasAnomaly = typeof anomaly === 'number' && Number.isFinite(anomaly)
  // The server compares anomalies when both readings carry one, values
  // otherwise (app/climate.py `_trend`): the arrow sits next to what it
  // actually compares.
  const trendOnAnomaly = hasAnomaly && previous !== null && previous !== undefined && 'anomaly' in previous
  const trendNode = trend ? (
    <Trend trend={trend} since={previous?.as_of ?? null} t={t} fmt={fmt} />
  ) : null

  const maxAge =
    typeof indicator.max_age_days === 'number' && Number.isFinite(indicator.max_age_days)
      ? indicator.max_age_days
      : null
  const label = orServer(t, `climate.ind.${indicator.id}`, indicator.label || indicator.id)

  return (
    <li className={`climate-item${available ? '' : ' is-unavailable'}`}>
      <h3 className="climate-name" id={nameId}>
        {label}
      </h3>

      {shown !== null ? (
        <p className={`climate-reading${available ? '' : ' is-kept'}`}>
          <span className={`climate-value${isText ? ' is-text' : ''}`}>{shown}</span>
          {unit ? <span className="climate-unit">{unit}</span> : null}
          {trendOnAnomaly ? null : trendNode}
        </p>
      ) : null}

      {hasAnomaly ? (
        <p className={`climate-anomaly${available ? '' : ' is-kept'}`}>
          <span>{t('climate.anomaly', { value: withUnit(fmt.signed(anomaly), unit) })}</span>
          {trendOnAnomaly ? trendNode : null}
        </p>
      ) : null}

      {shown !== null && asOf ? (
        <p className="climate-asof">{rich(t('climate.asof'), { date: <DayTime iso={asOf} fmt={fmt} /> })}</p>
      ) : null}

      {status === 'preliminary' || stale || lowerBound ? (
        <ul className="climate-flags">
          {lowerBound ? (
            // Visible on the card, not behind "Show more": the figure above
            // is a minimum, and the reader must know before trusting it.
            <li className="climate-flag is-lower-bound">
              <span className="climate-flag-glyph" aria-hidden="true">
                ≥
              </span>
              <span>
                {typeof detail.missing_million_km2 === 'number'
                  ? t('climate.lowerbound', {
                      amount: withUnit(
                        fmt.value(detail.missing_million_km2),
                        unitLabel(t, 'million km²'),
                      ),
                    })
                  : t('climate.lowerbound.short')}
              </span>
            </li>
          ) : null}
          {status === 'preliminary' ? (
            <li className="climate-flag is-preliminary">
              <span className="climate-flag-glyph" aria-hidden="true">
                ~
              </span>
              <span>{t('climate.preliminary')}</span>
            </li>
          ) : null}
          {stale ? (
            <li className="climate-flag is-stale">
              <span className="climate-flag-glyph" aria-hidden="true">
                ◷
              </span>
              <span>
                {maxAge !== null
                  ? t('climate.stale', { n: fmt.days(maxAge) })
                  : t('climate.stale.short')}
              </span>
            </li>
          ) : null}
        </ul>
      ) : null}

      {available ? null : (
        <p className="climate-problem">
          <span className="climate-problem-glyph" aria-hidden="true">
            ✕
          </span>
          <span className="climate-problem-text">
            {shown !== null ? t('climate.kept') : t('climate.unavailable')}
            {error ? null : shown !== null ? null : ` ${t('climate.noreading')}`}
          </span>
          {error ? <span className="climate-error-text">{error}</span> : null}
        </p>
      )}

      {source?.name ? (
        <p className="climate-source">
          <span>{t('climate.source')}</span>{' '}
          {source.url ? (
            <a href={source.url} target="_blank" rel="noopener noreferrer">
              {source.name}
            </a>
          ) : (
            <span>{source.name}</span>
          )}
        </p>
      ) : null}

      <button
        type="button"
        className="climate-more"
        aria-expanded={open}
        aria-controls={detailId}
        aria-describedby={nameId}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? t('more.hide') : t('more.show')}
      </button>

      <dl className="climate-detail" id={detailId} hidden={!open}>
        {baseline ? (
          <DetailRow label={t('climate.detail.baseline')}>
            {baseline.kind
              ? orServer(t, `climate.baseline.${baseline.kind}`, baseline.label)
              : baseline.label}
            {typeof baseline.value === 'number'
              ? `: ${withUnit(fmt.value(baseline.value), unit)}`
              : null}
            {baseline.as_of ? (
              <>
                {' ('}
                <DayTime iso={baseline.as_of} fmt={fmt} />
                {')'}
              </>
            ) : null}
            {typeof baseline.years === 'number' ? ` · ${fmt.years(baseline.years)}` : null}
          </DetailRow>
        ) : null}

        {previous?.as_of ? (
          <DetailRow label={t('climate.detail.previous')}>
            {rich(t('climate.detail.on'), {
              value:
                typeof previous.value === 'number'
                  ? withUnit(fmt.value(previous.value), unit)
                  : String(previous.value ?? ''),
              date: <DayTime iso={previous.as_of} fmt={fmt} />,
            })}
            {typeof previous.anomaly === 'number'
              ? ` · ${t('climate.anomaly', { value: withUnit(fmt.signed(previous.anomaly), unit) })}`
              : null}
          </DetailRow>
        ) : null}

        <ExtraRows detail={detail} t={t} fmt={fmt} />

        {maxAge !== null ? (
          <DetailRow label={t('climate.detail.maxage')}>{fmt.days(maxAge)}</DetailRow>
        ) : null}

        {source?.licence ? (
          <DetailRow label={t('climate.detail.licence')}>{source.licence}</DetailRow>
        ) : null}

        {source?.data_url ? (
          <DetailRow label={t('climate.detail.data')}>
            <a
              className="climate-token"
              href={source.data_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {source.data_url}
            </a>
          </DetailRow>
        ) : null}

        {state ? (
          <>
            <DetailRow label={t('climate.detail.lastok')}>
              {state.last_ok ? (
                <time dateTime={state.last_ok}>{fmt.dateTime(state.last_ok)}</time>
              ) : (
                t('climate.detail.never')
              )}
            </DetailRow>
            <DetailRow label={t('climate.detail.lastattempt')}>
              {state.last_attempt ? (
                <time dateTime={state.last_attempt}>{fmt.dateTime(state.last_attempt)}</time>
              ) : (
                t('climate.detail.never')
              )}
            </DetailRow>
            {typeof state.poll_seconds === 'number' && state.poll_seconds > 0 ? (
              <DetailRow label={t('climate.detail.poll')}>
                {fmt.hours(state.poll_seconds / 3600)}
              </DetailRow>
            ) : null}
            {typeof state.errors === 'number' ? (
              <DetailRow label={t('climate.detail.errors')}>{fmt.count(state.errors)}</DetailRow>
            ) : null}
          </>
        ) : null}
      </dl>
    </li>
  )
}

export interface ClimatePanelProps {
  /** Replaces the network call (tests, another origin). Read through a ref:
   * an inline arrow from the parent does not restart the polling. */
  fetchClimate?: ClimateFetcher
  /** A payload already in hand: drawn at once instead of the loading state.
   * The panel still fetches on mount, then every `refreshMs`. */
  initialData?: ClimateResponse
  refreshMs?: number
}

export function ClimatePanel({
  fetchClimate: fetcher = fetchClimate,
  initialData,
  refreshMs = CLIMATE_REFRESH_MS,
}: ClimatePanelProps) {
  // Stable slices only (lesson 1): a function and a primitive.
  const t = useStore((s) => s.t)
  const lang = useStore((s) => s.lang)
  const fmt = useMemo(() => makeFormats(lang), [lang])
  const titleId = useId()

  const [data, setData] = useState<ClimateResponse | null>(
    initialData && isClimateResponse(initialData) ? initialData : null,
  )
  const [error, setError] = useState<string | null>(null)

  const fetcherRef = useRef(fetcher)
  useEffect(() => {
    fetcherRef.current = fetcher
  }, [fetcher])

  useEffect(() => {
    let alive = true
    let controller: AbortController | null = null

    const load = () => {
      // A request still running ten minutes later is superseded, not stacked.
      controller?.abort()
      const current = new AbortController()
      controller = current
      let pending: Promise<ClimateResponse>
      try {
        pending = fetcherRef.current(current.signal)
      } catch (reason) {
        pending = Promise.reject(reason)
      }
      pending.then(
        (body) => {
          if (!alive || current.signal.aborted) return
          if (!isClimateResponse(body)) {
            setError('unexpected response from /api/climate')
            return
          }
          setData(body)
          setError(null)
        },
        (reason: unknown) => {
          if (!alive || current.signal.aborted) return
          setError(errorText(reason))
        },
      )
    }

    load()
    const timer = window.setInterval(load, refreshMs)
    return () => {
      alive = false
      window.clearInterval(timer)
      controller?.abort()
    }
  }, [refreshMs])

  const indicators = data?.indicators ?? null

  return (
    <section className="climate-panel" aria-labelledby={titleId}>
      <h2 className="climate-title" id={titleId}>
        {t('climate.title')}
      </h2>
      <p className="climate-intro">{t('climate.intro')}</p>

      {error ? (
        <p className="climate-problem climate-fetch-error">
          <span className="climate-problem-glyph" aria-hidden="true">
            ✕
          </span>
          <span className="climate-problem-text">
            {data
              ? rich(t('climate.error.kept'), {
                  // the SERVER's clock, from the last good payload
                  time: <time dateTime={data.server_time}>{fmt.dateTime(data.server_time)}</time>,
                })
              : t('climate.error')}
          </span>
          <span className="climate-error-text">{error}</span>
        </p>
      ) : null}

      {data === null && error === null ? (
        <p className="climate-loading" role="status">
          {t('climate.loading')}
        </p>
      ) : null}

      {indicators && indicators.length === 0 ? (
        <p className="climate-empty">{t('climate.empty')}</p>
      ) : null}

      {indicators && indicators.length > 0 ? (
        <ul className="climate-list">
          {indicators.map((indicator) => (
            <IndicatorCard key={indicator.id} indicator={indicator} t={t} fmt={fmt} />
          ))}
        </ul>
      ) : null}
    </section>
  )
}
