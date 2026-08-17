export type Kind =
  | 'earthquake'
  | 'tsunami'
  | 'volcano'
  | 'cyclone'
  | 'flood'
  | 'wildfire'
  | 'drought'
  | 'storm'
  | 'heat'
  | 'other'

export type Severity = 'info' | 'minor' | 'moderate' | 'severe' | 'extreme'

export interface SosEvent {
  id: string
  source: string
  source_id: string
  kind: Kind
  time: string
  received_at: string
  updated_at: string | null
  lat: number | null
  lon: number | null
  depth_km: number | null
  magnitude: number | null
  mag_type: string | null
  place: string
  region: string | null
  country: string | null
  country_code: string | null
  severity: Severity
  tsunami: boolean
  alert: string | null
  title: string
  url: string | null
  cluster_id: string | null
  revision: number
}

export interface Stats {
  total_buffered: number
  last_hour: number
  earthquakes_last_hour: number
  max_magnitude_last_hour: number | null
  tsunami_active: number
  by_source: Record<string, number>
  server_time: string
}

export interface SourceHealth {
  name: string
  connected: boolean
  last_ok: string | null
  last_error: string | null
  events_seen: number
  /** ce qui est reellement entre dans le store (events_seen compte les lectures) */
  ingested?: number
  errors: number
}

export type ServerMessage =
  | {
      type: 'snapshot'
      server_time: string
      events: SosEvent[]
      stats: Stats
      sources: SourceHealth[]
    }
  | {
      type: 'event' | 'update'
      event: SosEvent
      primary: boolean
      /** l'evenement vient reellement de se produire (et non: vient d'arriver
       * dans le buffer). Seul ce cas merite halo, clignotement et son. */
      breaking: boolean
    }
  | {
      type: 'tick'
      server_time: string
      stats: Stats
      sources: SourceHealth[]
      clients: number
    }
