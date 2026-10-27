import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useStore } from '../store'
import { SOURCE_LABEL, formatAge } from '../format'
import { degradedFeeds, failedFeeds, sourceLabel } from '../sourceHealth'
import { useMediaQuery } from '../useMediaQuery'
import type { SourceHealth } from '../types'

type SourceState = 'up' | 'degraded' | 'down'

function stateOf(source: SourceHealth): SourceState {
  if (!source.connected) return 'down'
  return degradedFeeds(source) !== null ? 'degraded' : 'up'
}

/** Each state has its own SHAPE: a dot online, a triangle degraded, a cross
 * offline. Two coloured dots measured 1.43:1 against each other, which said
 * nothing to a deuteranopic reader. */
const MARK: Record<SourceState, string> = { up: '●', degraded: '▲', down: '✕' }

function SourceButton({
  source,
  selected,
  onSelect,
}: {
  source: SourceHealth
  selected: boolean
  onSelect: (name: string) => void
}) {
  const t = useStore((s) => s.t)
  const state = stateOf(source)
  return (
    <button
      type="button"
      className={`source source-${state}`}
      data-source={source.name}
      aria-pressed={selected}
      aria-label={`${sourceLabel(source, SOURCE_LABEL)}, ${t(`footer.source.${state}`)}`}
      onClick={() => onSelect(source.name)}
    >
      <span className="source-mark" aria-hidden="true">
        {MARK[state]}
      </span>
      <span className="source-name">{sourceLabel(source, SOURCE_LABEL)}</span>
      <span className="count" aria-hidden="true">
        {source.ingested ?? source.events_seen}
      </span>
    </button>
  )
}

/** The footer's sources, in one row (owner, 2026-10-27: "met-les dans un
 * slider").
 *
 * The product rule is that the footer shows every source's real state
 * PERMANENTLY. A slider hides most of its items by construction, so a
 * source that is down or degraded is never put in it: it is pinned first,
 * outside the scroll, always on screen. The healthy ones scroll -- by touch,
 * mouse drag, the wheel, the keyboard (arrows, Home, End) or the two buttons.
 * No auto-scroll: a row that moves by itself is read by nobody and costs the
 * reader who was about to tap it.
 *
 * The detail of a source -- last poll, last event, its error, its failed
 * feeds -- is long secondary text, behind a tap on it ("Show more" rule).
 */
export function SourcesBar({ now }: { now: number }) {
  const sources = useStore((s) => s.sources)
  const clients = useStore((s) => s.clients)
  const received = useStore((s) => s.received)
  const connected = useStore((s) => s.connected)
  const t = useStore((s) => s.t)
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const [selected, setSelected] = useState<string | null>(null)
  const [edges, setEdges] = useState({ start: true, end: true })
  const strip = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; left: number; moved: boolean; id: number } | null>(null)

  const { pinned, healthy, up } = useMemo(() => {
    const pinnedList = sources.filter((s) => stateOf(s) !== 'up')
    return {
      pinned: pinnedList,
      healthy: sources.filter((s) => stateOf(s) === 'up'),
      up: sources.filter((s) => s.connected).length,
    }
  }, [sources])

  const detail = selected ? sources.find((s) => s.name === selected) ?? null : null

  const measure = useCallback(() => {
    const el = strip.current
    if (!el) return
    setEdges({
      start: el.scrollLeft <= 1,
      end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 1,
    })
  }, [])

  useEffect(() => {
    measure()
    const el = strip.current
    if (!el) return
    // A vertical wheel over a horizontal strip scrolls it sideways, but only
    // while it can still move: at either end the page gets its wheel back.
    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
      const atStart = el.scrollLeft <= 0 && event.deltaY < 0
      const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth && event.deltaY > 0
      if (atStart || atEnd) return
      event.preventDefault()
      el.scrollLeft += event.deltaY
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    window.addEventListener('resize', measure)
    return () => {
      el.removeEventListener('wheel', onWheel)
      window.removeEventListener('resize', measure)
    }
  }, [measure, healthy.length])

  const scrollByPage = (direction: 1 | -1) => {
    const el = strip.current
    if (!el) return
    el.scrollBy({ left: direction * el.clientWidth * 0.8, behavior: reduceMotion ? 'auto' : 'smooth' })
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const el = strip.current
    if (!el) return
    const step = { ArrowRight: 120, ArrowLeft: -120 }[event.key]
    if (step) {
      event.preventDefault()
      el.scrollBy({ left: step, behavior: reduceMotion ? 'auto' : 'smooth' })
    } else if (event.key === 'Home') {
      event.preventDefault()
      el.scrollLeft = 0
    } else if (event.key === 'End') {
      event.preventDefault()
      el.scrollLeft = el.scrollWidth
    }
  }

  // Mouse drag. Touch already scrolls natively, and a pen too: only a mouse
  // needs this. A drag of more than 4 px is not a click on the source under it.
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== 'mouse' || event.button !== 0 || !strip.current) return
    drag.current = { x: event.clientX, left: strip.current.scrollLeft, moved: false, id: event.pointerId }
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    const el = strip.current
    if (!d || !el) return
    // the button was released somewhere else (no pointerup reached the
    // strip): the drag is over, and a hovering mouse must not scroll the row
    // or swallow the next click (review of PR #5, #26)
    if (event.buttons === 0) {
      drag.current = null
      return
    }
    const dx = event.clientX - d.x
    if (!d.moved && Math.abs(dx) > 4) {
      d.moved = true
      el.setPointerCapture?.(d.id)
    }
    if (d.moved) el.scrollLeft = d.left - dx
  }
  const onPointerUp = () => {
    const d = drag.current
    window.setTimeout(() => {
      if (drag.current === d) drag.current = null
    }, 0)
  }

  const select = (name: string) => {
    if (drag.current?.moved) return
    setSelected((current) => (current === name ? null : name))
  }

  return (
    <div className="sources-bar">
      <div className="sources-row">
        <p className="sources-summary">
          <span
            className={`source-mark ${!received || !connected ? '' : up === sources.length ? 'all-up' : 'some-down'}`}
            aria-hidden="true"
          >
            {!received || !connected ? '○' : up === sources.length ? '●' : '▲'}
          </span>
          {/* before the feed has spoken, the counts are unknown, not zero;
              link down, they are the LAST KNOWN ones and say so (a mint
              "19/19 online" from a dead link vouched for it: review, #12) */}
          {!received
            ? t('footer.summary.waiting')
            : !connected
              ? t('footer.summary.offline', { up, total: sources.length })
              : clients === null
              ? t('footer.summary.noclients', { up, total: sources.length })
              : t('footer.summary', { up, total: sources.length, n: clients })}
        </p>

        {pinned.length > 0 ? (
          <div className="sources-pinned" role="group" aria-label={t('footer.pinned')}>
            {pinned.map((source) => (
              <SourceButton
                key={source.name}
                source={source}
                selected={selected === source.name}
                onSelect={select}
              />
            ))}
          </div>
        ) : null}

        <div className="sources-slider">
          <button
            type="button"
            className="btn btn-quiet btn-icon sources-step"
            aria-label={t('footer.prev')}
            disabled={edges.start}
            onClick={() => scrollByPage(-1)}
          >
            ‹
          </button>
          <div
            ref={strip}
            className="sources-strip"
            role="group"
            aria-label={t('footer.sources.title')}
            tabIndex={0}
            onScroll={measure}
            onKeyDown={onKeyDown}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onLostPointerCapture={onPointerUp}
          >
            {healthy.map((source) => (
              <SourceButton
                key={source.name}
                source={source}
                selected={selected === source.name}
                onSelect={select}
              />
            ))}
          </div>
          <button
            type="button"
            className="btn btn-quiet btn-icon sources-step"
            aria-label={t('footer.next')}
            disabled={edges.end}
            onClick={() => scrollByPage(1)}
          >
            ›
          </button>
        </div>
      </div>

      {detail ? (
        <SourceDetail
          source={detail}
          now={now}
          onClose={() => {
            // focus goes back to the source the detail belongs to, BEFORE the
            // Hide button unmounts: it dropped a keyboard user on <body>
            // (review of PR #5, #25)
            strip.current
              ?.closest('.sources-bar')
              ?.querySelector<HTMLButtonElement>(`.source[data-source="${detail.name}"]`)
              ?.focus()
            setSelected(null)
          }}
        />
      ) : null}
    </div>
  )
}

