import { create } from 'zustand'
import type { ServerMessage, SosEvent, SourceHealth, Stats, Kind, Severity } from './types'
import { detectLang, persistLang, translate, type Lang } from './i18n'

const MAX_EVENTS = 1000

export interface Filters {
  kinds: Set<Kind>
  minMagnitude: number
  sources: Set<string>
  /** time window in minutes; 0 = all available history */
  windowMinutes: number
  /** free-text search: place, title, country */
  query: string
}

/** The offered windows, in minutes (0 = all available history).
 *
 * Only the values live here: the labels come from i18n (`t('window.15')`,
 * ...). Labels stored alongside would be hardcoded French that would bypass
 * translation the day someone displays them. "Live" (15 min) is the
 * product's real promise: what just happened; the others are for regaining
 * context without leaving the page.
 */
export const WINDOWS: number[] = [15, 60, 360, 1440, 0]

interface State {
  events: SosEvent[]
  connected: boolean
  reconnectIn: number
  stats: Stats | null
  sources: SourceHealth[]
  clients: number
  /** browser clock -> server clock offset, in ms */
  clockSkew: number
  lastMessageAt: number
  selected: string | null
  soundOn: boolean
  lang: Lang
  filters: Filters
  fresh: Set<string>
  /** area the map should fly to (search result) */
  focus: { lat: number; lon: number; zoom: number; name: string } | null
  /** the place the user wants to be warned about. Wave arrival is computed
   * against this point, so it is opt-in and never guessed silently. */
  watch: { lat: number; lon: number; name: string } | null

  ingest: (message: ServerMessage) => void
  setConnected: (value: boolean) => void
  select: (id: string | null) => void
  toggleSound: () => void
  toggleKind: (kind: Kind) => void
  setMinMagnitude: (value: number) => void
  setWindow: (minutes: number) => void
  setQuery: (query: string) => void
  setFocus: (focus: State['focus']) => void
  setWatch: (watch: State['watch']) => void
  setLang: (lang: Lang) => void
  t: (key: string, vars?: Record<string, string | number>) => string
}

const ALL_KINDS: Kind[] = [
  'earthquake',
  'tsunami',
  'volcano',
  'cyclone',
  'flood',
  'wildfire',
  'storm',
  'heat',
  'drought',
  'other',
]

const SEVERITY_RANK: Record<Severity, number> = {
  info: 0,
  minor: 1,
  moderate: 2,
  severe: 3,
  extreme: 4,
}

/** A synthesized beep: no audio file to bundle, and the pitch rises with
 * severity. The browser requires a user gesture before playing sound, hence
 * the explicit enable button in the header. */
let audioContext: AudioContext | null = null

export function playAlert(severity: Severity) {
  try {
    audioContext ||= new AudioContext()
    if (audioContext.state === 'suspended') void audioContext.resume()
    const now = audioContext.currentTime
    const gain = audioContext.createGain()
    gain.connect(audioContext.destination)
    gain.gain.setValueAtTime(0.0001, now)
    gain.gain.exponentialRampToValueAtTime(severity === 'extreme' ? 0.25 : 0.12, now + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.9)

    const beeps = severity === 'extreme' ? 3 : 1
    for (let i = 0; i < beeps; i += 1) {
      const osc = audioContext.createOscillator()
      osc.type = 'sine'
      osc.frequency.setValueAtTime(severity === 'extreme' ? 880 : 587, now + i * 0.22)
      osc.connect(gain)
      osc.start(now + i * 0.22)
      osc.stop(now + i * 0.22 + 0.18)
    }
  } catch {
    /* no sound available: not a reason to break the feed */
  }
}

const FILTERS_KEY = 'sosforge.filters'
const WATCH_KEY = 'sosforge.watch'

/** The watched place survives a reload: an alert you have to re-arm on every
 * visit is an alert you will not have when it matters. */
function loadWatch(): { lat: number; lon: number; name: string } | null {
  try {
    const raw = localStorage.getItem(WATCH_KEY)
    if (!raw) return null
    const saved = JSON.parse(raw)
    if (typeof saved?.lat !== 'number' || typeof saved?.lon !== 'number') return null
    return { lat: saved.lat, lon: saved.lon, name: String(saved.name ?? '') }
  } catch {
    return null
  }
}

function saveWatch(watch: { lat: number; lon: number; name: string } | null): void {
  try {
    if (watch) localStorage.setItem(WATCH_KEY, JSON.stringify(watch))
    else localStorage.removeItem(WATCH_KEY)
  } catch {
    /* private mode: it just will not persist */
  }
}

/** Filters survive a reload: coming back to the page and finding
 * "Live + earthquakes only" already set avoids redoing three clicks on
 * every visit. Text search, though, is NOT persisted -- finding an invisible
 * filter that hides the whole feed would be confusing. */
function loadFilters(): Pick<Filters, 'kinds' | 'minMagnitude' | 'windowMinutes'> | null {
  try {
    const raw = localStorage.getItem(FILTERS_KEY)
    if (!raw) return null
    const saved = JSON.parse(raw)
    const kinds: Kind[] = Array.isArray(saved.kinds) ? saved.kinds : ALL_KINDS
    return {
      kinds: new Set(kinds.filter((k) => ALL_KINDS.includes(k))),
      minMagnitude: Number(saved.minMagnitude) || 0,
      windowMinutes: WINDOWS.includes(Number(saved.windowMinutes))
        ? Number(saved.windowMinutes)
        : 1440,
    }
  } catch {
    return null
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
      }),
    )
  } catch {
    /* private mode: filters won't survive, that's all */
  }
}

