import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { filterEvents, useStore } from "./store";
import { emptyReason } from "./emptyReason";
import { criticalEvents } from "./critical";
import { byRelevance } from "./relevance";
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
import { ClimateButton, OfflineMapButton } from "./components/FooterTools";
import {
  NEAR_RADIUS_KM,
  NEAR_ZOOM,
  WORLD_VIEW,
  effectiveScope,
  nearestEvents,
  splitByScope,
  worldZoom,
} from "./scope";
import { distanceKm } from "./arrival";
import { useNearMe } from "./useNearMe";
import { useCountries } from "./useCountries";
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

  const received = useStore((s) => s.received);

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
      {/* The two secondary counters stand down on a phone: the sources are
          in the footer, permanently, and the buffer size answers no question
          a reader has in an emergency. Five cards in a row were cut at the
          screen edge (owner: nothing is cut). Unknown stays "--", never 0. */}
      <div className="kpi kpi-secondary">
        <div className="label">{t("kpi.tracked")}</div>
        <div className="value">{stats?.total_buffered ?? "--"}</div>
        <div className="sub">
          {received && stats ? t("kpi.tracked.sub", { n: stats.last_hour }) : "--"}
        </div>
      </div>
      <div className="kpi kpi-secondary">
        <div className="label">{t("kpi.sources")}</div>
        <div className="value">
          {received ? `${online}/${sources.length}` : "--"}
        </div>
        <div className="sub">{t("kpi.sources.sub")}</div>
      </div>
    </div>
  );
}