/** One source, in full: what it last did and, when it fails, why. */
function SourceDetail({ source, now, onClose }: { source: SourceHealth; now: number; onClose: () => void }) {
  const t = useStore((s) => s.t)
  const state = stateOf(source)
  const failed = failedFeeds(source)
  const age = (iso: string | null | undefined) =>
    iso ? formatAge(t, (now - Date.parse(iso)) / 1000) : t('footer.never')
  return (
    <div className="source-detail" role="region" aria-label={sourceLabel(source, SOURCE_LABEL)}>
      <p className="source-detail-head">
        <strong>{sourceLabel(source, SOURCE_LABEL)}</strong>
        <span className={`source-${state}`}>
          <span aria-hidden="true">{MARK[state]}</span> {t(`footer.source.${state}`)}
        </span>
        <button type="button" className="btn btn-quiet btn-inline" onClick={onClose}>
          {t('more.hide')}
        </button>
      </p>
      <dl>
        <dt>{t('footer.lastpoll')}</dt>
        <dd>{age(source.last_ok)}</dd>
        {/* Two different facts, never one: the last NEW event (first seen,
            never handed over before) and the newest event's own time. A
            "last event 40 s ago" under a source whose newest event is 29 h
            old was a poll re-listing old news (lane Q, defect 7). When a
            poll last returned anything is not an event time: not shown. */}
        {source.last_event_at !== undefined ? (
          <>
            <dt>{t('footer.lastnew.label')}</dt>
            <dd>{source.last_event_at ? age(source.last_event_at) : t('footer.lastnew.none')}</dd>
          </>
        ) : null}
        {source.newest_event_time !== undefined ? (
          <>
            <dt>{t('footer.newest.label')}</dt>
            <dd>{source.newest_event_time ? age(source.newest_event_time) : t('footer.newest.none')}</dd>
          </>
        ) : null}
        <dt>{t('footer.seen')}</dt>
        <dd>
          {source.ingested ?? source.events_seen} / {source.events_seen}
        </dd>
        {failed && failed.length > 0 ? (
          <>
            <dt>{t('footer.failed.label')}</dt>
            <dd>
              <ul className="source-failed">
                {failed.map((f) => (
                  <li key={f.feed}>
                    {f.feed}
                    {f.error ? (
                      <>
                        {' '}
                        <code>{f.error}</code>
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        ) : null}
        {source.last_error ? (
          <>
            <dt>{t('footer.error')}</dt>
            <dd>
              <code>{source.last_error}</code>
            </dd>
          </>
        ) : null}
      </dl>
    </div>
  )
}
