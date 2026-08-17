import { create } from 'zustand'
import type { ServerMessage, SosEvent, SourceHealth, Stats, Kind, Severity } from './types'
import { detectLang, persistLang, translate, type Lang } from './i18n'

const MAX_EVENTS = 1000

export interface Filters {
  kinds: Set<Kind>
  minMagnitude: number
  sources: Set<string>
  /** fenetre temporelle en minutes; 0 = tout l'historique disponible */
  windowMinutes: number
  /** recherche texte libre: lieu, titre, pays */
  query: string
}

/** Les fenetres proposees, en minutes (0 = tout l'historique disponible).
 *
 * Seules les valeurs vivent ici: les libelles viennent de l'i18n
 * (`t('window.15')`, ...). Des libelles stockes a cote seraient du francais
 * code en dur qui contournerait la traduction le jour ou quelqu'un les
 * afficherait. "Direct" (15 min) est la vraie promesse du produit: ce qui vient
 * de tomber; les autres servent a reprendre du contexte sans quitter la page.
 */
export const WINDOWS: number[] = [15, 60, 360, 1440, 0]

interface State {
  events: SosEvent[]
  connected: boolean
  reconnectIn: number
  stats: Stats | null
  sources: SourceHealth[]
  clients: number
  /** decalage horloge navigateur -> horloge serveur, en ms */
  clockSkew: number
  lastMessageAt: number
  selected: string | null
  soundOn: boolean
  lang: Lang
  filters: Filters
  fresh: Set<string>
  /** zone vers laquelle la carte doit se rendre (resultat de recherche) */
  focus: { lat: number; lon: number; zoom: number; name: string } | null

  ingest: (message: ServerMessage) => void
  setConnected: (value: boolean) => void
  select: (id: string | null) => void
  toggleSound: () => void
  toggleKind: (kind: Kind) => void
  setMinMagnitude: (value: number) => void
  setWindow: (minutes: number) => void
  setQuery: (query: string) => void
  setFocus: (focus: State['focus']) => void
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

/** Un bip synthetise: pas de fichier audio a embarquer, et le timbre monte avec
 * la gravite. Le navigateur exige un geste utilisateur avant de jouer du son,
 * d'ou le bouton d'activation explicite dans l'entete. */
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
    /* pas de son disponible: ce n'est pas une raison pour casser le flux */
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
    // 24 h par defaut: assez pour du contexte, assez court pour que la carte
    // montre l'actualite et non un catalogue
    windowMinutes: 1440,
    query: '',
  },
  fresh: new Set(),
  focus: null,

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
      return { filters: { ...state.filters, kinds } }
    }),
  setMinMagnitude: (value) =>
    set((state) => ({ filters: { ...state.filters, minMagnitude: value } })),
  setWindow: (minutes) => set((state) => ({ filters: { ...state.filters, windowMinutes: minutes } })),
  setQuery: (query) => set((state) => ({ filters: { ...state.filters, query } })),
  setFocus: (focus) => set({ focus }),
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
        // une reconnexion repart d'un etat propre: sans ca, des halos "vient de
        // tomber" survivaient a une coupure de plusieurs minutes
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

    const incoming = message.event
    const state = get()
    const known = state.events.findIndex((e) => e.id === incoming.id)
    const events =
      known >= 0
        ? state.events.map((e, i) => (i === known ? incoming : e))
        : [incoming, ...state.events]

    events.sort((a, b) => Date.parse(b.time) - Date.parse(a.time))

    // `breaking` vient du serveur: il distingue "vient de se produire" de "vient
    // d'arriver dans le buffer". Sans lui, le premier cycle GDACS ferait clignoter
    // une centaine d'alertes vieilles de plusieurs jours.
    let fresh = state.fresh
    if (message.breaking) {
      fresh = new Set(state.fresh)
      fresh.add(incoming.id)
      // le halo s'eteint tout seul au bout de 30 s
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

/** Fonction pure, a memoiser cote composant (`useMemo`).
 *
 * A ne surtout PAS passer directement a `useStore`: un selecteur qui renvoie un
 * nouveau tableau a chaque appel fait boucler `useSyncExternalStore` a l'infini
 * sous React 19, et le composant ne monte jamais.
 */
export function filterEvents(events: SosEvent[], filters: Filters, now: number): SosEvent[] {
  const { kinds, minMagnitude, windowMinutes, query } = filters
  const cutoff = windowMinutes > 0 ? now - windowMinutes * 60_000 : null
  const needle = query.trim().toLowerCase()
  return events.filter((event) => {
    if (needle) {
      // on cherche dans ce que l'utilisateur VOIT (le lieu, le titre) plus le
      // pays, qui n'est affiche que comme drapeau mais reste ce qu'on tape
      const haystack = `${event.place} ${event.title} ${event.country ?? ''} ${event.country_code ?? ''}`
      if (!haystack.toLowerCase().includes(needle)) return false
    }
    // la fenetre d'abord: c'est elle qui separe le direct de l'historique
    if (cutoff !== null && Date.parse(event.time) < cutoff) return false
    if (!kinds.has(event.kind)) return false
    // un evenement sans magnitude (alerte, volcan) n'est pas filtre par le
    // curseur de magnitude: il n'a simplement pas cette dimension
    // `magnitude: null` n'est PAS zero: c'est "pas encore publiee" (revision
    // precoce de l'EMSC). Le traiter comme 0 faisait disparaitre un seisme reel
    // des que le curseur quittait le minimum.
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
