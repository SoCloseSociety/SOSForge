import { useEffect, useMemo, useState } from 'react'
import { ALL_KINDS, WINDOWS, filterEvents, useStore } from './store'
import { connectLive } from './live'
import { syncDeepLink } from './deeplink'
import { Feed } from './components/Feed'
import { MapView } from './components/MapView'
import { LivePanel } from './components/LivePanel'
import { SearchBar } from './components/SearchBar'
import { ArrivalAlert } from './components/ArrivalAlert'
import { LANGS } from './i18n'
import {
  KIND_GLYPH,
  SEVERITY_META,
  SOURCE_LABEL,
  formatAge,
  formatClock,
  kindLabel,
} from './format'
import type { SosEvent } from './types'

/** Server clock, refreshed every second. Everything displayed as "N s ago"
 * is computed from it, not from the browser clock: a misconfigured machine
 * would otherwise show wrong, even negative, ages. */
function useServerNow(): number {
  const skew = useStore((s) => s.clockSkew)
  const [tick, setTick] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  return tick + skew
}

function Kpis({ now }: { now: number }) {
  const stats = useStore((s) => s.stats)
  const sources = useStore((s) => s.sources)
  const events = useStore((s) => s.events)
  const t = useStore((s) => s.t)

  const lastQuake = useMemo(() => events.find((e) => e.kind === 'earthquake'), [events])
  const online = sources.filter((s) => s.connected).length

  return (
    <div className="kpis">
      <div className="kpi">
        <div className="label">{t('kpi.quakes')}</div>
        <div className="value">{stats?.earthquakes_last_hour ?? '--'}</div>
        <div className="sub">
          {lastQuake
            ? t('kpi.quakes.last', {
                age: formatAge(t, (now - Date.parse(lastQuake.time)) / 1000),
              })
            : '--'}
        </div>
      </div>
      <div className="kpi">
        <div className="label">{t('kpi.maxmag')}</div>
        <div className="value">{stats?.max_magnitude_last_hour?.toFixed(1) ?? '--'}</div>
        <div className="sub">{t('kpi.maxmag.sub')}</div>
      </div>
      <div className="kpi">
        <div className="label">{t('kpi.tsunami')}</div>
        <div
          className="value"
          style={{ color: stats?.tsunami_active ? SEVERITY_META.extreme.color : undefined }}
        >
          {stats?.tsunami_active ?? '--'}
        </div>
        <div className="sub">{t('kpi.tsunami.sub')}</div>
      </div>
      <div className="kpi">
        <div className="label">{t('kpi.tracked')}</div>
        <div className="value">{stats?.total_buffered ?? '--'}</div>
        <div className="sub">{t('kpi.tracked.sub', { n: stats?.last_hour ?? 0 })}</div>
      </div>
      <div className="kpi">
        <div className="label">{t('kpi.sources')}</div>
        <div className="value">
          {online}/{sources.length || '--'}
        </div>
        <div className="sub">{t('kpi.sources.sub')}</div>
      </div>
    </div>
  )
}

function Banner({ events, now }: { events: SosEvent[]; now: number }) {
  const t = useStore((s) => s.t)
  const critical = useMemo(() => {
    const cutoff = now - 6 * 3600 * 1000
    return events.filter(
      (e) => Date.parse(e.time) > cutoff && (e.tsunami || e.severity === 'extreme'),
    )
  }, [events, now])

  if (critical.length === 0) return null
  const top = critical[0]
  return (
    <div className="banner" role="alert">
      <span className="icon" aria-hidden="true">
        {top.tsunami ? '🌊' : SEVERITY_META.extreme.glyph}
      </span>
      <span>
        {top.tsunami ? t('banner.tsunami') : t('banner.major')} : {top.place || top.title}
        <br />
        <small>
          {formatAge(t, (now - Date.parse(top.time)) / 1000)}
          {critical.length > 1 ? ` · ${t('banner.others', { n: critical.length - 1 })}` : ''}
        </small>
      </span>
    </div>
  )
}

