/** Fabriques de fixtures partagees par les tests.
 *
 * Contrairement au backend (fixtures verbatim des sources), le frontend ne
 * parle qu'au serveur SOSForge: le contrat est le type `SosEvent` normalise,
 * pas un payload externe. Une fabrique typee suffit donc ici.
 */
import type { SosEvent, Stats } from '../types'
import { useStore } from '../store'

/** Instant de reference fixe pour tous les tests: pas d'horloge reelle. */
export const NOW = Date.parse('2026-08-17T12:00:00Z')

/** Un instant `minutes` avant NOW, au format ISO attendu dans `event.time`. */
export function minutesAgo(minutes: number): string {
  return new Date(NOW - minutes * 60_000).toISOString()
}

export function makeEvent(overrides: Partial<SosEvent> = {}): SosEvent {
  return {
    id: 'emsc:test-1',
    source: 'emsc',
    source_id: 'test-1',
    kind: 'earthquake',
    time: minutesAgo(5),
    received_at: minutesAgo(5),
    updated_at: null,
    lat: 35.6,
    lon: 139.7,
    depth_km: 10,
    magnitude: 5.8,
    mag_type: 'mw',
    place: 'Off the coast of Honshu',
    region: 'Honshu',
    country: 'Japan',
    country_code: 'JP',
    severity: 'moderate',
    tsunami: false,
    alert: null,
    title: 'M5.8 Off the coast of Honshu',
    url: null,
    cluster_id: null,
    revision: 0,
    ...overrides,
  }
}

export function makeStats(overrides: Partial<Stats> = {}): Stats {
  return {
    total_buffered: 0,
    last_hour: 0,
    earthquakes_last_hour: 0,
    max_magnitude_last_hour: null,
    tsunami_active: 0,
    by_source: {},
    server_time: new Date(NOW).toISOString(),
    ...overrides,
  }
}

/** Remet le store Zustand (singleton de module) a son etat initial entre deux
 * tests. `replace: true` pour ne rien laisser trainer d'un test precedent. */
export function resetStore(): void {
  useStore.setState(useStore.getInitialState(), true)
}
