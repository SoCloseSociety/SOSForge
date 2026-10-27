import type { SourceHealth } from './types'

/** The feeds of a source that failed while the source as a whole still
 * answers, or `null` when it is not degraded.
 *
 * Lane C adds `degraded` to `/api/sources` while this client is written, so
 * the client reads the shapes a reasonable server would send -- `degraded:
 * true` with `failed_feeds`, `degraded` as the list itself, or an object with
 * `failed` / `feeds` -- and an API without the field reads as "not degraded",
 * which is what it reported before. An empty list still means degraded: the
 * server said so, it just did not say which feed.
 */
export function degradedFeeds(source: SourceHealth): string[] | null {
  const flag = source.degraded
  const listed = Array.isArray(source.failed_feeds) ? source.failed_feeds : null
  if (Array.isArray(flag)) return flag.map(String)
  if (flag && typeof flag === 'object') {
    const inner = flag.failed ?? flag.feeds
    return Array.isArray(inner) ? inner.map(String) : (listed ?? [])
  }
  if (flag === true) return listed ?? []
  if (flag === undefined || flag === null) return listed && listed.length ? listed : null
  return null
}

/** Is there anything about this source worth the "Show more" detail? */
export function hasIssue(source: SourceHealth): boolean {
  return (!source.connected && !!source.last_error) || degradedFeeds(source) !== null
}