function Filters({ events }: { events: SosEvent[] }) {
  const filters = useStore((s) => s.filters)
  const toggleKind = useStore((s) => s.toggleKind)
  const setMinMagnitude = useStore((s) => s.setMinMagnitude)
  const setWindow = useStore((s) => s.setWindow)
  const t = useStore((s) => s.t)

  const counts = useMemo(() => {
    const map = new Map<string, number>()
    for (const event of events) map.set(event.kind, (map.get(event.kind) ?? 0) + 1)
    return map
  }, [events])

  return (
    <div className="filters">
      {/* Search comes first: it's the shortcut to "what's happening OVER
          THERE", the question people ask when they open a tracker after
          hearing about something. */}
      <SearchBar />

      {/* Then the window: it's what separates live from historical, and
          therefore the question the user is asking when they arrive. */}
      <div className="segmented" role="group" aria-label={t('filters.window')}>
        {WINDOWS.map((minutes) => (
          <button
            type="button"
            key={minutes}
            aria-pressed={filters.windowMinutes === minutes}
            onClick={() => setWindow(minutes)}
          >
            {t(`window.${minutes}`)}
          </button>
        ))}
      </div>

      {/* ALL covered types are shown, even at zero. Showing only the types
          currently active would suggest the product doesn't cover tsunamis
          on days when there aren't any -- on an emergency tracker,
          "0 tsunami alerts" is information, not an absence of information. */}
      <div className="chips">
        {ALL_KINDS.map((kind) => {
          const n = counts.get(kind) ?? 0
          return (
            <button
              type="button"
              key={kind}
              className={`chip${n === 0 ? ' empty' : ''}`}
              aria-pressed={filters.kinds.has(kind)}
              onClick={() => toggleKind(kind)}
              title={n === 0 ? t('filters.none', { kind: kindLabel(t, kind) }) : undefined}
            >
              <span aria-hidden="true">{KIND_GLYPH[kind]}</span>
              {kindLabel(t, kind)}
              <span className="count">{n}</span>
            </button>
          )
        })}
      </div>

      <label className="slider">
        {t('filters.minmag')}
        <input
          type="range"
          min={0}
          max={7}
          step={0.5}
          value={filters.minMagnitude}
          onChange={(e) => setMinMagnitude(Number(e.target.value))}
        />
        <output>{filters.minMagnitude.toFixed(1)}</output>
      </label>
    </div>
  )
}

function LangPicker() {
  const lang = useStore((s) => s.lang)
  const setLang = useStore((s) => s.setLang)
  return (
    <label className="lang">
      <span aria-hidden="true">{LANGS.find((l) => l.code === lang)?.flag}</span>
      <select
        value={lang}
        onChange={(e) => setLang(e.target.value as typeof lang)}
        aria-label="Langue / Language"
      >
        {LANGS.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function Footer() {
  const sources = useStore((s) => s.sources)
  const clients = useStore((s) => s.clients)
  const t = useStore((s) => s.t)
  return (
    <footer className="footer">
      {sources.map((source) => (
        <span className="source" key={source.name} title={source.last_error ?? 'OK'}>
          <span className={`dot ${source.connected ? 'up' : 'down'}`} />
          {SOURCE_LABEL[source.name] ?? source.name}
          <span className="count">{source.ingested ?? source.events_seen}</span>
        </span>
      ))}
      <span className="spacer" />
      <span>{t('footer.clients', { n: clients })}</span>
      <span>{t('footer.basemap')}</span>
    </footer>
  )
}

export default function App() {
  const now = useServerNow()
  const connected = useStore((s) => s.connected)
  const soundOn = useStore((s) => s.soundOn)
  const toggleSound = useStore((s) => s.toggleSound)
  const events = useStore((s) => s.events)
  const filters = useStore((s) => s.filters)
  const selected = useStore((s) => s.selected)
  const lang = useStore((s) => s.lang)
  const t = useStore((s) => s.t)

  // The window slides with time, so the list must be recomputed... but not
  // 60 times a minute: we recompute it in 10 s buckets, which is plenty for
  // a 15-minute cutoff and avoids re-pushing the map's GeoJSON source every
  // second.
  const bucket = Math.floor(now / 10_000)
  const visible = useMemo(
    () => filterEvents(events, filters, bucket * 10_000),
    [events, filters, bucket],
  )

  const selectedEvent = useMemo(
    () => events.find((e) => e.id === selected) ?? null,
    [events, selected],
  )

  useEffect(() => connectLive(), [])
  useEffect(() => syncDeepLink(), [])
  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <strong>SOSForge</strong>
          <span>{t('app.tagline')}</span>
        </div>
        <span className="spacer" />
        <LangPicker />
        <span className="clock" aria-label={`${formatClock(new Date(now))} UTC`}>
          {formatClock(new Date(now))} UTC
        </span>
        <button
          type="button"
          className="toggle"
          aria-pressed={soundOn}
          onClick={toggleSound}
          title={t('app.sound.title')}
        >
          <span aria-hidden="true">{soundOn ? '🔔' : '🔕'}</span>
          {soundOn ? t('app.sound.on') : t('app.sound.off')}
        </button>
        {/* aria-live: connection status is THE thing a screen reader user
            needs to learn without having to go looking for it */}
        <span className={`live ${connected ? 'on' : 'off'}`} role="status" aria-live="polite">
          <span className="dot" aria-hidden="true" />
          {connected ? t('app.live') : t('app.reconnecting')}
        </span>
      </header>

      {/* Above everything: it is the only element with a deadline. */}
      <ArrivalAlert events={events} now={now} />
      <Kpis now={now} />
      <Banner events={events} now={now} />

      <div className="body">
        <section className="panel">
          <Filters events={events} />
          <Feed
            events={visible}
            now={now}
            emptyKey={
              filters.query.trim()
                ? 'filters.empty.search'
                : filters.windowMinutes > 0 && events.length > 0
                  ? 'filters.empty.window'
                  : 'filters.empty'
            }
          />
        </section>
        <div className="map-column">
          <MapView events={visible} now={now} />
          {selectedEvent ? <LivePanel event={selectedEvent} now={now} /> : null}
        </div>
      </div>

      <Footer />
    </div>
  )
}
