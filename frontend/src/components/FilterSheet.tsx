import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { ALL_KINDS, WINDOWS, useStore } from '../store'
import { KIND_GLYPH, SEVERITY_META, kindLabel, severityLabel } from '../format'
import type { Severity, SosEvent } from '../types'

/** The window everyone starts on. A different one is a narrowing worth a
 * chip on a phone, where the window lives in the sheet. */
const DEFAULT_WINDOW = 1440

const SEVERITY_STEPS: Severity[] = ['info', 'minor', 'moderate', 'severe', 'extreme']

/** Every focusable element of a container, in document order. */
function focusables(root: HTMLElement): HTMLElement[] {
  return [
    ...root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select, [href], [tabindex]:not([tabindex="-1"])',
    ),
  ].filter((el) => !el.closest('[hidden]'))
}

/** How many filters narrow the feed, and what each one is, for the badge and
 * the removable chips. "Active" means narrowed from the default, not merely
 * set: every kind over 24 h is the resting state, and badging it would cry
 * wolf. The window counts only where it sits inside the sheet (a phone). */
function useActive(windowInside: boolean) {
  const filters = useStore((s) => s.filters)
  const t = useStore((s) => s.t)
  return useMemo(() => {
    const chips: { key: string; label: string }[] = []
    if (filters.kinds.size < ALL_KINDS.length) {
      chips.push({
        key: 'kinds',
        label: t('filters.chip.kinds', { n: filters.kinds.size, total: ALL_KINDS.length }),
      })
    }
    if (filters.minSeverity !== 'info') {
      chips.push({
        key: 'severity',
        label: `${SEVERITY_META[filters.minSeverity].glyph} ${t('filters.chip.severity', {
          severity: severityLabel(t, filters.minSeverity),
        })}`,
      })
    }
    if (filters.minMagnitude > 0) {
      chips.push({ key: 'magnitude', label: t('filters.chip.magnitude', { m: filters.minMagnitude.toFixed(1) }) })
    }
    if (windowInside && filters.windowMinutes !== DEFAULT_WINDOW) {
      chips.push({ key: 'window', label: t(`window.${filters.windowMinutes}`) })
    }
    return chips
  }, [filters, t, windowInside])
}

/** The window switch: what separates live from history. */
export function WindowSwitch() {
  const windowMinutes = useStore((s) => s.filters.windowMinutes)
  const setWindow = useStore((s) => s.setWindow)
  const t = useStore((s) => s.t)
  return (
    <div className="segmented" role="group" aria-label={t('filters.window')}>
      {WINDOWS.map((minutes) => (
        <button
          type="button"
          key={minutes}
          aria-pressed={windowMinutes === minutes}
          onClick={() => setWindow(minutes)}
        >
          {t(`window.${minutes}`)}
        </button>
      ))}
    </div>
  )
}

/** The kind filters, folded into one button (owner, 2026-10-27: "range-les
 * dans un bouton pour qu'ils prennent moins de place").
 *
 * The button says how many filters are narrowing the feed; it opens a dialog
 * -- a popover beside the button on a wide screen, a bottom sheet on a phone
 * -- holding the thirteen kinds with their counts, the severity floor, the
 * magnitude floor and, on a phone, the time window. What is active stays
 * visible outside, as small chips that remove themselves.
 *
 * Dialog semantics, measured by the tests: `role="dialog"`, a label, focus
 * moved in and trapped, Esc and the scrim close it, focus goes back to the
 * button that opened it.
 */
