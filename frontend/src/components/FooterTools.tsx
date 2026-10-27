import { Suspense, lazy, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../store'

/* Two tools in the footer's credit line, where they cost the first screen
 * nothing: on a phone the live feed keeps the fold (tools/responsive.py),
 * on a wide screen the footer is always in view.
 *
 *  - the climate indicators (lane C2's panel), in a dialog, loaded only
 *    when opened: the panel and its CSS stay out of the first paint, and
 *    /api/climate is not polled for a reader who never looks;
 *  - "Save the world map for offline use", on the service worker's message
 *    contract (lane G, public/sw.js), shown only while there is something
 *    to save. */

const ClimatePanel = lazy(() => import('./ClimatePanel').then((m) => ({ default: m.ClimatePanel })))

function focusables(root: HTMLElement): HTMLElement[] {
  return [
    ...root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  ]
}

/** The climate indicators, behind one button. */
export function ClimateButton() {
  const t = useStore((s) => s.t)
  const [open, setOpen] = useState(false)
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
        className="footer-tool"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <span aria-hidden="true">◔</span> {t('climate.title')}
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
                aria-label={t('climate.title')}
                tabIndex={-1}
                onKeyDown={onKeyDown}
              >
                <div className="climate-dialog-head">
                  <button
                    type="button"
                    className="btn btn-quiet btn-icon"
                    aria-label={t('climate.close')}
                    onClick={() => setOpen(false)}
                  >
                    ✕
                  </button>
                </div>
                <Suspense fallback={<p className="climate-dialog-wait">{t('climate.loading')}</p>}>
                  <ClimatePanel />
                </Suspense>
              </div>
            </>,
            document.body,
          )
        : null}
    </>
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

/** The button's label: the size the worker measured, "about" because the
 * files it could not size (a few glyph ranges) are not in the sum; no size
 * at all when it could size none (offline). Mobile data is said. */
export function saveLabel(t: (key: string, vars?: Record<string, string | number>) => string, status: BasemapStatus): string {
  if (status.missingBytes <= 0 || status.unmeasuredFiles >= status.missingFiles) return t('offline.save.nosize')
  const mb = Math.max(1, Math.round(status.missingBytes / 1e6))
  return t(status.unmetered === false ? 'offline.save.metered' : 'offline.save', { mb })
}

/** "Save the world map for offline use (~16 MB)". */
export function OfflineMapButton() {
  const t = useStore((s) => s.t)
  const [status, setStatus] = useState<BasemapStatus | null>(null)
  const [progress, setProgress] = useState<{ done: number; failed: number; total: number } | null>(null)
  const [outcome, setOutcome] = useState<{ saved: boolean; failed: number } | null>(null)

  useEffect(() => {
    const worker = navigator.serviceWorker
    if (!worker) return
    let alive = true
    const ask = () => {
      const controller = worker.controller
      if (!controller) return
      const channel = new MessageChannel()
      channel.port1.onmessage = (event) => {
        channel.port1.close() // one question, one answer
        // an error reply, or a shape we do not know: no button, no claim
        if (alive) setStatus(isStatus(event.data) ? event.data : null)
      }
      controller.postMessage({ type: 'sosforge:basemap-status' }, [channel.port2])
    }
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; done?: number; failed?: number; total?: number } | null
      if (!alive || !data || typeof data.type !== 'string') return
      if (data.type === 'sosforge:basemap-progress') {
        setProgress({ done: data.done ?? 0, failed: data.failed ?? 0, total: data.total ?? 0 })
      } else if (data.type === 'sosforge:basemap-saved') {
        setProgress(null)
        setOutcome({ saved: true, failed: 0 })
        ask()
      } else if (data.type === 'sosforge:basemap-failed') {
        setProgress(null)
        setOutcome({ saved: false, failed: data.failed ?? 0 })
        ask()
      }
    }
    ask()
    worker.addEventListener('message', onMessage)
    // a first visit starts uncontrolled: the worker claims the page later
    worker.addEventListener('controllerchange', ask)
    return () => {
      alive = false
      worker.removeEventListener('message', onMessage)
      worker.removeEventListener('controllerchange', ask)
    }
  }, [])

  const save = () => {
    setOutcome(null)
    navigator.serviceWorker?.controller?.postMessage({ type: 'sosforge:basemap-save' })
  }

  if (outcome?.saved) {
    return (
      <span className="footer-tool-note" role="status">
        {t('offline.saved')}
      </span>
    )
  }
  if (progress) {
    return (
      <span className="footer-tool-note" role="status">
        {t('offline.progress', { done: progress.done, total: progress.total })}
      </span>
    )
  }
  if (!status || status.saved) return null
  if (status.saving) {
    return (
      <span className="footer-tool-note" role="status">
        {t('offline.saving')}
      </span>
    )
  }
  return (
    <>
      {outcome && !outcome.saved ? (
        <span className="footer-tool-note" role="status">
          {t('offline.failed', { failed: outcome.failed })}
        </span>
      ) : null}
      <button type="button" className="footer-tool" onClick={save}>
        <span aria-hidden="true">⤓</span> {saveLabel(t, status)}
      </button>
    </>
  )
}
