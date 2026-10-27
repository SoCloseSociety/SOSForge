import { create } from "zustand";
import type {
  ServerMessage,
  SosEvent,
  SourceHealth,
  Stats,
  Kind,
  Severity,
} from "./types";
import { detectLang, persistLang, translate, type Lang } from "./i18n";
import { closeNotification } from "./notify";

const MAX_EVENTS = 1000;

/** How long a server-clock sample is kept. In TIME, not in messages: a
 * slow snapshot delivers ~18 chunks and then ~20 queued ticks, and a
 * 30-message window dropped the one good sample (the first chunk) while the
 * queue was still draining -- the clock was still 8 s behind (measured). */
const SKEW_WINDOW_MS = 60_000;

/** The server clock offset, from a message's server_time and its arrival.
 *
 * A message can wait in the socket: on a slow link the snapshot chunks are
 * sent at once and received over twenty seconds, and the ticks emitted
 * meanwhile queue behind them. Each of those, read on its own, said the
 * server clock was up to 20 s late -- the header showed a time 20 s in the
 * past and every "N s ago" was 20 s too young (measured in Chrome, Slow 3G).
 * A transit delay can only make the server look BEHIND, never ahead, so the
 * least delayed recent message is the truest: the offset is the max of the
 * samples of the last minute. A new snapshot (a new connection) starts a new
 * window. The price: if the browser clock jumps forward, the old offset holds
 * for up to a minute. */
function nextSkew(
  samples: [number, number][],
  serverTime: string,
  arrival: number,
  fresh = false,
): { clockSkew: number; skewSamples: [number, number][] } {
  const sample = new Date(serverTime).getTime() - arrival;
  const kept = (fresh ? [] : samples).filter(([at]) => arrival - at <= SKEW_WINDOW_MS);
  const next: [number, number][] = Number.isFinite(sample)
    ? [...kept, [arrival, sample]]
    : kept;
  return {
    clockSkew: next.length ? Math.max(...next.map(([, value]) => value)) : 0,
    skewSamples: next,
  };
}

/** The buffer cap, applied to what is OVER only. The list is sorted by event
 * time, so a plain `slice` dropped the OLDEST first -- which is exactly where
 * a volcano alert open for a month or a tsunami advisory issued this morning
 * sits. They are still in force; the server keeps them in its snapshot, and
 * the client must not quietly cut them on the next quake. Ongoing events are
 * kept whatever their age, and the cap applies to the rest. */
function capEvents(events: SosEvent[]): SosEvent[] {
  if (events.length <= MAX_EVENTS) return events;
  let kept = 0;
  return events.filter((event) => {
    if (event.ongoing) return true;
    kept += 1;
    return kept <= MAX_EVENTS;
  });
}

export interface Filters {
  kinds: Set<Kind>;
  minMagnitude: number;
  /** lowest severity shown; `info` = everything */
  minSeverity: Severity;
  sources: Set<string>;
  /** time window in minutes; 0 = all available history */
  windowMinutes: number;
  /** free-text search: place, title, country */
  query: string;
}

/** The offered windows, in minutes (0 = all available history).
 *
 * Only the values live here: the labels come from i18n (`t('window.15')`,
 * ...). Labels stored alongside would be hardcoded French that would bypass
 * translation the day someone displays them. "Live" (15 min) is the
 * product's real promise: what just happened; the others are for regaining
 * context without leaving the page.
 */
export const WINDOWS: number[] = [15, 60, 360, 1440, 0];

/** The place the user wants to be warned about.
 *
 * `kinds` is what the reader chose to be alarmed for, in step 2 of the watch
 * funnel. ABSENT means "not chosen yet": every kind alarms (the safe default)
 * and the panel asks the question. A watch armed from the search bar arrives
 * without it, and so does one saved before the funnel existed. */
export interface Watch {
  lat: number;
  lon: number;
  name: string;
  kinds?: Kind[];
}

/** What the feed and the map show: around the reader, or the whole world. */
export type Scope = "near" | "world" | "place";

/** A place the reader CHOSE (a search result): not their position. Its
 * coordinates come from the geocoder, so keeping them is no privacy matter,
 * and it survives a reload like the watch does. */
export interface Place {
  lat: number;
  lon: number;
  name: string;
}

/** Where the location request stands, said in place by the scope bar.
 * `idle`: never asked this visit; `asking`: the browser prompt is up;
 * `locating`: permission given, waiting for a fix; `ok`: we have one; the
 * rest are the honest reasons the world view is showing instead. */
