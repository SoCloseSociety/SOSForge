export type Kind =
  | "earthquake"
  | "tsunami"
  | "volcano"
  | "cyclone"
  | "flood"
  | "wildfire"
  | "drought"
  | "storm"
  | "heat"
  | "landslide"
  | "avalanche"
  | "space_weather"
  | "other";

export type Severity = "info" | "minor" | "moderate" | "severe" | "extreme";

export interface SosEvent {
  id: string;
  source: string;
  source_id: string;
  kind: Kind;
  time: string;
  received_at: string;
  updated_at: string | null;
  lat: number | null;
  lon: number | null;
  depth_km: number | null;
  magnitude: number | null;
  mag_type: string | null;
  place: string;
  region: string | null;
  country: string | null;
  country_code: string | null;
  severity: Severity;
  /** declared ONGOING by its source (active fire, live storm, running warning).
   * It stays relevant while it runs, unlike a past earthquake. */
  ongoing: boolean;
  /** an automatic solution, not yet reviewed by a human. The first automatic
   * magnitude of a large quake is routinely off by up to a full unit and
   * relocated by tens of km in the minutes that follow -- every agency labels
   * it, and so must we. */
  preliminary: boolean;
  /** shaking, as opposed to size: the quantity that answers "how strong was it
   * HERE", which magnitude does not. */
  intensity_mmi: number | null;
  /** how many people reported feeling it. Null is NOT zero: most of the planet
   * has no reporters. */
  felt_reports: number | null;
  /** What the issuing agency told people to DO. The actionable core of an
   * alert, and the reason CAP has the field at all. Attacker-influenced text
   * from feeds we do not control: render it through a text node, NEVER through
   * dangerouslySetInnerHTML. Nothing strips `<` on the way in, because real
   * alerts say "temperatures < 32F". */
  instruction: string | null;
  /** The agency's own prose description of the hazard. */
  description: string | null;
  /** CAP responseType, a closed vocabulary: Shelter, Evacuate, Avoid, Monitor,
   * Prepare, Execute. One word, present on ~100% of NWS and Meteoalarm alerts,
   * which is what makes it the one new field dense enough for the list. */
  response_type: string | null;
  /** What people REPORTED feeling, as opposed to intensity_mmi which is what
   * the model estimates. Never average the two: they measure different things. */
  intensity_cdi: number | null;
  /** Tsunami: when the first wave reaches a named site, and the largest height
   * a gauge has actually measured. The height is the only measurement in the
   * set, and the one that deserves to be loud. */
  wave_eta: string | null;
  wave_eta_site: string | null;
  wave_max_m: number | null;
  wave_max_site: string | null;
  /** when the source states an end (NWS and the CAP feeds do). The server
   * purges on it; the UI can show the remaining time. */
  expires: string | null;
  tsunami: boolean;
  alert: string | null;
  title: string;
  url: string | null;
  /** forecast positions, when the source publishes them (NHC cyclone tracks) */
  forecast_track:
    | {
        tau?: number;
        valid?: string;
        lat?: number;
        lon?: number;
        wind_kt?: number;
        category?: number;
      }[]
    | null;
  cluster_id: string | null;
  revision: number;
}

export interface Stats {
  total_buffered: number;
  last_hour: number;
  earthquakes_last_hour: number;
  max_magnitude_last_hour: number | null;
  tsunami_active: number;
  by_source: Record<string, number>;
  server_time: string;
}

export interface SourceHealth {
  name: string;
  connected: boolean;
  last_ok: string | null;
  last_error: string | null;
  events_seen: number;
  /** what actually made it into the store (events_seen counts reads) */
  ingested?: number;
  errors: number;
}

export type ServerMessage =
  | {
      type: "snapshot";
      server_time: string;
      events: SosEvent[];
      stats: Stats;
      sources: SourceHealth[];
    }
  | {
      type: "event" | "update";
      event: SosEvent;
      primary: boolean;
      /** the event actually just happened (as opposed to: just arrived in
       * the buffer). Only this case deserves a halo, flash, and sound. */
      breaking: boolean;
    }
  | {
      /** the server removed events: a lifted warning, a cancelled early
       * warning, an alert whose source went silent. Without this message the
       * protocol could only add and update, so a tab left open since the
       * morning kept a dissipated cyclone on screen indefinitely -- dead data
       * shown as live, which is the one direction of the freshness rule that
       * is easy to miss. */
      type: "purge";
      ids: string[];
      reason: "stale" | "cancelled" | "lifted";
    }
  | {
      type: "tick";
      server_time: string;
      stats: Stats;
      sources: SourceHealth[];
      clients: number;
    };
