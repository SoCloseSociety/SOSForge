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

let versionPromise: Promise<ApiVersion | null> | null = null
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

/** The API's version and feature list, or null when the endpoint is absent. */
export function apiVersion(): Promise<ApiVersion | null> {
  versionPromise ??= getJson<ApiVersion>('/api/version').then((v) => {
    if (v === undefined) {
      versionPromise = null // ask again next time
      return null
    }
    return v && Array.isArray(v.features) ? v : null
  })
  return versionPromise
}

/** Does this API serve prevention guidance? */
export async function hasGuidance(): Promise<boolean> {
  const version = await apiVersion()
  return !!version?.features.includes(GUIDANCE_FEATURE)
}

/** The official page a line was taken from, wherever the table puts it:
 * `source_url`, `url`, or a `source` object `{url, name}`. Lane B's catalogue
 * was being written while this client was, so the client reads the few
 * spellings a reasonable table would use rather than pinning one guess. */
function lineUrl(line: Record<string, unknown>): string | null {
  const nested =
    line.source && typeof line.source === 'object'
      ? (line.source as Record<string, unknown>).url
      : undefined
  const candidates = [line.source_url, line.url, nested, line.source]
  for (const value of candidates) {
    if (typeof value === 'string' && /^https?:\/\//.test(value)) return value
  }
  return null
}

/** Who publishes it, for display. Never the URL itself. */
function linePublisher(line: Record<string, unknown>): string | null {
  const nested =
    line.source && typeof line.source === 'object'
      ? (line.source as Record<string, unknown>).name
      : undefined
  for (const value of [line.source_name, line.publisher, line.authority, nested, line.source]) {
    if (typeof value === 'string' && value.trim() !== '' && !/^https?:\/\//.test(value)) {
      return value.trim()
    }
  }
  return null
}

/** A line is usable only when it has both a text and an official URL. The
 * server is ours, but the table behind it is edited by hand, and a line with
 * a text and no source must not be shown as if it were sourced. */
function cleanLines(lines: unknown): GuidanceLine[] {
  if (!Array.isArray(lines)) return []
  const out: GuidanceLine[] = []
  for (const raw of lines) {
    if (!raw || typeof raw !== 'object') continue
    const line = raw as Record<string, unknown>
    const text = typeof line.text === 'string' ? line.text.trim() : ''
    const url = lineUrl(line)
    if (!text || !url) continue
    out.push({ text, source_url: url, source: linePublisher(line) })
  }
  return out
}

/** The kinds of a guidance answer as `[kind, entry]` pairs. Accepts the kinds
 * keyed by name (`{kinds: {earthquake: {...}}}`), as a list
 * (`{kinds: [{kind: 'earthquake', ...}]}`), under `guidance` instead of
 * `kinds`, or keyed at the top level next to `lang`: the table is the
 * contract, not its container. */
function kindEntries(raw: Record<string, unknown>): [string, Record<string, unknown>][] {
  const container = raw.kinds ?? raw.guidance ?? raw
  if (Array.isArray(container)) {
    return container
      .filter((e): e is Record<string, unknown> => !!e && typeof e === 'object')
      .filter((e) => typeof e.kind === 'string')
      .map((e) => [e.kind as string, e])
  }
  if (container && typeof container === 'object') {
    return Object.entries(container as Record<string, unknown>)
      .filter(([, v]) => !!v && typeof v === 'object' && !Array.isArray(v))
      .map(([k, v]) => [k, v as Record<string, unknown>])
  }
  return []
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
      const raw = await getJson<Record<string, unknown>>(
        `/api/guidance?lang=${encodeURIComponent(lang)}`,
      )
      if (raw === undefined) guidanceByLang.delete(lang) // retry next time
      if (!raw || typeof raw !== 'object') return null
      const kinds: Record<string, KindGuidance> = {}
      for (const [kind, entry] of kindEntries(raw)) {
        // a phase may sit directly on the entry or under `phases`
        const phases = (
          entry.phases && typeof entry.phases === 'object' ? entry.phases : entry
        ) as Record<string, unknown>
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
