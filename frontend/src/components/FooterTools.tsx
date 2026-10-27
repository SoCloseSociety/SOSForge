import { Component, Suspense, lazy, useEffect, useMemo, useRef, useState, type ComponentType, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../store'

/* Two tools in the footer's credit line, where they cost the first screen
 * nothing: on a phone the live feed keeps the fold (tools/responsive.py),
 * on a wide screen the footer is always in view. The place report's button
 * (under the scope bar) shares their dialog.
 *
 *  - the climate indicators (lane C2's panel), in a dialog, loaded only
 *    when opened: the panel and its CSS stay out of the first paint, and
 *    /api/climate is not polled for a reader who never looks;
 *  - "Save the world map for offline use", on the service worker's message
 *    contract (lane G, public/sw.js), shown only while there is something
 *    to save. */

/** What Tab can reach inside a dialog: not what sits in a collapsed detail
 * (`hidden`, or a closed <details> body). Counted, the last "focusable" of the
 * climate dialog was a link in a hidden list: Tab left the aria-modal dialog
 * and Shift+Tab from the first control did nothing (final review of PR #5). */
export function focusables(root: HTMLElement): HTMLElement[] {
  return [
    ...root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), select, textarea, details > summary, [tabindex]:not([tabindex="-1"])',
    ),
  ].filter((el) => !el.closest('[hidden], [inert], [aria-hidden="true"]') && !inClosedDetails(el))
}

function inClosedDetails(el: HTMLElement): boolean {
  const details = el.closest('details')
  if (!details || details.open) return false
  const summary = details.querySelector(':scope > summary')
  return !summary || !summary.contains(el)
}

/** A failed chunk stays inside the dialog. Without it, a stale tab after a
 * deploy, or a dropped request, put the root crash page in place of the
 * live feed, the map and the alerts (final review of PR #5). */
class ChunkBoundary extends Component<
  { children: ReactNode; fallback: (error: Error) => ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render(): ReactNode {
    return this.state.error ? this.props.fallback(this.state.error) : this.props.children
  }
}

const loadPanel = () => import('./ClimatePanel').then((m) => ({ default: m.ClimatePanel }))
const loadPlace = () => import('./PlaceReport').then((m) => ({ default: m.PlaceReport }))

/** The words one panel's button and dialog say, as i18n keys. */
interface PanelWords {
  title: string
  close: string
  loading: string
  failed: string
}

/** A panel behind one button, loaded only when opened, in a dialog over
 * everything (a bottom sheet on a phone): the panel and its CSS stay out of
 * the first paint, and nothing is fetched for a reader who never looks. */
