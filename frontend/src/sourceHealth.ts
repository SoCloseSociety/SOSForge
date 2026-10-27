import type { FailedFeed, SourceHealth } from './types'

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
  return failedFeeds(source)?.map((f) => f.feed) ?? null
}

/** The same, with each feed's own error when the server gave it. The
 * server sends objects (`{feed, error}`); joined as strings they printed
 * "Failed feeds: [object Object]" under a degraded NOAA tsunami (review of
 * PR #5, #10). */
export function failedFeeds(source: SourceHealth): FailedFeed[] | null {
  const flag = source.degraded
  const listed = Array.isArray(source.failed_feeds) ? source.failed_feeds.map(asFeed) : null
  if (Array.isArray(flag)) return flag.map(asFeed)
  if (flag && typeof flag === 'object') {
    const inner = flag.failed ?? flag.feeds
    return Array.isArray(inner) ? inner.map(asFeed) : (listed ?? [])
  }
  if (flag === true) return listed ?? []
  if (flag === undefined || flag === null) return listed && listed.length ? listed : null
  return null
}

function asFeed(item: unknown): FailedFeed {
  if (item && typeof item === 'object' && typeof (item as FailedFeed).feed === 'string') {
    const { feed, error } = item as FailedFeed
    return { feed, error: typeof error === 'string' && error.trim() ? error : null }
  }
  return { feed: String(item), error: null }
}

/** What a source is called: the client's own label, else the server's. */
export function sourceLabel(source: Pick<SourceHealth, 'name' | 'label'>, labels: Record<string, string>): string {
  return labels[source.name] ?? source.label ?? source.name
}

/** Is there anything about this source worth the "Show more" detail? */
export function hasIssue(source: SourceHealth): boolean {
  return (!source.connected && !!source.last_error) || degradedFeeds(source) !== null
}
