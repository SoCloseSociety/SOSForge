import { useEffect, useState } from 'react'
import { eventUrl } from '../deeplink'
import { useStore } from '../store'
import {
  SEVERITY_META,
  SOURCE_LABEL,
  countryName,
  flagEmoji,
  formatAge,
  kindLabel,
  severityLabel,
} from '../format'
import type { SosEvent } from '../types'

interface NearbyLink {
  id: string
  label: string
  detail: string
  url: string
}

interface Camera {
  id: string
  title: string
  city: string | null
  country: string | null
  status: string | null
  thumbnail: string | null
  url: string | null
}

interface Nearby {
  found: boolean
  links: NearbyLink[]
  cameras: Camera[]
  cameras_configured?: boolean
}

/** Fiche de l'evenement selectionne + acces aux vues en direct de la zone.
 *
 * Les liens sont calcules cote serveur et marchent sans aucune cle. Les webcams
 * ne remontent que si une cle Windy est configuree: l'absence de cle ne doit pas
 * donner l'impression que la zone n'a rien a montrer, d'ou le message explicite.
 */
export function LivePanel({ event, now }: { event: SosEvent; now: number }) {
  const t = useStore((s) => s.t)
  const lang = useStore((s) => s.lang)
  const select = useStore((s) => s.select)
  const [nearby, setNearby] = useState<Nearby | null>(null)
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (event.lat === null || event.lon === null) {
      setNearby(null)
      return
    }
    const controller = new AbortController()
    setLoading(true)
    fetch(`/api/events/${encodeURIComponent(event.id)}/nearby`, { signal: controller.signal })
      .then((r) => r.json())
      .then((data: Nearby) => setNearby(data))
      .catch(() => setNearby(null))
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [event.id, event.lat, event.lon])

  const severity = SEVERITY_META[event.severity]

  return (
    <aside className="live-panel">
      <header>
        <div>
          <h3>
            <span className="flag" aria-hidden="true">
              {flagEmoji(event.country_code) ?? '🌐'}
            </span>{' '}
            {event.place || event.title}
          </h3>
          <p className="live-meta">
            <span style={{ color: severity.color }}>
              <span aria-hidden="true">{severity.glyph}</span> {severityLabel(t, event.severity)}
            </span>
            {' · '}
            {kindLabel(t, event.kind)}
            {' · '}
            {countryName(lang, event.country_code) ? `${countryName(lang, event.country_code)} · ` : ''}
            {formatAge(t, (now - Date.parse(event.time)) / 1000)}
          </p>
        </div>
        <span className="live-actions">
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard?.writeText(eventUrl(event.id))
              setCopied(true)
              window.setTimeout(() => setCopied(false), 2000)
            }}
            title={t('detail.share')}
            aria-label={t('detail.share')}
          >
            {copied ? '✓' : '🔗'}
          </button>
          <button type="button" onClick={() => select(null)} aria-label={t('detail.close')}>
            ✕
          </button>
        </span>
      </header>

      <dl className="live-facts">
        {event.magnitude !== null ? (
          <>
            <dt>{t('detail.magnitude')}</dt>
            <dd>
              {event.magnitude} {event.mag_type ?? ''}
            </dd>
          </>
        ) : null}
        {event.depth_km !== null ? (
          <>
            <dt>{t('detail.depth')}</dt>
            <dd>{Math.round(event.depth_km)} km</dd>
          </>
        ) : null}
        <dt>{t('detail.time')}</dt>
        <dd>{event.time.slice(11, 19)}</dd>
        <dt>{t('detail.source')}</dt>
        <dd>{SOURCE_LABEL[event.source] ?? event.source}</dd>
      </dl>

      {event.url ? (
        <a className="live-official" href={event.url} target="_blank" rel="noreferrer">
          {t('detail.official')} ↗
        </a>
      ) : null}

      <h4>{t('live.title')}</h4>
      {event.lat === null || event.lon === null ? (
        <p className="live-note">{t('live.nocoords')}</p>
      ) : loading && !nearby ? (
        <p className="live-note">{t('live.loading')}</p>
      ) : (
        <>
          <ul className="live-links">
            {(nearby?.links ?? []).map((link) => (
              <li key={link.id}>
                <a href={link.url} target="_blank" rel="noreferrer">
                  <strong>{link.label}</strong>
                  <span>{link.detail}</span>
                </a>
              </li>
            ))}
          </ul>

          <h4>{t('live.cameras')}</h4>
          {nearby?.cameras?.length ? (
            <ul className="live-cams">
              {nearby.cameras.map((cam) => (
                <li key={cam.id}>
                  <a href={cam.url ?? '#'} target="_blank" rel="noreferrer">
                    {cam.thumbnail ? <img src={cam.thumbnail} alt="" loading="lazy" /> : null}
                    <span>{cam.title}</span>
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="live-note">
              {nearby?.cameras_configured ? t('live.nocamera') : t('live.nokey')}
            </p>
          )}
        </>
      )}
    </aside>
  )
}