export type LocateState =
  | "idle"
  | "asking"
  | "locating"
  | "ok"
  | "denied"
  | "unavailable"
  | "timeout"
  | "insecure";

/** The reader's position, COARSE: two decimals (about 1 km). Enough to say
 * what is near, not enough to say which house. */
export interface Here {
  lat: number;
  lon: number;
}

interface State {
  events: SosEvent[];
  connected: boolean;
  reconnectIn: number;
  stats: Stats | null;
  sources: SourceHealth[];
  /** connected clients, from the 1 Hz tick; null until the first tick says */
  clients: number | null;
  /** has a snapshot arrived on this page yet? Until it has, every count is
   * UNKNOWN, not zero: "0 tsunami alerts" before the feed has spoken would be
   * a statement about data we do not have (owner: nothing may be fake). */
  received: boolean;
  /** browser clock -> server clock offset, in ms */
  clockSkew: number;
  /** [arrival, server_time - arrival] of the last minute; `clockSkew` is
   * the max of their values */
  skewSamples: [number, number][];
  lastMessageAt: number;
  selected: string | null;
  soundOn: boolean;
  lang: Lang;
  filters: Filters;
  fresh: Set<string>;
  /** area the map should fly to (search result) */
  focus: { lat: number; lon: number; zoom: number; name: string } | null;
  /** the place the user wants to be warned about. Wave arrival is computed
   * against this point, so it is opt-in and never guessed silently. */
  watch: Watch | null;
  /** ids of the events that already raised a watch alarm for this place.
   * In the store, and persisted, because the component that used to keep it
   * (the watch panel) mounts and unmounts with the phone filter sheet: every
   * opening forgot it and the whole last hour alarmed again. */
  alarmed: Map<string, number | null>;
  /** snapshot events still on their way (`snapshot.more`, then each chunk's
   * `remaining`). While above zero, nothing on screen may claim the picture
   * is complete: no "no event", no final count. */
  snapshotRemaining: number;
  /** what the reader wants to see; `near` falls back to the world view by
   * itself when there is no position (see `effectiveScope`) */
  scope: Scope;
  here: Here | null;
  /** the chosen place the "place" scope centres on */
  place: Place | null;
  locate: LocateState;
  /** bumped when the reader ASKS for Near me: an explicit ask may show the
   * browser prompt again, where an automatic one never shows it twice */
  locateRequest: number;

