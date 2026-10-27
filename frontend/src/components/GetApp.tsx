import { useEffect, useState } from 'react'
import { useStore } from '../store'

/** What `/download/latest.json` says (contract in tasks/todo.md, round 2;
 * served by lane G). Only what the entry needs. */
interface Release {
  version: string
  url: string
}

let pending: Promise<Release | null> | null = null

/** The published release, or null when there is none to point at. Asked
 * once per page: a link to a download page that does not exist yet would
 * send the reader to the app shell (the SPA fallback answers 200 with
 * HTML), so the entry only appears when the file is really there. */
export function latestRelease(): Promise<Release | null> {
  pending ??= fetch('/download/latest.json', { headers: { Accept: 'application/json' } })
    .then(async (response) => {
      if (!response.ok || !(response.headers.get('content-type') ?? '').includes('json')) return null
      const body = (await response.json()) as Partial<Release>
      return typeof body.version === 'string' ? { version: body.version, url: '/download/' } : null
    })
    .catch(() => {
      pending = null // a dropped request is retried next time
      return null
    })
  return pending
}

/** Test seam. */
export function resetRelease(): void {
  pending = null
}

export function isAndroid(): boolean {
  return /Android/i.test(navigator.userAgent)
}

/** The release, read once by every entry on the page. */
function useRelease(): Release | null {
  const [release, setRelease] = useState<Release | null>(null)
  useEffect(() => {
    let alive = true
    void latestRelease().then((found) => {
      if (alive) setRelease(found)
    })
    return () => {
      alive = false
    }
  }, [])
  return release
}

const DISMISS_KEY = 'sosforge.getapp.dismissed'

function dismissedVersion(): string | null {
  try {
    return localStorage.getItem(DISMISS_KEY)
  } catch {
    return null
  }
}

/** "Get the app": prominent on an Android phone, where it is what the reader
 * needs (a web page cannot alert once its tab is closed; the app can), and
 * a discreet footer link everywhere else (`GetAppLink`). `/download/` is
 * lane G's page. Dismissed once, it stays dismissed for that version: a
 * banner that comes back on every visit is nagging. */
export function GetAppBanner() {
  const t = useStore((s) => s.t)
  const release = useRelease()
  const [dismissed, setDismissed] = useState(dismissedVersion)
  if (!release || !isAndroid() || dismissed === release.version) return null
  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, release.version)
    } catch {
      /* private mode: it comes back next visit, nothing worse */
    }
    setDismissed(release.version)
  }
  return (
    <aside className="get-app-banner" aria-label={t('app.get')}>
      <span className="get-app-glyph" aria-hidden="true">
        ⤓
      </span>
      <p>{t('app.get.why')}</p>
      <a className="btn btn-secondary btn-compact" href={release.url}>
        {t('app.get')}
      </a>
      <button
        type="button"
        className="btn btn-quiet btn-icon"
        aria-label={t('app.get.dismiss')}
        onClick={dismiss}
      >
        ✕
      </button>
    </aside>
  )
}

export function GetAppLink() {
  const t = useStore((s) => s.t)
  const release = useRelease()
  if (!release) return null
  return (
    <a className="get-app-link" href={release.url}>
      {t('app.get.link', { version: release.version })}
    </a>
  )
}

/** For the watch card: the honest sentence about a closed tab, with the way
 * out when the app is published. */
export function useAppPublished(): boolean {
  return useRelease() !== null
}