function PanelButton<P extends object>({
  load,
  panel,
  words,
  icon,
  label,
  className,
}: {
  load: () => Promise<{ default: ComponentType<P> }>
  panel: P
  words: PanelWords
  icon: string
  label: string
  className: string
}) {
  const t = useStore((s) => s.t)
  const [open, setOpen] = useState(false)
  // React.lazy keeps a rejection forever: a retry needs a new one
  const [attempt, setAttempt] = useState(0)
  // `attempt` is in the deps on purpose: it is the reason to rebuild
  const Panel = useMemo(() => lazy(load), [load, attempt])
  const trigger = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLDivElement>(null)

  // Into the dialog on open, back to the button on close; Esc from anywhere.
  useEffect(() => {
    if (!open) return
    dialog.current?.focus()
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onEscape)
    const back = trigger.current
    return () => {
      document.removeEventListener('keydown', onEscape)
      back?.focus()
    }
  }, [open])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab' || !dialog.current) return
    const items = focusables(dialog.current)
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (event.shiftKey && (active === first || active === dialog.current)) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <>
      <button
        type="button"
        ref={trigger}
        className={className}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <span aria-hidden="true">{icon}</span> {label}
      </button>
      {open
        ? createPortal(
            <>
              <div className="climate-scrim" aria-hidden="true" onClick={() => setOpen(false)} />
              <div
                ref={dialog}
                className="climate-dialog"
                role="dialog"
                aria-modal="true"
                aria-label={t(words.title)}
                tabIndex={-1}
                onKeyDown={onKeyDown}
              >
                <div className="climate-dialog-head">
                  <button
                    type="button"
                    className="btn btn-quiet btn-icon"
                    aria-label={t(words.close)}
                    onClick={() => setOpen(false)}
                  >
                    ✕
                  </button>
                </div>
                <ChunkBoundary
                  key={attempt}
                  fallback={(error) => (
                    // A plain sentence first. After a deploy the old chunk is a
                    // 404 that no retry can fetch: reloading is what works. The
                    // raw text (an unbreakable asset URL that ran past a 280 px
                    // card) waits behind "Show more" (recheck of PR #5).
                    <div className="climate-dialog-wait climate-failed" role="alert">
                      <p>{t(words.failed)}</p>
                      <p className="climate-failed-actions">
                        <button type="button" className="btn btn-secondary" onClick={() => location.reload()}>
                          {t('climate.reload')}
                        </button>
                        <button type="button" className="btn btn-quiet" onClick={() => setAttempt((n) => n + 1)}>
                          {t('climate.retry')}
                        </button>
                      </p>
                      <details className="climate-failed-more">
                        <summary>{t('more.show')}</summary>
                        <code>{error.message}</code>
                      </details>
                    </div>
                  )}
                >
                  <Suspense fallback={<p className="climate-dialog-wait">{t(words.loading)}</p>}>
                    <Panel {...panel} />
                  </Suspense>
                </ChunkBoundary>
              </div>
            </>,
            document.body,
          )
        : null}
    </>
  )
}

const CLIMATE_WORDS: PanelWords = {
  title: 'climate.title',
  close: 'climate.close',
  loading: 'climate.loading',
  failed: 'climate.failed.reload',
}
const NO_PROPS = {}

/** The climate indicators, behind one button. */
export function ClimateButton({ load = loadPanel }: { load?: typeof loadPanel } = {}) {
  const t = useStore((s) => s.t)
  return (
    <PanelButton
      load={load}
      panel={NO_PROPS}
      words={CLIMATE_WORDS}
      icon="◔"
      label={t('climate.title')}
      className="footer-tool"
    />
  )
}

const PLACE_WORDS: PanelWords = {
  title: 'place.title',
  close: 'place.close',
  loading: 'place.loading',
  failed: 'place.failed.reload',
}

/** The place report (weather, sea, wind, air, risks, history) of a place
 * the reader SEARCHED or CLICKED, behind one button under the scope bar.
 * Never give it the device position: /api/place sends its point to about
 * fifteen third parties, and "your location never leaves your device" is
 * a promise. Inline, the report took about two phone screens and pushed
 * every live event below the fold; here it costs one row, and it is
 * fetched only when opened. */
export function PlaceReportButton({
  lat,
  lon,
  name,
  load = loadPlace,
}: {
  lat: number
  lon: number
  name: string
  load?: typeof loadPlace
}) {
  const t = useStore((s) => s.t)
  const panel = useMemo(() => ({ lat, lon, name }), [lat, lon, name])
  return (
    <PanelButton
      load={load}
      panel={panel}
      words={PLACE_WORDS}
      icon="☁"
      label={t('place.open', { place: name })}
      className="place-open"
    />
  )
}

/** The worker's status reply, as public/sw.js sends it. */
export interface BasemapStatus {
  type: 'sosforge:basemap-status'
  saved: boolean
  saving: boolean
  files: number
  missingFiles: number
  missingBytes: number
  unmeasuredFiles: number
  /** true / false when the connection says, null when it does not */
  unmetered: boolean | null
}

function isStatus(data: unknown): data is BasemapStatus {
  const d = data as BasemapStatus | null
  return (
    !!d &&
    d.type === 'sosforge:basemap-status' &&
    typeof d.saved === 'boolean' &&
    typeof d.missingFiles === 'number' &&
    typeof d.missingBytes === 'number' &&
    typeof d.unmeasuredFiles === 'number'
  )
}

