import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { filterEvents, useStore } from "./store";
import { emptyReason } from "./emptyReason";
import { criticalEvents } from "./critical";
import { connectLive } from "./live";
import { syncDeepLink } from "./deeplink";
import { Feed } from "./components/Feed";
import { useIsPhone } from "./useMediaQuery";
import { useWatchAlarms } from "./useWatchAlarms";

/** MapLibre is over a megabyte, and the feed is readable without it. Loading it
 * lazily takes the entry bundle from 1277 kB to a fraction of that, which is
 * what decides whether this page opens at all on a saturated network -- the
 * exact condition it exists for. */
const MapView = lazy(() =>
  import("./components/MapView").then((m) => ({ default: m.MapView })),
);
import { LivePanel } from "./components/LivePanel";
import { SearchBar } from "./components/SearchBar";
import { ArrivalAlert } from "./components/ArrivalAlert";
import { WatchCompact } from "./components/WatchPanel";
import { ActiveFilters, FilterSheet, WindowSwitch } from "./components/FilterSheet";
import { ScopeBar } from "./components/ScopeBar";
import { SourcesBar } from "./components/SourcesBar";
import { GetAppBanner, GetAppLink } from "./components/GetApp";
import { NEAR_ZOOM, WORLD_VIEW, effectiveScope, splitByScope, worldZoom } from "./scope";
import { distanceKm } from "./arrival";
import { useNearMe } from "./useNearMe";
import { LANGS } from "./i18n";
import { applyTheme, currentTheme, type Theme } from "./theme";
import { SEVERITY_META, formatAge, formatClock, kindLabel } from "./format";
import type { SosEvent } from "./types";

/** Server clock, refreshed every second. Everything displayed as "N s ago"
 * is computed from it, not from the browser clock: a misconfigured machine
 * would otherwise show wrong, even negative, ages. */
function useServerNow(): number {
  const skew = useStore((s) => s.clockSkew);
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return tick + skew;
}

function Kpis({ now }: { now: number }) {
  const stats = useStore((s) => s.stats);
  const sources = useStore((s) => s.sources);
  const events = useStore((s) => s.events);
  const t = useStore((s) => s.t);
  const loading = useStore((s) => s.snapshotRemaining > 0);

  const lastQuake = useMemo(
    () => events.find((e) => e.kind === "earthquake"),
    [events],
  );
  const online = sources.filter((s) => s.connected).length;

  return (
    <div className="kpis">
      <div className="kpi">
        <div className="label">{t("kpi.quakes")}</div>
        <div className="value">{stats?.earthquakes_last_hour ?? "--"}</div>
        <div className="sub">
          {/* the newest quake may still be in a snapshot chunk on its way */}
          {loading
            ? t("kpi.loading")
            : lastQuake
            ? t("kpi.quakes.last", {
                age: formatAge(t, (now - Date.parse(lastQuake.time)) / 1000),
              })
            : "--"}
        </div>
      </div>
      <div className="kpi">
        <div className="label">{t("kpi.maxmag")}</div>
        <div className="value">
          {stats?.max_magnitude_last_hour?.toFixed(1) ?? "--"}
        </div>
        <div className="sub">{t("kpi.maxmag.sub")}</div>
      </div>
      <div className="kpi">
        <div className="label">{t("kpi.tsunami")}</div>
        {/* An active tsunami bulletin is said by the extreme glyph AND the
            colour, never the colour alone (it used to be a red number). */}
        <div
          className={`value${stats?.tsunami_active ? " sev-extreme" : ""}`}
        >
          {stats?.tsunami_active ? (
            <span className="glyph" aria-hidden="true">
              {SEVERITY_META.extreme.glyph}{" "}
            </span>
          ) : null}
          {stats?.tsunami_active ?? "--"}
        </div>
        <div className="sub">{t("kpi.tsunami.sub")}</div>
      </div>
      <div className="kpi">
        <div className="label">{t("kpi.tracked")}</div>
        <div className="value">{stats?.total_buffered ?? "--"}</div>
        <div className="sub">
          {t("kpi.tracked.sub", { n: stats?.last_hour ?? 0 })}
        </div>
      </div>
      <div className="kpi">
        <div className="label">{t("kpi.sources")}</div>
        <div className="value">
          {online}/{sources.length || "--"}
        </div>
        <div className="sub">{t("kpi.sources.sub")}</div>
      </div>
    </div>
  );
}

