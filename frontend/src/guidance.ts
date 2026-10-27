import type {
  ApiVersion,
  GuidanceLine,
  GuidancePhase,
  GuidanceResponse,
  Kind,
  KindGuidance,
  SosEvent,
} from './types'

/** Prevention guidance, feature-detected.
 *
 * `GET /api/version` says which optional features this API serves; only when
 * it lists `guidance` is `GET /api/guidance?lang=` asked for. An API without
 * either answers 404 (or HTML from the SPA fallback), and in that case the
 * detail panel keeps today's display -- the CAP instruction -- and says
 * nothing about prevention. Detect, never assume: a newer frontend in front
 * of an older API must degrade, not break.
 *
 * Both answers are cached for the page's lifetime: the guidance text changes
 * with releases, not with the feed.
 */

const GUIDANCE_FEATURE = 'guidance'

let versionPromise: Promise<ApiVersion | null | undefined> | null = null
const guidanceByLang = new Map<string, Promise<GuidanceResponse | null>>()

/** `null` is an ANSWER (no such endpoint, not JSON), `undefined` is a
 * failure to get one (network down, a 5xx): only an answer is worth caching,
 * otherwise one dropped request at page load would hide the guidance for the
 * whole session on the saturated network this product is built for. */
async function getJson<T>(url: string): Promise<T | null | undefined> {
  try {
    const response = await fetch(url, { headers: { Accept: 'application/json' } })
    if (response.status >= 500) return undefined
    if (!response.ok) return null
    // The SPA fallback answers 200 with HTML for any unknown path: the
    // content type is the only thing that separates "no such endpoint" from
    // an answer.
    const type = response.headers.get('content-type') ?? ''
    if (!type.includes('json')) return null
    return (await response.json()) as T
  } catch {
    return undefined
  }
}

/** The version check itself: `undefined` while no answer came. */
function askVersion(): Promise<ApiVersion | null | undefined> {
  versionPromise ??= getJson<ApiVersion>('/api/version').then((v) => {
    if (v === undefined) {
      versionPromise = null // ask again next time
      return undefined
    }
    return v && Array.isArray(v.features) ? v : null
  })
  return versionPromise
}

/** The API's version and feature list, or null when the endpoint is absent. */
export async function apiVersion(): Promise<ApiVersion | null> {
  return (await askVersion()) ?? null
}

/** Does this API serve `feature`? `undefined` when it could not be asked
 * (network down, a 5xx): not a "no", so the caller can ask again instead of
 * hiding the feature for the whole session. */
export async function serves(feature: string): Promise<boolean | undefined> {
  const version = await askVersion()
  return version === undefined ? undefined : !!version?.features.includes(feature)
}

/** Does this API serve prevention guidance? */
export async function hasGuidance(): Promise<boolean> {
  const version = await apiVersion()
  return !!version?.features.includes(GUIDANCE_FEATURE)
}

/** One line of `GET /api/guidance` (contract v2, lane B):
 * `{text, source: {name, url, lang}, needs_review}`. */
interface WireLine {
  text?: unknown
  source?: { name?: unknown; url?: unknown; lang?: unknown } | null
  needs_review?: unknown
}

/** A line is usable only when it has both a text and an official https URL.
 * The server validates its catalogue, but this is the last step before a
 * reader acts on the words: a line without a source must never be shown as
 * if it were sourced. */
function cleanLines(lines: unknown): GuidanceLine[] {
  if (!Array.isArray(lines)) return []
  const out: GuidanceLine[] = []
  for (const raw of lines as WireLine[]) {
    if (!raw || typeof raw !== 'object') continue
    const text = typeof raw.text === 'string' ? raw.text.trim() : ''
    const url = typeof raw.source?.url === 'string' ? raw.source.url : ''
    if (!text || !/^https:\/\//.test(url)) continue
    const name = typeof raw.source?.name === 'string' ? raw.source.name.trim() : ''
    out.push({ text, source_url: url, source: name || null, needs_review: raw.needs_review === true })
  }
  return out
}

/** The guidance table for one language, or null when the API does not
 * serve it. */
export function guidance(lang: string): Promise<GuidanceResponse | null> {
  let pending = guidanceByLang.get(lang)
  if (!pending) {
    pending = hasGuidance().then(async (ok) => {
      if (!ok) {
        // an unanswered version check is retried, so this must be too
        if (versionPromise === null) guidanceByLang.delete(lang)
        return null
      }
      // 422: a language the catalogue does not have; 503: a broken
      // catalogue. Either way the panel keeps the CAP "what to do".
      const raw = await getJson<{ lang?: unknown; kinds?: unknown }>(
        `/api/guidance?lang=${encodeURIComponent(lang)}`,
      )
      if (raw === undefined) guidanceByLang.delete(lang) // retry next time
      if (!raw || typeof raw !== 'object' || !raw.kinds || typeof raw.kinds !== 'object') return null
      const kinds: Record<string, KindGuidance> = {}
      for (const [kind, value] of Object.entries(raw.kinds as Record<string, unknown>)) {
        if (!value || typeof value !== 'object') continue
        const phases = value as Record<string, unknown>
        const cleaned: KindGuidance = {
          kind: kind as Kind,
          before: cleanLines(phases.before),
          during: cleanLines(phases.during),
          after: cleanLines(phases.after),
        }
        if (cleaned.before.length + cleaned.during.length + cleaned.after.length === 0) continue
        kinds[kind] = cleaned
      }
      if (Object.keys(kinds).length === 0) return null
      return { lang: typeof raw.lang === 'string' ? raw.lang : lang, kinds }
    })
    guidanceByLang.set(lang, pending)
  }
  return pending
}

/** Which phase matters NOW for this event, so the panel opens on it:
 *  - a warning dated in the future is lead time: BEFORE;
 *  - an alert still running, or anything from the last hour: DURING;
 *  - older than that, the event is over: AFTER.
 * The other two phases stay one tap away. */
export function currentPhase(event: SosEvent, now: number): GuidancePhase {
  const age = (now - Date.parse(event.time)) / 1000
  if (age < -60) return 'before'
  if (event.ongoing || age < 3600) return 'during'
  return 'after'
}

/** The guidance for one kind, or null: absent feature, absent kind, or a
 * table with nothing sourced for it. */
export async function guidanceFor(lang: string, kind: Kind): Promise<KindGuidance | null> {
  const table = await guidance(lang)
  return table?.kinds[kind] ?? null
}

/** Test seam: forget every cached answer. */
export function resetGuidanceCache(): void {
  versionPromise = null
  guidanceByLang.clear()
}