/** Up to this share of files the worker could not size, the sum is "about"
 * the transfer (today 4 small glyph files of 118). Beyond it the sum covers
 * only the files that answered: "at least" -- on a flaky link 50 of 84 HEADs
 * failed and "~6 MB" was offered for a 16 MB download (final review). */
const UNSIZED_SHARE = 0.1

type T = (key: string, vars?: Record<string, string | number>) => string

/** The button's label: the size the worker measured; no size at all when it
 * could size nothing (offline). Mobile data is said. */
export function saveLabel(t: T, status: BasemapStatus): string {
  if (status.missingBytes <= 0 || status.unmeasuredFiles >= status.missingFiles) return t('offline.save.nosize')
  const mb = Math.max(1, Math.round(status.missingBytes / 1e6))
  const floor = status.unmeasuredFiles > status.missingFiles * UNSIZED_SHARE
  const metered = status.unmetered === false
  const key = floor ? 'offline.save.atleast' : 'offline.save'
  return t(metered ? `${key}.metered` : key, { mb })
}

/** The worker is asked only once the page has what it came for: the live
 * snapshot whole, then 20 s, like the automatic precache -- and never by
 * itself on Save-Data or 2G, where the status alone sends about a hundred
 * HEAD requests to the tile host (final review of PR #5). */
export const STATUS_DELAY_MS = 20_000
/** No word from a running save for this long: ask the worker how it stands.
 * A worker killed mid-download (5 min per event, on a slow link) sends no
 * "saved" and no "failed", and the last progress stayed on screen forever. */
export const STALL_MS = 45_000

function constrained(): boolean {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } })
    .connection
  return !!connection && (connection.saveData === true || /(^|-)2g$/.test(connection.effectiveType ?? ''))
}

type Phase =
  | { kind: 'idle' }
  | { kind: 'saving'; done: number; total: number }
  | { kind: 'saved' }
  | { kind: 'failed'; failed: number }
  | { kind: 'stopped'; done: number; total: number }

/** "Save the world map for offline use (~16 MB)". ONE button from the first
 * status to the end: it shows the progress itself, so a keyboard user who
 * pressed it keeps the focus, and a status region that is always there says
 * the milestones (inserted already filled, it was often not announced). */