function Banner({ events, now }: { events: SosEvent[]; now: number }) {
  const t = useStore((s) => s.t);
  const critical = useMemo(() => criticalEvents(events, now), [events, now]);

  if (critical.length === 0) return null;
  const top = critical[0];
  return (
    <div className="banner" role="alert">
      <span className="icon" aria-hidden="true">
        {top.tsunami ? "🌊" : SEVERITY_META.extreme.glyph}
      </span>
      <span className="banner-text">
        <strong>{top.tsunami ? t("banner.tsunami") : t("banner.major")}</strong>
        {": "}
        {/* The place alone is not enough: the NHC names a storm by its
            given name, so the banner read "MAJOR ALERT: Polo" and nothing
            told the reader that Polo was a hurricane. The hazard type comes
            first, the place after. */}
        {top.tsunami ? "" : `${kindLabel(t, top.kind)} -- `}
        {top.place || top.title}
        <br />
        <small>
          <span className="banner-age">
            {formatAge(t, (now - Date.parse(top.time)) / 1000)}
          </span>
          {critical.length > 1 ? (
            <span className="banner-others">
              {` · ${t("banner.others", { n: critical.length - 1 })}`}
            </span>
          ) : null}
        </small>
      </span>
    </div>
  );
}

function LangPicker() {
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const t = useStore((s) => s.t);
  return (
    <label className="lang">
      <span aria-hidden="true">{LANGS.find((l) => l.code === lang)?.flag}</span>
      <select
        value={lang}
        onChange={(e) => setLang(e.target.value as typeof lang)}
        aria-label={t("lang.picker")}
      >
        {LANGS.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Lunar (dark, the default) or Soft (light, for daylight). A pressed toggle
 * with a constant label, not a button whose word flips: a screen reader hears
 * "Soft, toggle button, pressed", which says both the choice and the state.
 * The map stays dark in both themes, as Sentinel House keeps its sky. */
function ThemeToggle() {
  const t = useStore((s) => s.t);
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const soft = theme === "soft";
  return (
    <button
      type="button"
      className="btn btn-secondary btn-compact"
      aria-pressed={soft}
      title={t("theme.title")}
      onClick={() => {
        const next: Theme = soft ? "lunar" : "soft";
        applyTheme(next);
        setTheme(next);
      }}
    >
      <span aria-hidden="true">◐</span>
      <span className="label">{t("theme.soft")}</span>
    </button>
  );
}

function Credit() {
  return (
    <span className="credit">
      Developed by{" "}
      <a href="https://soclose.co" target="_blank" rel="noopener">
        SoClose Pte. Ltd.
      </a>
      , Singapore
    </span>
  );
}

/** The footer: every source's state in one row (`SourcesBar`), then one
 * quiet line of credits. */
function Footer({ now }: { now: number }) {
  const t = useStore((s) => s.t);
  return (
    <footer className="footer">
      <SourcesBar now={now} />
      <p className="footer-credit">
        <span>{t("footer.basemap")}</span>
        <GetAppLink />
        <Credit />
      </p>
    </footer>
  );
}

export default function App() {
  const now = useServerNow();
  const connected = useStore((s) => s.connected);
  const soundOn = useStore((s) => s.soundOn);
  const toggleSound = useStore((s) => s.toggleSound);
  const events = useStore((s) => s.events);
  const filters = useStore((s) => s.filters);
  const selected = useStore((s) => s.selected);
  const lang = useStore((s) => s.lang);
  const t = useStore((s) => s.t);
  const remaining = useStore((s) => s.snapshotRemaining);
  const received = useStore((s) => s.received);
  const scope = useStore((s) => s.scope);
  const here = useStore((s) => s.here);
  const isPhone = useIsPhone();

  // Near me by default; the world when there is no position (see the hook).
  useNearMe();
  const showing = effectiveScope(scope, here);

  // The window slides with time, so the list must be recomputed... but not
  // 60 times a minute: we recompute it in 10 s buckets, which is plenty for
  // a 15-minute cutoff and avoids re-pushing the map's GeoJSON source every
  // second.
  const bucket = Math.floor(now / 10_000);
  const filtered = useMemo(
    () => filterEvents(events, filters, bucket * 10_000),
    [events, filters, bucket],
  );
  // Near me keeps what is within reach of the reader; what is severe beyond
  // it is COUNTED, permanently, so it never silently disappears.
  const { visible, severeElsewhere } = useMemo(() => {
    if (showing !== "near" || !here) return { visible: filtered, severeElsewhere: 0 };
    const split = splitByScope(filtered, here);
    return { visible: split.near, severeElsewhere: split.severeElsewhere };
  }, [filtered, showing, here]);

  // The camera follows the scope: a regional view on the reader, the world
  // otherwise. Moved again only when the reader moved far (50 km): a reader
  // who panned the map must not have it yanked back every kilometre.
  const framed = useRef<{ scope: "near" | "world"; lat: number; lon: number } | null>(null);
  useEffect(() => {
    const { setFocus } = useStore.getState();
    const previous = framed.current;
    if (showing === "near" && here) {
      if (
        !previous ||
        previous.scope !== "near" ||
        distanceKm(previous.lat, previous.lon, here.lat, here.lon) > 50
      ) {
        framed.current = { scope: "near", lat: here.lat, lon: here.lon };
        setFocus({ lat: here.lat, lon: here.lon, zoom: NEAR_ZOOM, name: "near" });
      }
    } else if (showing === "world" && previous?.scope === "near") {
      framed.current = { scope: "world", lat: WORLD_VIEW.lat, lon: WORLD_VIEW.lon };
      const width = document.querySelector(".map-wrap")?.clientWidth ?? window.innerWidth;
      setFocus({ ...WORLD_VIEW, zoom: worldZoom(width), name: "world" });
    }
  }, [showing, here]);

  const emptyKey =
    showing === "near" && remaining === 0 && visible.length === 0 && filtered.length > 0
      ? "scope.empty"
      : emptyReason({ events, visible, filters, remaining, received });

  // The watch alarm rings from HERE, whatever panel is mounted: on a phone
  // the watch panel lives in the collapsed filter sheet (audit 2026-10, #3).
  const alarms = useWatchAlarms(events, now);

  const selectedEvent = useMemo(
    () => events.find((e) => e.id === selected) ?? null,
    [events, selected],
  );

  useEffect(() => connectLive(), []);
  useEffect(() => syncDeepLink(), []);
  useEffect(() => {
    document.documentElement.lang = lang;
    // The tab title, the bookmark and what a screen reader announces on page
    // load all come from here. Leaving it English meant a Japanese reader's
    // tab said "SOSForge -- live earthquake, tsunami and disaster tracker"
    // forever, whatever the interface said.
    document.title = t("app.title");
  }, [lang, t]);

  return (
    // `data-layout` is what tools/responsive.py reads to check that the
    // components and the stylesheet agree on what a phone is.
    <div className="app" data-layout={isPhone ? "phone" : "wide"}>
      <header className="header">
        <div className="brand">
          {/* The only heading present at all times. Without it a screen-reader
              user pressing H landed on the map legend, or on nothing. */}
          <h1>SOSForge</h1>
          <span className="tagline">{t("app.tagline")}</span>
        </div>
        <span className="spacer" />
        {/* aria-live: connection status is THE thing a screen reader user
            needs to learn without having to go looking for it. First in the
            bar after the name: it is what every other number depends on. */}
        <span
          className={`live ${connected ? "on" : "off"}`}
          role="status"
          aria-live="polite"
        >
          <span className="dot" aria-hidden="true" />
          {connected ? t("app.live") : t("app.reconnecting")}
        </span>
        <span
          className="clock"
          aria-label={`${formatClock(new Date(now))} UTC`}
        >
          {formatClock(new Date(now))} UTC
        </span>
        <span className="header-tools">
          <button
            type="button"
            className="btn btn-secondary btn-compact toggle"
            aria-pressed={soundOn}
            onClick={toggleSound}
            title={t("app.sound.title")}
          >
            <span aria-hidden="true">{soundOn ? "🔔" : "🔕"}</span>
            {/* wrapped so the phone layout can drop the word and keep the
                icon: a bare text node cannot be targeted by CSS */}
            <span className="label">
              {soundOn ? t("app.sound.on") : t("app.sound.off")}
            </span>
          </button>
          <ThemeToggle />
          <LangPicker />
        </span>
      </header>

      {/* Above everything: it is the only element with a deadline. */}
      <ArrivalAlert events={events} now={now} />
      <Kpis now={now} />
      <Banner events={events} now={now} />

      <div className="body">
        <section className="panel">
          <GetAppBanner />
          <div className="filters">
            {/* Search first: it is the shortcut to "what is happening OVER
                THERE", the question people ask after hearing about
                something. The kind filters fold into one button beside it. */}
            <div className="tools-row">
              <SearchBar />
              <FilterSheet events={events} resultCount={visible.length} phone={isPhone} />
            </div>
            <ActiveFilters phone={isPhone} />
            {/* The watch answers the same question for one place: one line
                that says where it stands and opens the whole card. A full
                card here left a 841x673 foldable and a 1024x768 tablet with
                no event above the fold (tools/responsive.py). */}
            <WatchCompact alarms={alarms} />
            {/* The window separates live from history; on a phone it lives
                in the filter sheet. */}
            {isPhone ? null : <WindowSwitch />}
          </div>
          <ScopeBar severeElsewhere={severeElsewhere} />
          <Feed events={visible} now={now} remaining={remaining} emptyKey={emptyKey} />
        </section>
        <div className="map-column">
          <Suspense fallback={<div className="map-wrap map-fallback" />}>
            <MapView events={visible} now={now} />
          </Suspense>
          {selectedEvent ? <LivePanel event={selectedEvent} now={now} /> : null}
        </div>
      </div>

      <Footer now={now} />
    </div>
  );
}