export const useStore = create<State>((set, get) => ({
  events: [],
  connected: false,
  reconnectIn: 0,
  stats: null,
  sources: [],
  clients: 0,
  clockSkew: 0,
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
    query: '',
    ...(loadFilters() ?? {}),
  },
  fresh: new Set(),
  focus: null,
  watch: loadWatch(),

  setConnected: (value) => set({ connected: value }),
  select: (id) => set({ selected: id }),
  toggleSound: () => {
    const next = !get().soundOn
    if (next) playAlert('info')
    set({ soundOn: next })
  },
  toggleKind: (kind) =>
    set((state) => {
      const kinds = new Set(state.filters.kinds)
      if (kinds.has(kind)) kinds.delete(kind)
      else kinds.add(kind)
      const next = { ...state.filters, kinds }
      saveFilters(next)
      return { filters: next }
    }),
  setMinMagnitude: (value) =>
    set((state) => {
      const next = { ...state.filters, minMagnitude: value }
      saveFilters(next)
      return { filters: next }
    }),
  setWindow: (minutes) =>
    set((state) => {
      const next = { ...state.filters, windowMinutes: minutes }
      saveFilters(next)
      return { filters: next }
    }),
  setQuery: (query) => set((state) => ({ filters: { ...state.filters, query } })),
  setFocus: (focus) => set({ focus }),
  setWatch: (watch) => {
    saveWatch(watch)
    set({ watch })
  },
  setLang: (lang) => {
    persistLang(lang)
    document.documentElement.lang = lang
    set({ lang })
  },
  t: (key, vars) => translate(get().lang, key, vars),

  ingest: (message) => {
    const now = Date.now()
    if (message.type === 'snapshot') {
      set({
        events: message.events,
        stats: message.stats,
        sources: message.sources,
        clockSkew: new Date(message.server_time).getTime() - now,
        lastMessageAt: now,
        connected: true,
        // a reconnection starts from a clean state: without this, "just
        // happened" halos would survive an outage of several minutes
        fresh: new Set(),
      })
      return
    }

    if (message.type === 'tick') {
      set({
        stats: message.stats,
        sources: message.sources,
        clients: message.clients,
        clockSkew: new Date(message.server_time).getTime() - now,
        lastMessageAt: now,
      })
      return
    }

    // The initial snapshot is filtered `primary_only` server-side, but each
    // broadcast arrived as-is: secondary solutions for the same earthquake
    // (BMKG and USGS for an Indonesian event, for example) showed up as
    // duplicates until the next reconnection.
    if (message.primary === false) return

    const incoming = message.event
    const state = get()
    const known = state.events.findIndex((e) => e.id === incoming.id)
    const events =
      known >= 0
        ? state.events.map((e, i) => (i === known ? incoming : e))
        : [incoming, ...state.events]

    events.sort((a, b) => Date.parse(b.time) - Date.parse(a.time))

    // `breaking` comes from the server: it distinguishes "just happened"
    // from "just arrived in the buffer". Without it, the first GDACS cycle
    // would make a hundred alerts several days old flash.
    let fresh = state.fresh
    if (message.breaking) {
      fresh = new Set(state.fresh)
      fresh.add(incoming.id)
      // the halo fades out on its own after 30 s
      window.setTimeout(() => {
        const current = new Set(useStore.getState().fresh)
        current.delete(incoming.id)
        useStore.setState({ fresh: current })
      }, 30000)

      if (state.soundOn && SEVERITY_RANK[incoming.severity] >= SEVERITY_RANK.severe) {
        playAlert(incoming.severity)
      }
    }

    set({ events: events.slice(0, MAX_EVENTS), lastMessageAt: now, fresh })
  },
}))

/** Pure function, to be memoized on the component side (`useMemo`).
 *
 * Never pass this straight to `useStore`: a selector that returns a fresh
 * array on every call sends `useSyncExternalStore` into an infinite loop
 * under React 19, and the component never mounts.
 */
export function filterEvents(events: SosEvent[], filters: Filters, now: number): SosEvent[] {
  const { kinds, minMagnitude, windowMinutes, query } = filters
  const cutoff = windowMinutes > 0 ? now - windowMinutes * 60_000 : null
  const needle = query.trim().toLowerCase()
  return events.filter((event) => {
    if (needle) {
      // we search in what the user SEES (place, title) plus the country,
      // which is only shown as a flag but is still what gets typed
      const haystack = `${event.place} ${event.title} ${event.country ?? ''} ${event.country_code ?? ''}`
      if (!haystack.toLowerCase().includes(needle)) return false
    }
    // window first: it's what separates live from historical
    if (cutoff !== null && Date.parse(event.time) < cutoff) return false
    if (!kinds.has(event.kind)) return false
    // an event without a magnitude (alert, volcano) is not filtered by the
    // magnitude slider: it simply doesn't have that dimension
    // `magnitude: null` is NOT zero: it means "not yet published" (early
    // EMSC revision). Treating it as 0 was making a real earthquake
    // disappear as soon as the slider left the minimum.
    if (
      minMagnitude > 0 &&
      event.kind === 'earthquake' &&
      event.magnitude !== null &&
      event.magnitude < minMagnitude
    ) {
      return false
    }
    return true
  })
}

export { ALL_KINDS, SEVERITY_RANK }
