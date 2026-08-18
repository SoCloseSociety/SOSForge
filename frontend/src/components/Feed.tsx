import { useStore } from '../store'
import {
  SEVERITY_META,
  SOURCE_LABEL,
  badge,
  countryName,
  flagEmoji,
  formatAge,
  kindLabel,
  severityLabel,
} from '../format'
import type { SosEvent } from '../types'

interface Props {
  events: SosEvent[]
  /** current server timestamp, refreshed every second */
  now: number
  emptyKey: string
}

function Row({ event, now }: { event: SosEvent; now: number }) {
  const selected = useStore((s) => s.selected === event.id)
  const fresh = useStore((s) => s.fresh.has(event.id))
  const select = useStore((s) => s.select)
  const t = useStore((s) => s.t)
  const lang = useStore((s) => s.lang)
  const severity = SEVERITY_META[event.severity]
  const flag = flagEmoji(event.country_code)
  const chip = badge(event)
  const age = (now - Date.parse(event.time)) / 1000

  return (
    <button
      type="button"
      className={`item${fresh ? ' fresh' : ''}`}
      aria-selected={selected}
      onClick={() => select(selected ? null : event.id)}
    >
      <span className="mag" style={{ color: severity.color }}>
        {chip.value}
        {chip.unit ? <small>{chip.unit}</small> : null}
      </span>

      <span>
        <span className="place">
          <span className="flag" title={countryName(lang, event.country_code) ?? ''}>
            {flag ?? '🌐'}
          </span>
          {event.place || event.title}
        </span>
        <span className="meta">
          <span className={`sev sev-${event.severity}`}>
            <span className="glyph" aria-hidden="true">
              {severity.glyph}
            </span>
            {severityLabel(t, event.severity)}
          </span>
          <span className="tag">{kindLabel(t, event.kind)}</span>
          <span>{SOURCE_LABEL[event.source] ?? event.source}</span>
          {event.revision > 0 ? <span className="tag">{t('map.revised')}</span> : null}
        </span>
      </span>

      <span className="age">{formatAge(t, age)}</span>
    </button>
  )
}

export function Feed({ events, now, emptyKey }: Props) {
  const t = useStore((s) => s.t)
  if (events.length === 0) {
    return <div className="feed-empty">{t(emptyKey)}</div>
  }
  return (
    // aria-live="polite": a new event is announced without interrupting
    // ongoing reading. "assertive" would be unbearable at 20 events/minute.
    <div className="feed" role="feed" aria-live="polite" aria-busy={false}>
      {events.map((event) => (
        <Row key={event.id} event={event} now={now} />
      ))}
    </div>
  )
}