export function FilterSheet({
  events,
  resultCount,
  phone,
}: {
  events: SosEvent[]
  /** how many events the current filters show, for the dialog's action */
  resultCount: number
  phone: boolean
}) {
  const filters = useStore((s) => s.filters)
  const loading = useStore((s) => s.snapshotRemaining > 0)
  const received = useStore((s) => s.received)
  const toggleKind = useStore((s) => s.toggleKind)
  const resetKinds = useStore((s) => s.resetKinds)
  const setMinSeverity = useStore((s) => s.setMinSeverity)
  const setMinMagnitude = useStore((s) => s.setMinMagnitude)
  const setWindow = useStore((s) => s.setWindow)
  const t = useStore((s) => s.t)
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  const chips = useActive(phone)
  /** Where the popover goes on a wide screen. Measured, not hung from its
   * row by CSS: on a 768 px tall laptop the dialog started at y 291 and
   * ended 100 px below the screen (tools/responsive.py). It now opens under
   * the button with the height that is left, or from the top of the screen
   * when what is left is too little to choose anything in. */
  const [placement, setPlacement] = useState<CSSProperties | undefined>(undefined)
  useLayoutEffect(() => {
    if (!open || phone || !trigger.current) {
      setPlacement(undefined)
      return
    }
    const place = () => {
      const button = trigger.current?.getBoundingClientRect()
      const row = trigger.current?.closest('.tools-row')?.getBoundingClientRect() ?? button
      if (!button || !row) return
      const gap = 8
      const below = window.innerHeight - button.bottom - gap * 2
      const top = below >= 320 ? button.bottom + gap : gap * 2
      const width = Math.min(480, row.width, window.innerWidth - 32)
      setPlacement({
        position: 'fixed',
        top,
        left: Math.max(16, Math.min(row.left, window.innerWidth - width - 16)),
        width,
        maxHeight: window.innerHeight - top - gap * 2,
      })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open, phone])

  const counts = useMemo(() => {
    const map = new Map<string, number>()
    for (const event of events) map.set(event.kind, (map.get(event.kind) ?? 0) + 1)
    return map
  }, [events])

  // Focus moves into the dialog when it opens and back to its button when it
  // closes: opening replaced the focused element, and without this a
  // keyboard or switch user was dropped on <body>.
  const wasOpen = useRef(false)
  useEffect(() => {
    if (open) {
      const first = dialog.current ? focusables(dialog.current)[0] : null
      first?.focus()
    } else if (wasOpen.current) {
      trigger.current?.focus()
    }
    wasOpen.current = open
  }, [open])

  // Esc closes from ANYWHERE while open: on the dialog element alone, it
  // died as soon as focus left it -- a click on a heading, or the "Show all"
  // button unmounting itself under the focus (review of PR #5, #24).
  useEffect(() => {
    if (!open) return
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onEscape)
    return () => document.removeEventListener('keydown', onEscape)
  }, [open])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab' || !dialog.current) return
    const items = focusables(dialog.current)
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const resetAll = () => {
    resetKinds()
    setMinSeverity('info')
    setMinMagnitude(0)
    if (phone) setWindow(DEFAULT_WINDOW)
  }

  return (
    <div className="filter-sheet">
      <button
        type="button"
        ref={trigger}
        className="btn btn-secondary filter-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">☰</span>
        {t('filters.button')}
        {chips.length > 0 ? (
          <span className="badge" aria-label={t('filters.active', { n: chips.length })}>
            {chips.length}
          </span>
        ) : null}
      </button>

      {open ? (
        <>
          <div className="filter-scrim" aria-hidden="true" onClick={() => setOpen(false)} />
          <div
            ref={dialog}
            className={`filter-dialog${phone ? ' filter-dialog-sheet' : ''}`}
            style={placement}
            role="dialog"
            aria-modal="true"
            aria-labelledby="filter-dialog-title"
            tabIndex={-1}
            onKeyDown={onKeyDown}
          >
            <div className="filter-dialog-head">
              <h2 id="filter-dialog-title" className="card-title">
                {t('filters.button')}
              </h2>
              <button
                type="button"
                className="btn btn-quiet btn-icon"
                aria-label={t('filters.close')}
                onClick={() => setOpen(false)}
              >
                ✕
              </button>
            </div>

            {phone ? (
              <section className="filter-group" aria-labelledby="filter-window">
                <h3 className="overline" id="filter-window">
                  {t('filters.window')}
                </h3>
                <WindowSwitch />
              </section>
            ) : null}

            {/* ALL covered types are shown, even at zero: on an emergency
                tracker "0 tsunami alerts" is information, not an absence of
                it. "+" while the snapshot is still arriving: a count of what
                has landed so far is a floor, not a total. */}
            <section className="filter-group" aria-labelledby="filter-kinds">
              <div className="filter-group-head">
                <h3 className="overline" id="filter-kinds">
                  {t('filters.kinds')}
                </h3>
                {filters.kinds.size < ALL_KINDS.length ? (
                  <button
                    type="button"
                    className="btn btn-quiet btn-inline"
                    onClick={() => {
                      resetKinds()
                      // this button unmounts: focus stays inside the dialog
                      dialog.current?.focus()
                    }}
                  >
                    {t('filters.all')}
                  </button>
                ) : null}
              </div>
              <div className="filter-kinds">
                {ALL_KINDS.map((kind) => {
                  const n = counts.get(kind) ?? 0
                  // "none of this kind" only once every event is in
                  const none = received && !loading && n === 0
                  return (
                    <button
                      type="button"
                      key={kind}
                      className={`chip${none ? ' empty' : ''}`}
                      aria-pressed={filters.kinds.has(kind)}
                      onClick={() => toggleKind(kind)}
                      title={none ? t('filters.none', { kind: kindLabel(t, kind) }) : undefined}
                    >
                      <span aria-hidden="true">{KIND_GLYPH[kind]}</span>
                      <span className="chip-label">{kindLabel(t, kind)}</span>
                      {/* unknown ("--") before the feed has spoken, a floor
                          ("12+") while the snapshot is still arriving */}
                      <span className="count">{!received ? '--' : `${n}${loading ? '+' : ''}`}</span>
                    </button>
                  )
                })}
              </div>
            </section>

            <section className="filter-group" aria-labelledby="filter-severity">
              <h3 className="overline" id="filter-severity">
                {t('filters.severity')}
              </h3>
              <div className="filter-severity" role="group" aria-labelledby="filter-severity">
                {SEVERITY_STEPS.map((severity) => (
                  <button
                    type="button"
                    key={severity}
                    className="chip"
                    aria-pressed={filters.minSeverity === severity}
                    onClick={() => setMinSeverity(severity)}
                  >
                    {severity === 'info' ? (
                      t('filters.severity.all')
                    ) : (
                      <span className={`sev sev-${severity}`}>
                        <span className="glyph" aria-hidden="true">
                          {SEVERITY_META[severity].glyph}
                        </span>
                        {severity === 'extreme'
                          ? severityLabel(t, severity)
                          : t('filters.severity.up', { severity: severityLabel(t, severity) })}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </section>

            <section className="filter-group">
              <label className="slider">
                {t('filters.minmag')}
                <input
                  type="range"
                  min={0}
                  max={7}
                  step={0.5}
                  value={filters.minMagnitude}
                  onChange={(e) => setMinMagnitude(Number(e.target.value))}
                />
                <output>{filters.minMagnitude.toFixed(1)}</output>
              </label>
            </section>

            <div className="filter-dialog-foot">
              <button type="button" className="btn btn-quiet" onClick={resetAll}>
                {t('filters.reset')}
              </button>
              <button type="button" className="btn btn-primary" onClick={() => setOpen(false)}>
                {/* unknown before the feed has spoken, a floor while the
                    snapshot arrives: "Show 0 events" was a claim about data
                    the page did not have (review of PR #5, #8, #9) */}
                {!received
                  ? t('filters.show.unknown')
                  : t('filters.show', { n: `${resultCount}${loading ? '+' : ''}` })}
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}

/** What narrows the feed right now, outside the dialog: one small chip per
 * filter, each removing itself. The dialog is where filters are chosen; this
 * is where the reader sees, at a glance, why the feed is shorter. */
export function ActiveFilters({ phone }: { phone: boolean }) {
  const resetKinds = useStore((s) => s.resetKinds)
  const setMinSeverity = useStore((s) => s.setMinSeverity)
  const setMinMagnitude = useStore((s) => s.setMinMagnitude)
  const setWindow = useStore((s) => s.setWindow)
  const t = useStore((s) => s.t)
  const chips = useActive(phone)
  const list = useRef<HTMLUListElement>(null)
  /** After a chip removes itself, where the focus goes: the chip now at
   * its place, the one before, or -- the last one gone -- the Filters
   * button. Removed with the focus on it, the chip dropped a keyboard user
   * on <body> (review of PR #5, #25). */
  const refocus = useRef<number | null>(null)
  useEffect(() => {
    const index = refocus.current
    if (index === null) return
    refocus.current = null
    const buttons = list.current ? [...list.current.querySelectorAll<HTMLButtonElement>('button')] : []
    const target = buttons[Math.min(index, buttons.length - 1)]
    if (target) target.focus()
    else document.querySelector<HTMLButtonElement>('.filter-trigger')?.focus()
  })
  if (chips.length === 0) return null
  const remove = (key: string, index: number) => {
    refocus.current = index
    if (key === 'kinds') resetKinds()
    else if (key === 'severity') setMinSeverity('info')
    else if (key === 'magnitude') setMinMagnitude(0)
    else if (key === 'window') setWindow(DEFAULT_WINDOW)
  }
  return (
    <ul ref={list} className="filter-chips" aria-label={t('filters.active.list')}>
      {chips.map((chip, index) => (
        <li key={chip.key}>
          <button
            type="button"
            className="filter-chip"
            aria-label={t('filters.remove', { filter: chip.label })}
            onClick={() => remove(chip.key, index)}
          >
            <span>{chip.label}</span>
            <span aria-hidden="true">✕</span>
          </button>
        </li>
      ))}
    </ul>
  )
}
