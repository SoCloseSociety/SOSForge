import type { Kind, Severity, SosEvent } from './types'

export type T = (key: string, vars?: Record<string, string | number>) => string

/** Severity: a FILL colour, a glyph and (from i18n) a word. All three travel
 * together on display: the colour alone never carries the information.
 *
 * Each rank has a glyph of a DIFFERENT SHAPE -- circle, diamond, triangle,
 * square, letter -- so a reader who cannot tell the hues apart (or a phone in
 * full sun) still tells the ranks apart. Moderate and severe used to be two
 * triangles (`▲` and `⚠`), which separated nothing once the colour was gone.
 * `●` and `▲` are also the alert and attention marks of the Sentinel House
 * pills, so the two products read the same way.
 *
 * `color` is the FILL: a map marker, a legend swatch. The map is dark in both
 * themes, so one fill per rank is enough (3:1 non-text, on the dark basemap).
 *
 * The same rank rendered as WORDS lives in styles.css (`--sev-*-text`), once
 * per theme: same hue and saturation as the fill, only the lightness moves,
 * until the worst surface of that theme clears 4.5:1. The ratios are pinned
 * by `__tests__/contrast.test.ts`, which reads the stylesheet itself. A text
 * colour hard-coded here could not follow the theme, which is why there is
 * none any more.
 */
export const SEVERITY_META: Record<Severity, { glyph: string; color: string }> = {
  info: { glyph: 'i', color: '#6f7379' },
  minor: { glyph: '■', color: '#0ca30c' },
  moderate: { glyph: '▲', color: '#fab219' },
  severe: { glyph: '◆', color: '#ec835a' },
  extreme: { glyph: '●', color: '#d03b3b' },
}

/** Map series that are NOT severities: the wave fronts and the forecast
 * tracks. They used to be drawn in the severe orange, which made a series look
 * like a rank -- an S-wave ring around a minor quake read as a severe alert.
 * Violet is the Sentinel House series colour (`--violet`), never a state. */
export const SERIES = {
  waveP: '#9ec5f4',
  waveS: '#7c8cff',
  track: '#7c8cff',
} as const

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
  landslide: '⛰️',
  avalanche: '🏔️',
  space_weather: '🛰️',
  other: '⚠️',
}

/** Display name of every backend source (`Source.name`). English, like the
 * rest of the interface's source data: this table used to mix French labels
 * ("USGS volcans", "Cendres (SIGMET)", "OMM") into a Japanese or English
 * page, and nine sources added later had no entry at all, so the footer
 * printed their raw identifiers ("phivolcs", "jma-tsunami", "geonet-volcano"). */
export const SOURCE_LABEL: Record<string, string> = {
  emsc: 'EMSC (push)',
  usgs: 'USGS',
  tsunami: 'NOAA tsunami',
  gdacs: 'GDACS',
  nws: 'NWS',
  volcano: 'USGS volcanoes',
  jma: 'JMA',
  bmkg: 'BMKG',
  geonet: 'GeoNet',
  ingv: 'INGV',
  geofon: 'GEOFON',
  eonet: 'NASA EONET',
  meteoalarm: 'Meteoalarm',
  wmo: 'WMO',
  nhc: 'NHC',
  ash: 'Ash SIGMET',
  afad: 'AFAD',
  jma_eew: 'JMA EEW',
  cenc: 'CENC',
  swarm: 'Swarm detection',
  aftershock: 'USGS aftershock',
  space: 'NOAA SWPC',
  'jma-tsunami': 'JMA tsunami',
  ssn: 'SSN (Mexico)',
  nrcan: 'NRCan',
  noa: 'NOA (Greece)',
  phivolcs: 'PHIVOLCS',
  igp: 'IGP (Peru)',
  'geonet-volcano': 'GeoNet volcanoes',
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

  // A negative age means the event has not started yet: a weather warning is
  // published BEFORE its onset, and that lead time is the whole point of it.
  // Showing "just now" for something starting in two hours threw away the one
  // piece of anticipation those feeds give us.
  if (seconds < -60) {
    const ahead = -seconds
    if (ahead < 3600) return t('age.in.min', { n: Math.round(ahead / 60) })
    if (ahead < 86400) return t('age.in.h', { n: Math.round(ahead / 3600) })
    return t('age.in.d', { n: Math.round(ahead / 86400) })
  }

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