  ingest: (message: ServerMessage) => void;
  setConnected: (value: boolean) => void;
  select: (id: string | null) => void;
  toggleSound: () => void;
  toggleKind: (kind: Kind) => void;
  setMinMagnitude: (value: number) => void;
  setWindow: (minutes: number) => void;
  setQuery: (query: string) => void;
  setFocus: (focus: State["focus"]) => void;
  setMinSeverity: (severity: Severity) => void;
  /** every kind back on */
  resetKinds: () => void;
  setScope: (scope: Scope) => void;
  /** a place picked from the search: the feed scopes to it, like Near me;
   * null goes back to Near me */
  setPlace: (place: Place | null) => void;
  setHere: (here: Here | null) => void;
  setLocate: (state: LocateState) => void;
  /** the reader pressed "Near me": switch, and ask for the position now */
  requestNearMe: () => void;
  setWatch: (watch: State["watch"]) => void;
  /** remember what rang, at which rank (the highest wins) */
  markAlarmed: (rung: { id: string; rank: number }[]) => void;
  /** No longer concerns the watched place: forgotten, so it rings again if
   * it comes back in reach. */
  forgetAlarmed: (ids: string[]) => void;
  setLang: (lang: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const ALL_KINDS: Kind[] = [
  "earthquake",
  "tsunami",
  "volcano",
  "cyclone",
  "flood",
  "wildfire",
  "storm",
  "heat",
  "drought",
  "landslide",
  "avalanche",
  "space_weather",
  "other",
];

const SEVERITY_RANK: Record<Severity, number> = {
  info: 0,
  minor: 1,
  moderate: 2,
  severe: 3,
  extreme: 4,
};

/** A synthesized beep: no audio file to bundle, and the pitch rises with
 * severity. The browser requires a user gesture before playing sound, hence
 * the explicit enable button in the header. */
let audioContext: AudioContext | null = null;

export function playAlert(severity: Severity) {
  try {
    audioContext ||= new AudioContext();
    if (audioContext.state === "suspended") void audioContext.resume();
    const now = audioContext.currentTime;
    const gain = audioContext.createGain();
    gain.connect(audioContext.destination);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(
      severity === "extreme" ? 0.25 : 0.12,
      now + 0.02,
    );
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.9);

    const beeps = severity === "extreme" ? 3 : 1;
    for (let i = 0; i < beeps; i += 1) {
      const osc = audioContext.createOscillator();
      osc.type = "sine";
      osc.frequency.setValueAtTime(
        severity === "extreme" ? 880 : 587,
        now + i * 0.22,
      );
      osc.connect(gain);
      osc.start(now + i * 0.22);
      osc.stop(now + i * 0.22 + 0.18);
    }
  } catch {
    /* no sound available: not a reason to break the feed */
  }
}

const FILTERS_KEY = "sosforge.filters";
const WATCH_KEY = "sosforge.watch";

/** The watched place survives a reload: an alert you have to re-arm on every
 * visit is an alert you will not have when it matters. */
function loadWatch(): Watch | null {
  try {
    const raw = localStorage.getItem(WATCH_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (typeof saved?.lat !== "number" || typeof saved?.lon !== "number")
      return null;
    const watch: Watch = {
      lat: saved.lat,
      lon: saved.lon,
      name: String(saved.name ?? ""),
    };
    // Only kinds this build knows: a hand-edited or future value must not
    // smuggle an unknown kind into the alarm filter.
    if (Array.isArray(saved.kinds)) {
      watch.kinds = saved.kinds.filter((k: unknown): k is Kind =>
        ALL_KINDS.includes(k as Kind),
      );
    }
    return watch;
  } catch {
    return null;
  }
}

function saveWatch(watch: Watch | null): void {
  try {
    if (watch) localStorage.setItem(WATCH_KEY, JSON.stringify(watch));
    else localStorage.removeItem(WATCH_KEY);
  } catch {
    /* private mode: it just will not persist */
  }
}

const ALARMED_KEY = "sosforge.alarmed";
/** Enough for weeks of alarms at one place; the oldest ids go first. */
const ALARMED_MAX = 500;

/** What already rang, and at which RANK, survives a reload too.
 *
 * Keyed on the id AND the highest rank already rung (the macOS agent's
 * rule): an escalation -- M6.4 revised to M7.3, an EEW from 5- to 6- --
 * rings once more, a revision at the same rank stays silent. Keyed on the id
 * alone, lane N's browser drill showed the escalation never rang.
 *
 * An ongoing alert stays alarm-worthy for as long as it runs (days, for a
 * heat wave): without the persistence, every reload of the tab rang it again.
 * The old format (ids only) loads with an UNKNOWN rank (null): the first
 * look at the event records its rank without ringing. */
function loadAlarmed(): Map<string, number | null> {
  try {
    const saved = JSON.parse(localStorage.getItem(ALARMED_KEY) ?? "[]");
    const out = new Map<string, number | null>();
    if (!Array.isArray(saved)) return out;
    for (const entry of saved) {
      if (typeof entry === "string") out.set(entry, null);
      // [id, null] is an old-format id saved again before its rank was seen:
      // still remembered, or it rang again at the next reload (review of PR #5)
      else if (
        Array.isArray(entry) &&
        typeof entry[0] === "string" &&
        (typeof entry[1] === "number" || entry[1] === null)
      )
        out.set(entry[0], entry[1]);
    }
    return out;
  } catch {
    return new Map();
  }
}

function saveAlarmed(rung: Map<string, number | null>): void {
  try {
    localStorage.setItem(ALARMED_KEY, JSON.stringify([...rung].slice(-ALARMED_MAX)));
  } catch {
    /* private mode: alarms may repeat after a reload, nothing worse */
  }
}

/** Filters survive a reload: coming back to the page and finding
 * "Live + earthquakes only" already set avoids redoing three clicks on
 * every visit. Text search, though, is NOT persisted -- finding an invisible
 * filter that hides the whole feed would be confusing. */
function loadFilters(): Pick<
  Filters,
  "kinds" | "minMagnitude" | "windowMinutes" | "minSeverity"
> | null {
  try {
    const raw = localStorage.getItem(FILTERS_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    const kinds: Kind[] = Array.isArray(saved.kinds) ? saved.kinds : ALL_KINDS;
    return {
      kinds: new Set(kinds.filter((k) => ALL_KINDS.includes(k))),
      minMagnitude: Number(saved.minMagnitude) || 0,
      windowMinutes: WINDOWS.includes(Number(saved.windowMinutes))
        ? Number(saved.windowMinutes)
        : 1440,
      minSeverity:
        saved.minSeverity in SEVERITY_RANK ? (saved.minSeverity as Severity) : "info",
    };
  } catch {
    return null;
  }
}

function saveFilters(filters: Filters): void {
  try {
    localStorage.setItem(
      FILTERS_KEY,
      JSON.stringify({
        kinds: [...filters.kinds],
        minMagnitude: filters.minMagnitude,
        windowMinutes: filters.windowMinutes,
        minSeverity: filters.minSeverity,
      }),
    );
  } catch {
    /* private mode: filters won't survive, that's all */
  }
}

const SCOPE_KEY = "sosforge.scope";

/** Two decimals: about a kilometre, the only precision the page keeps. The
 * position itself is never stored: only the mode and whether the prompt was
 * already shown are. */
export function coarse(value: number): number {
  return Math.round(value * 100) / 100;
}

function loadScope(): { scope: Scope; asked: boolean; place: Place | null } {
  try {
    const saved = JSON.parse(localStorage.getItem(SCOPE_KEY) ?? "null");
    const p = saved?.place;
    const place: Place | null =
      p && typeof p.lat === "number" && typeof p.lon === "number" &&
      Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180
        ? { lat: p.lat, lon: p.lon, name: String(p.name ?? "") }
        : null;
    const scope: Scope =
      saved?.scope === "world" ? "world" : saved?.scope === "place" && place ? "place" : "near";
    return { scope, asked: saved?.asked === true, place };
  } catch {
    return { scope: "near", asked: false, place: null };
  }
}

/** Saves the mode, the chosen place and whether the browser prompt has
 * already been shown once (so a dismissed prompt is not shown on every
 * visit: no nagging). Never the reader's own position. */
export function saveScope(patch: { scope?: Scope; asked?: boolean; place?: Place | null }): void {
  try {
    const next = { ...loadScope(), ...patch };
    localStorage.setItem(
      SCOPE_KEY,
      JSON.stringify({ scope: next.scope, asked: next.asked, place: next.place }),
    );
  } catch {
    /* private mode: the mode is not remembered, the default applies */
  }
}

/** Was the browser's location prompt already shown on an earlier visit? */
export function promptAlreadyShown(): boolean {
  return loadScope().asked;
}

const SAVED_SCOPE = loadScope();
// The position is NEVER restored from a previous visit: the reader may have
// moved, and a view centred on where they were would be a guess presented
// as their location (owner: nothing may be fake). Near me waits for a real
// fix from the browser, and shows the world until then.

export const useStore = create<State>((set, get) => ({
  events: [],
  connected: false,
  reconnectIn: 0,
  stats: null,
  sources: [],
  clients: null,
  received: false,
  clockSkew: 0,
  skewSamples: [],
  lastMessageAt: 0,
  selected: null,
  soundOn: false,
  lang: detectLang(),
  filters: {
    kinds: new Set(ALL_KINDS),
    minMagnitude: 0,
    sources: new Set(),
    // 24 h by default: enough for context, short enough that the map shows
    // current events rather than a catalog
    windowMinutes: 1440,
    query: "",
    minSeverity: "info",
    ...(loadFilters() ?? {}),
  },
  fresh: new Set(),
  focus: null,
  watch: loadWatch(),
  alarmed: loadAlarmed(),
  snapshotRemaining: 0,
  scope: SAVED_SCOPE.scope,
  here: null,
  place: SAVED_SCOPE.place,
  locate: "idle",
  locateRequest: 0,

  setConnected: (value) => set({ connected: value }),
  select: (id) => set({ selected: id }),
  toggleSound: () => {
    const next = !get().soundOn;
    if (next) playAlert("info");
    set({ soundOn: next });
  },
  toggleKind: (kind) =>
    set((state) => {
      const kinds = new Set(state.filters.kinds);
      if (kinds.has(kind)) kinds.delete(kind);
      else kinds.add(kind);
      const next = { ...state.filters, kinds };
      saveFilters(next);
      return { filters: next };
    }),
  setMinMagnitude: (value) =>
    set((state) => {
      const next = { ...state.filters, minMagnitude: value };
      saveFilters(next);
      return { filters: next };
    }),
  setWindow: (minutes) =>
    set((state) => {
      const next = { ...state.filters, windowMinutes: minutes };
      saveFilters(next);
      return { filters: next };
    }),
  setQuery: (query) =>
    set((state) => ({ filters: { ...state.filters, query } })),
  setFocus: (focus) => set({ focus }),
  setMinSeverity: (severity) =>
    set((state) => {
      const next = { ...state.filters, minSeverity: severity };
      saveFilters(next);
      return { filters: next };
    }),
  resetKinds: () =>
    set((state) => {
      const next = { ...state.filters, kinds: new Set(ALL_KINDS) };
      saveFilters(next);
      return { filters: next };
    }),
  setPlace: (place) => {
    const scope: Scope = place ? "place" : "near";
    saveScope({ scope, place });
    // a place is not the reader: the fix stops being followed (see setScope)
    set(place ? { place, scope, here: null, locate: "idle" } : { place, scope });
  },
  setScope: (scope) => {
    saveScope({ scope });
    // Leaving Near me stops following the reader (useNearMe): the last fix
    // no longer says where they are. Kept, "Use my location" armed the watch
    // on Paris for a reader since gone to Lyon, and Near me came back on the
    // old fix (review of PR #5, #11).
    set(scope === "near" ? { scope } : { scope, here: null, locate: "idle" });
  },
  setHere: (here) => {
    const next = here ? { lat: coarse(here.lat), lon: coarse(here.lon) } : null;
    const current = get().here;
    // the same kilometre again: no new object, no re-render of the feed
    if (current && next && current.lat === next.lat && current.lon === next.lon) return;
    set({ here: next });
  },
  setLocate: (locate) => set({ locate }),
  requestNearMe: () => {
    saveScope({ scope: "near" });
    set((state) => ({ scope: "near", locateRequest: state.locateRequest + 1 }));
  },
  setWatch: (watch) => {
    saveWatch(watch);
    // A NEW place starts with a clean memory: an event already alarmed for
    // Osaka has never been announced for Sendai. Changing what is watched at
    // the same place keeps it.
    const previous = get().watch;
    const moved =
      !watch ||
      !previous ||
      previous.lat !== watch.lat ||
      previous.lon !== watch.lon;
    if (moved) {
      // what rang for the old place is about the old place: off the tray
      for (const id of get().alarmed.keys()) void closeNotification(id);
      const alarmed = new Map<string, number | null>();
      saveAlarmed(alarmed);
      set({ watch, alarmed });
    } else {
      set({ watch });
    }
  },
  markAlarmed: (rung) => {
    if (rung.length === 0) return;
    const alarmed = new Map(get().alarmed);
    for (const { id, rank } of rung) {
      const before = alarmed.get(id) ?? -1;
      // re-inserted at the end, so the cap drops the oldest
      alarmed.delete(id);
      alarmed.set(id, Math.max(before, rank));
    }
    saveAlarmed(alarmed);
    set({ alarmed });
  },
  forgetAlarmed: (ids) => {
    const current = get().alarmed;
    if (!ids.some((id) => current.has(id))) return;
    const alarmed = new Map(current);
    for (const id of ids) alarmed.delete(id);
    saveAlarmed(alarmed);
    set({ alarmed });
  },
  setLang: (lang) => {
    persistLang(lang);
    document.documentElement.lang = lang;
    set({ lang });
  },
  t: (key, vars) => translate(get().lang, key, vars),

  ingest: (message) => {
    const now = Date.now();
    if (message.type === "snapshot") {
      set({
        events: message.events,
        stats: message.stats,
        sources: message.sources,
        ...nextSkew(get().skewSamples, message.server_time, now, true),
        lastMessageAt: now,
        connected: true,
        // a reconnection starts from a clean state: without this, "just
        // happened" halos would survive an outage of several minutes
        fresh: new Set(),
        snapshotRemaining: Math.max(0, Number(message.more) || 0),
        received: true,
      });
      return;
    }

    if (message.type === "snapshot_more") {
      // The rest of the snapshot: HISTORY, upserted like the snapshot's own
      // events -- no halo, no sound, nothing "breaking" about an event that
      // was already in the buffer before this tab connected.
      set((s) => {
        const byId = new Map(s.events.map((e) => [e.id, e]));
        for (const event of message.events ?? []) byId.set(event.id, event);
        const events = [...byId.values()].sort(
          (a, b) => Date.parse(b.time) - Date.parse(a.time),
        );
        return {
          events: capEvents(events),
          ...nextSkew(s.skewSamples, message.server_time, now),
          lastMessageAt: now,
          snapshotRemaining: Math.max(0, Number(message.remaining) || 0),
        };
      });
      return;
    }


    if (message.type === "tick") {
      set({
        stats: message.stats,
        sources: message.sources,
        clients: message.clients,
        ...nextSkew(get().skewSamples, message.server_time, now),
        lastMessageAt: now,
      });
      return;
    }

    if (message.type === "purge") {
      const gone = new Set(message.ids);
      set((s) => ({
        events: s.events.filter((e) => !gone.has(e.id)),
        // a selected event that just disappeared must not keep the detail
        // panel open on something that no longer exists
        selected: s.selected && gone.has(s.selected) ? null : s.selected,
        fresh: new Set([...s.fresh].filter((id) => !gone.has(id))),
        lastMessageAt: now,
      }));
      return;
    }

    // A frame type this build does not know (a newer server): alive, but
    // not an event. Reading it as one threw on `undefined.id`.
    if (message.type !== "event" && message.type !== "update") {
      set({ lastMessageAt: now });
      return;
    }

    // The initial snapshot is filtered `primary_only` server-side, but each
    // broadcast arrived as-is: secondary solutions for the same earthquake
    // (BMKG and USGS for an Indonesian event, for example) showed up as
    // duplicates until the next reconnection.
    if (message.primary === false) return;

    const incoming = message.event;
    const state = get();
    const known = state.events.findIndex((e) => e.id === incoming.id);
    const events =
      known >= 0
        ? state.events.map((e, i) => (i === known ? incoming : e))
        : [incoming, ...state.events];

    events.sort((a, b) => Date.parse(b.time) - Date.parse(a.time));

    // `breaking` comes from the server: it distinguishes "just happened"
    // from "just arrived in the buffer". Without it, the first GDACS cycle
    // would make a hundred alerts several days old flash.
    // `escalated` is the same urgency reached by an UPDATE: a quake revised
    // from M5.9 to M7.1 two minutes after it struck, a bulletin that gained
    // its tsunami flag. Checking `breaking` alone let those pass in silence.
    let fresh = state.fresh;
    if (message.breaking || message.escalated) {
      fresh = new Set(state.fresh);
      fresh.add(incoming.id);
      // the halo fades out on its own after 30 s
      window.setTimeout(() => {
        const current = new Set(useStore.getState().fresh);
        current.delete(incoming.id);
        useStore.setState({ fresh: current });
      }, 30000);

      if (
        state.soundOn &&
        SEVERITY_RANK[incoming.severity] >= SEVERITY_RANK.severe
      ) {
        playAlert(incoming.severity);
      }
    }

    set({ events: capEvents(events), lastMessageAt: now, fresh });
  },
}));

/** Pure function, to be memoized on the component side (`useMemo`).
 *
 * Never pass this straight to `useStore`: a selector that returns a fresh
 * array on every call sends `useSyncExternalStore` into an infinite loop
 * under React 19, and the component never mounts.
 */
export function filterEvents(
  events: SosEvent[],
  filters: Filters,
  now: number,
): SosEvent[] {
  const { kinds, minMagnitude, windowMinutes, query } = filters;
  const minRank = SEVERITY_RANK[filters.minSeverity ?? "info"];
  const cutoff = windowMinutes > 0 ? now - windowMinutes * 60_000 : null;
  const needle = query.trim().toLowerCase();
  return events.filter((event) => {
    if (needle) {
      // we search in what the user SEES (place, title) plus the country,
      // which is only shown as a flag but is still what gets typed
      const haystack = `${event.place} ${event.title} ${event.country ?? ""} ${event.country_code ?? ""}`;
      if (!haystack.toLowerCase().includes(needle)) return false;
    }
    // window first: it's what separates live from historical
    if (cutoff !== null && Date.parse(event.time) < cutoff) return false;
    if (!kinds.has(event.kind)) return false;
    // a tsunami flag is never filtered away by rank: it is the one alert
    // whose label can lag behind the danger
    if (minRank > 0 && !event.tsunami && SEVERITY_RANK[event.severity] < minRank) return false;
    // an event without a magnitude (alert, volcano) is not filtered by the
    // magnitude slider: it simply doesn't have that dimension
    // `magnitude: null` is NOT zero: it means "not yet published" (early
    // EMSC revision). Treating it as 0 was making a real earthquake
    // disappear as soon as the slider left the minimum.
    if (
      minMagnitude > 0 &&
      event.kind === "earthquake" &&
      event.magnitude !== null &&
      event.magnitude < minMagnitude
    ) {
      return false;
    }
    return true;
  });
}

export { ALL_KINDS, SEVERITY_RANK };
