import type { Kind, Severity, SosEvent } from './types'

export type T = (key: string, vars?: Record<string, string | number>) => string

/** Severity: color from the status palette + glyph. The LABEL comes from
 * i18n -- all three always travel together on display, the color alone must
 * never carry the information. */
export const SEVERITY_META: Record<Severity, { glyph: string; color: string }> = {
  info: { glyph: 'i', color: '#6f7379' },
  minor: { glyph: '▪', color: '#0ca30c' },
  moderate: { glyph: '▲', color: '#fab219' },
  severe: { glyph: '⚠', color: '#ec835a' },
  extreme: { glyph: '⛔', color: '#d03b3b' },
}

export const KIND_GLYPH: Record<Kind, string> = {
  earthquake: '🌍',
  tsunami: '🌊',
  volcano: '🌋',
  cyclone: '🌀',
  flood: '💧',
  wildfire: '🔥',
  storm: '⛈️',
  heat: '🌡️',
  drought: '🏜️',
  other: '⚠️',
}

export const SOURCE_LABEL: Record<string, string> = {
  emsc: 'EMSC (push)',
  usgs: 'USGS',
  tsunami: 'NOAA tsunami',
  gdacs: 'GDACS',
  nws: 'NWS',
  volcano: 'USGS volcans',
  jma: 'JMA',
  bmkg: 'BMKG',
  geonet: 'GeoNet',
  ingv: 'INGV',
  geofon: 'GEOFON',
  eonet: 'NASA EONET',
  meteoalarm: 'Meteoalarm',
  wmo: 'OMM',
  nhc: 'NHC',
  ash: 'Cendres (SIGMET)',
  afad: 'AFAD',
  jma_eew: 'JMA alerte precoce',
  cenc: 'CENC',
}

/** The flag is computed from the ISO2 code (two Unicode regional indicator
 * symbols): no image to load, nothing to store. No code = no flag, never an
 * approximate flag -- the high seas belong to no one. */
export function flagEmoji(iso2: string | null): string | null {
  // two NON-alphabetic characters ('12', '??') fell outside the regional
  // indicator range and displayed a stray character -- whereas the product
  // rule is: never an approximate flag
  if (!iso2 || !/^[A-Za-z]{2}$/.test(iso2)) return null
  return String.fromCodePoint(
    ...[...iso2.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65),
  )
}

/** Country name in the user's language, for free, via the browser's Intl:
 * it's already translated in the product's five languages. */
export function countryName(lang: string, iso2: string | null): string | null {
  if (!iso2) return null
  try {
    return new Intl.DisplayNames([lang], { type: 'region' }).of(iso2) ?? iso2
  } catch {
    return iso2
  }
}

export function severityLabel(t: T, severity: Severity): string {
  return t(`sev.${severity}`)
}

export function kindLabel(t: T, kind: Kind): string {
  return t(`kind.${kind}`)
}

export function formatAge(t: T, seconds: number): string {
  if (!Number.isFinite(seconds)) return ''
  if (seconds < 5) return t('age.now')
  if (seconds < 60) return t('age.s', { n: Math.floor(seconds) })
  if (seconds < 3600) return t('age.min', { n: Math.floor(seconds / 60) })
  if (seconds < 86400) return t('age.h', { n: Math.floor(seconds / 3600) })
  return t('age.d', { n: Math.floor(seconds / 86400) })
}

export function formatClock(date: Date): string {
  return date.toISOString().slice(11, 19)
}

/** What's shown in the left-hand badge: a magnitude if the event has one,
 * otherwise the hazard type's pictogram. */
export function badge(event: SosEvent): { value: string; unit: string } {
  if (event.magnitude !== null) {
    return { value: event.magnitude.toFixed(1), unit: event.mag_type?.toUpperCase() ?? 'MAG' }
  }
  return { value: KIND_GLYPH[event.kind], unit: '' }
}