function Banner({ events, now }: { events: SosEvent[]; now: number }) {
  const t = useStore((s) => s.t);
  // the banner names what is happening now, not the next warning's onset
  // A tsunami is named first, whatever is newer: on a phone held sideways
  // the tsunami KPI is hidden, and an advisory under a newer storm was
  // named nowhere above the feed (review of PR #5, #19).
  const critical = useMemo(() => {
    const ranked = byRelevance(criticalEvents(events, now), now);
    return [...ranked.filter((e) => e.tsunami), ...ranked.filter((e) => !e.tsunami)];
  }, [events, now]);

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
            <>
              <span className="banner-others">
                {` · ${t("banner.others", { n: critical.length - 1 })}`}
              </span>
              {/* the same count, short, where the long line does not fit */}
              <span className="banner-others-short">
                {` · ${t("banner.others.short", { n: critical.length - 1 })}`}
              </span>
            </>
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
        <ClimateButton />
        <OfflineMapButton />
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
  const place = useStore((s) => s.place);
  const isPhone = useIsPhone();

  // Near me by default; the world when there is no position (see the hook).
  useNearMe();
  const showing = effectiveScope(scope, here, place);
  // The point the feed is around: the reader (Near me), a chosen place, or
  // none (World).
  const center = showing === "near" ? here : showing === "place" ? place : null;
  const centerName = showing === "place" && place ? place.name : t("scope.near");
  // Its countries, found in the page (the position never leaves it): the
  // alerts that carry no position but name one of them belong in this feed.
  const countries = useCountries(center);

  // The window slides with time, so the list must be recomputed... but not
  // 60 times a minute: we recompute it in 10 s buckets, which is plenty for
  // a 15-minute cutoff and avoids re-pushing the map's GeoJSON source every
  // second.
  const bucket = Math.floor(now / 10_000);
  // What is happening now first: an upcoming warning is not "the latest"
  // just because its onset is in the future (see relevance.ts).
  const filtered = useMemo(
    () => byRelevance(filterEvents(events, filters, bucket * 10_000), bucket * 10_000),
    [events, filters, bucket],
  );
  // Near me keeps what is within reach of the reader, and what names the
  // reader's country without a position; what is severe beyond it is
  // COUNTED, permanently, so it never silently disappears -- and what is
  // severe with no position at all is counted apart, never "elsewhere".
  const { visible, unmatched } = useMemo(() => {
    if (!center) return { visible: filtered, unmatched: 0 };
    const split = splitByScope(filtered, center, countries);
    return { visible: split.near, unmatched: split.unmatched };
  }, [filtered, center, countries]);
  // The counts beside the scope are a claim about the WORLD: never narrowed
  // by the reader's kinds, search or severity floor, and an alert still in
  // force counts whatever its age -- the banner above names it. Taken from
  // the filtered list, the bar read "Nothing severe elsewhere" under a
  // "TSUNAMI ALERT" banner (review of PR #5, #3 to #5).
  const windowMinutes = filters.windowMinutes;
  const { severeElsewhere, unlocated } = useMemo(() => {
    if (!center) return { severeElsewhere: 0, unlocated: 0 };
    const cutoff = windowMinutes > 0 ? bucket * 10_000 - windowMinutes * 60_000 : null;
    const inForce =
      cutoff === null ? events : events.filter((e) => e.ongoing || Date.parse(e.time) >= cutoff);
    return splitByScope(inForce, center, countries);
  }, [events, windowMinutes, bucket, center, countries]);

  // Nothing within reach of the place: said honestly, AND the nearest recent
  // events with their distance instead of an empty list.
  const nearest = useMemo(
    () =>
      center && visible.length === 0 && filtered.length > 0 && remaining === 0
        ? nearestEvents(filtered, center)
        : [],
    [center, visible.length, filtered, remaining],
  );

  // The camera follows the scope: a regional view on the reader, the world
  // otherwise. Moved again only when the reader moved far (50 km): a reader
  // who panned the map must not have it yanked back every kilometre.
  const framed = useRef<{ scope: "near" | "world" | "place"; lat: number; lon: number } | null>(
    null,
  );
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
        // A position that ARRIVES (the first fix, or a move) does not pull
        // the camera off an open event: a shared #e/<id> link to a quake in
        // Chile stays on Chile (review of PR #5, #6). Pressing Near me does.
        const automatic = !previous || previous.scope === "near";
        if (!(automatic && useStore.getState().selected)) {
          setFocus({ lat: here.lat, lon: here.lon, zoom: NEAR_ZOOM, name: "near" });
        }
      }
    } else if (showing === "place" && place) {
      if (
        !previous ||
        previous.scope !== "place" ||
        previous.lat !== place.lat ||
        previous.lon !== place.lon
      ) {
        framed.current = { scope: "place", lat: place.lat, lon: place.lon };
        // The search flew there itself, at the zoom of the place it found (a
        // country is wider than an island): not overridden. A place restored
        // on reload, or picked again in the scope bar, has no flight yet.
        const focus = useStore.getState().focus;
        if (!focus || focus.lat !== place.lat || focus.lon !== place.lon) {
          setFocus({ lat: place.lat, lon: place.lon, zoom: NEAR_ZOOM, name: place.name });
        }
      }
    } else if (showing === "world" && previous && previous.scope !== "world") {
      framed.current = { scope: "world", lat: WORLD_VIEW.lat, lon: WORLD_VIEW.lon };
      const width = document.querySelector(".map-wrap")?.clientWidth ?? window.innerWidth;
      setFocus({ ...WORLD_VIEW, zoom: worldZoom(width), name: "world" });
    }
  }, [showing, here, place]);

  // "Nothing near" is never said bare while alerts without a position could
  // not be matched to the reader's country yet (the table still loading).
  const unmatchedNote = unmatched > 0 ? ` ${t("scope.unmatched", { n: unmatched })}` : "";
  const emptyKey =
    center && remaining === 0 && visible.length === 0 && filtered.length > 0
      ? unmatched > 0
        ? "scope.empty.unmatched"
        : "scope.empty"
      : emptyReason({ events, visible, filters, remaining, received });
  const nearestIntro =
    nearest.length === 0
      ? undefined
      : (showing === "place"
          ? t("scope.nearest", { place: centerName, km: NEAR_RADIUS_KM.toLocaleString() })
          : t("scope.nearest.you", { km: NEAR_RADIUS_KM.toLocaleString() })) + unmatchedNote;
  const nearestDistances = useMemo(
    () => new Map(nearest.map((n) => [n.event.id, n.km])),
    [nearest],
  );

  // The watch alarm rings from HERE, whatever panel is mounted: on a phone
  // the watch panel lives in the collapsed filter sheet (audit 2026-10, #3).
  const alarms = useWatchAlarms(events, now);

  const selectedEvent = useMemo(
    () => events.find((e) => e.id === selected) ?? null,
    [events, selected],
  );
  // The map draws what the feed lists, PLUS the open event wherever it is:
  // an alarm or an arrival alert opens events beyond the scope, and the
  // map flew to an empty spot with no marker and no popup (review, #7).
  const feedEvents = useMemo(
    () => (nearest.length > 0 ? nearest.map((n) => n.event) : visible),
    [nearest, visible],
  );
  const mapEvents = useMemo(
    () =>
      selectedEvent && !feedEvents.some((e) => e.id === selectedEvent.id)
        ? [...feedEvents, selectedEvent]
        : feedEvents,
    [feedEvents, selectedEvent],
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
          <ScopeBar severeElsewhere={severeElsewhere} unlocated={unlocated} />
          {/* PLACE REPORT SLOT. <PlaceReport lat lon name /> (weather, sea,
              wind, air, risk and history for a place) mounts HERE, under the
              scope bar, ONLY for a place the reader searched or clicked:
              {showing === "place" && place && <PlaceReport lat={place.lat}
              lon={place.lon} name={place.name} />}. NEVER with `center`:
              in Near me `center` is the DEVICE position, and /api/place
              sends its point to about fifteen third parties, which breaks
              "your location never leaves your device". Nothing is rendered
              until it is wired. */}
          <Feed
            events={feedEvents}
            now={now}
            remaining={remaining}
            emptyKey={emptyKey}
            emptyParams={{ unmatched }}
            distances={nearest.length > 0 ? nearestDistances : undefined}
            intro={nearestIntro}
          />
        </section>
        <div className="map-column">
          <Suspense fallback={<div className="map-wrap map-fallback" />}>
            <MapView events={mapEvents} now={now} />
          </Suspense>
          {selectedEvent ? <LivePanel event={selectedEvent} now={now} /> : null}
        </div>
      </div>

      <Footer now={now} />
    </div>
  );
}