export function OfflineMapButton() {
  const t = useStore((s) => s.t)
  const ready = useStore((s) => s.received && s.snapshotRemaining === 0)
  const [status, setStatus] = useState<BasemapStatus | null>(null)
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const [asked, setAsked] = useState(false)
  const [manual] = useState(constrained)
  const lastWord = useRef(0)
  const askRef = useRef<() => void>(() => undefined)
  /** One status question in flight at most: each one re-sizes (HEAD) every
   * file still missing. Cleared by the answer, or after STALL_MS without one. */
  const inFlight = useRef(0)

  useEffect(() => {
    const worker = navigator.serviceWorker
    if (!worker) return
    let alive = true
    const ask = () => {
      const controller = worker.controller
      if (!controller) return
      if (inFlight.current && Date.now() - inFlight.current < STALL_MS) return
      inFlight.current = Date.now()
      setAsked(true)
      const channel = new MessageChannel()
      channel.port1.onmessage = (event) => {
        channel.port1.close() // one question, one answer
        inFlight.current = 0
        if (!alive) return
        // an error reply, or a shape we do not know: no button, no claim
        const next = isStatus(event.data) ? event.data : null
        setStatus(next)
        // a save we were showing has stopped (the worker restarted): say so
        setPhase((p) =>
          p.kind === 'saving' && next && !next.saving && !next.saved
            ? { kind: 'stopped', done: p.done, total: p.total }
            : p,
        )
      }
      controller.postMessage({ type: 'sosforge:basemap-status' }, [channel.port2])
    }
    askRef.current = ask
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; done?: number; failed?: number; total?: number } | null
      if (!alive || !data || typeof data.type !== 'string') return
      lastWord.current = Date.now()
      if (data.type === 'sosforge:basemap-progress') {
        setPhase({ kind: 'saving', done: data.done ?? 0, total: data.total ?? 0 })
      } else if (data.type === 'sosforge:basemap-saved') {
        // our save finished: said on the button; a save the worker made on
        // its own (over Wi-Fi, lane G) drops the offer instead
        setPhase((p) => (p.kind === 'saving' || p.kind === 'stopped' ? { kind: 'saved' } : p))
        setStatus((prev) => (prev ? { ...prev, saved: true, saving: false } : prev))
      } else if (data.type === 'sosforge:basemap-failed') {
        setPhase({ kind: 'failed', failed: data.failed ?? 0 })
        ask()
      }
    }
    worker.addEventListener('message', onMessage)
    return () => {
      alive = false
      worker.removeEventListener('message', onMessage)
    }
  }, [])

  // the first question waits for the live snapshot, then STATUS_DELAY_MS; a
  // worker that takes control later (first visit) is asked then
  useEffect(() => {
    const worker = navigator.serviceWorker
    if (!worker || !ready || manual) return
    const timer = window.setTimeout(() => askRef.current(), STATUS_DELAY_MS)
    const later = () => askRef.current()
    worker.addEventListener('controllerchange', later)
    return () => {
      window.clearTimeout(timer)
      worker.removeEventListener('controllerchange', later)
    }
  }, [ready, manual])

  // A save that stops talking is asked how it stands -- not on Save-Data or
  // 2G, where only what the reader asks is asked: there, pressing the button
  // while it says "Saving" is the question (recheck of PR #5).
  const saving = phase.kind === 'saving' || (phase.kind === 'idle' && !!status?.saving)
  useEffect(() => {
    if (!saving || manual) return
    const timer = window.setInterval(() => {
      if (Date.now() - lastWord.current >= STALL_MS) askRef.current()
    }, STALL_MS)
    return () => window.clearInterval(timer)
  }, [saving, manual])

  const save = () => {
    if (manual && (!asked || status === null)) {
      askRef.current()
      return
    }
    if (phase.kind === 'saved' || status?.saved) return
    if (phase.kind === 'saving' || status?.saving) {
      if (manual) askRef.current()
      return
    }
    lastWord.current = Date.now()
    setPhase({ kind: 'saving', done: 0, total: status?.missingFiles ?? 0 })
    navigator.serviceWorker?.controller?.postMessage({ type: 'sosforge:basemap-save' })
  }

  const controlled = !!navigator.serviceWorker?.controller
  // Save-Data / 2G: nothing is asked by itself; the SAME button asks first,
  // then offers the measured size -- swapped for another, it took the focus
  // with it (recheck of PR #5). Asked there and found saved, it says so
  // instead of vanishing under the reader's finger.
  // (and while that first answer is on its way: unmounted then, the button
  // dropped the focus between the press and the reply)
  const waiting = manual && (!asked || status === null)
  const savedNow = phase.kind === 'saved' || (manual && asked && !!status?.saved)
  if (waiting && !controlled) return null
  if (!waiting && !savedNow && (!status || status.saved)) return null

  const label = waiting
    ? t('offline.save.nosize')
    : phase.kind === 'saving'
      ? phase.done > 0
        ? t('offline.progress', { done: phase.done, total: phase.total })
        : t('offline.saving')
      : savedNow
        ? t('offline.saved')
        : status?.saving
          ? t('offline.saving')
          : status
            ? saveLabel(t, status)
            : ''
  const busy = !waiting && (phase.kind === 'saving' || savedNow || (phase.kind === 'idle' && !!status?.saving))
  const said =
    phase.kind === 'saving'
      ? t('offline.saving')
      : savedNow
        ? t('offline.saved')
        : phase.kind === 'failed'
          ? t('offline.failed', { failed: phase.failed })
          : phase.kind === 'stopped'
            ? t('offline.stopped', { done: phase.done, total: phase.total })
            : ''
  return (
    <>
      <span className="sr-only" role="status">
        {said}
      </span>
      {phase.kind === 'failed' || phase.kind === 'stopped' ? (
        <span className="footer-tool-note" aria-hidden="true">
          {said}
        </span>
      ) : null}
      <button type="button" className="footer-tool" aria-disabled={busy} onClick={save}>
        <span aria-hidden="true">⤓</span> {label}
      </button>
    </>
  )
}
