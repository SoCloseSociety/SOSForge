"""SOSForge configuration, driven by the environment (SOS_ prefix)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "SOSForge"
    host: str = "0.0.0.0"
    port: int = 8300
    log_level: str = "INFO"

    cors_origins: str = "http://localhost:5273,http://127.0.0.1:5273"

    # --- storage ---
    data_dir: Path = Path("./data")
    ring_size: int = 5000
    snapshot_size: int = 300
    persist_jsonl: bool = True

    # --- sources ---
    enable_emsc_ws: bool = True
    enable_usgs: bool = True
    enable_tsunami: bool = True
    enable_gdacs: bool = True
    enable_nws: bool = True
    enable_volcano: bool = True
    # regional agencies: they bring what EMSC does not have (Japanese shindo,
    # BMKG tsunami potential, very low local detection threshold)
    enable_jma: bool = True
    enable_bmkg: bool = True
    enable_geonet: bool = True
    enable_ingv: bool = True
    enable_afad: bool = True
    # high-value non-seismic hazards: NHC cyclones, volcanic ash
    enable_nhc: bool = True
    enable_ash: bool = True
    enable_geofon: bool = True
    enable_eonet: bool = True
    # official alerts outside the USA
    enable_meteoalarm: bool = True
    enable_wmo: bool = True
    # Unofficial third-party relay (Wolfx): enriches, is authoritative on
    # nothing. The Japanese early warning is the only information in this
    # product that can still be used to take cover.
    enable_jma_eew: bool = True
    enable_cenc: bool = True

    emsc_ws_url: str = "wss://www.seismicportal.eu/standing_order/websocket"
    usgs_feed_url: str = (
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
    )
    usgs_poll_seconds: float = 5.0
    tsunami_poll_seconds: float = 30.0
    gdacs_poll_seconds: float = 120.0
    # beyond this, a green GDACS event (typically a fire) is no longer current
    # news and only clutters the map. Orange and red always pass.
    gdacs_max_age_days: float = 1.0
    nws_poll_seconds: float = 20.0
    volcano_poll_seconds: float = 300.0
    jma_poll_seconds: float = 45.0
    bmkg_poll_seconds: float = 60.0
    geonet_poll_seconds: float = 60.0
    ingv_poll_seconds: float = 60.0
    afad_poll_seconds: float = 60.0
    nhc_poll_seconds: float = 300.0
    ash_poll_seconds: float = 180.0
    geofon_poll_seconds: float = 60.0
    eonet_poll_seconds: float = 600.0
    meteoalarm_poll_seconds: float = 300.0
    wmo_poll_seconds: float = 300.0
    # an EEW is measured in seconds; stay reasonable with a third-party service
    jma_eew_poll_seconds: float = 5.0
    cenc_poll_seconds: float = 120.0
    # Meteoalarm: 1 green, 2 yellow, 3 orange, 4 red. Below orange it is
    # weather-bulletin material, and there are over 2000 per cycle across ten
    # countries.
    meteoalarm_min_level: int = 3
    # WMO: CAP rank, 1 = Extreme, 2 = Severe. Caveat measured on the real
    # feed: the scale is NOT homogeneous from one country to another (US
    # "Small Craft Advisory" alerts arrive at rank 1, while India tags routine
    # rain at rank 2). So we stick to the top tier as declared by each country
    # -- 221 alerts out of 2258 -- rather than trusting the scale.
    wmo_max_severity_rank: int = 1

    # backfill at startup: we do not want an empty map
    backfill_url: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"

    # --- nearby live views (optional) ---
    # without a key, the API only returns deep links (Windy, YouTube,
    # Worldview, Google Maps) that work everywhere; with a key, the real
    # webcam list.
    windy_api_key: str = ""
    nearby_radius_km: int = 100

    # --- broadcasting ---
    heartbeat_seconds: float = 1.0
    # below this age, an event is announced as "breaking" on the UI side
    # (halo, blinking, sound). Beyond it, it is added silently.
    breaking_seconds: float = 900.0
    min_magnitude: float = 0.0
    # ingestion horizon: beyond it, an event is no longer current news. The
    # JMA list goes back more than nine months, GDACS keeps its alerts for
    # weeks. Severe and extreme severities are never cut off.
    max_event_age_days: float = 3.0
    # an "ongoing" alert that no source has mentioned for this long is
    # considered over. The slowest polling cycle is 300 s, so 6 h leave a very
    # wide margin before concluding silence.
    # clock-lead tolerance before declaring a timestamp wrong
    future_tolerance_seconds: float = 120.0
    stale_after_hours: float = 6.0
    sweep_seconds: float = 300.0
    # journal retention: about 5 MB per day, on a disk shared with the other
    # products of the suite
    journal_keep_days: int = 7

    # --- cross-source dedup ---
    dedupe_window_seconds: float = 90.0
    dedupe_radius_km: float = 250.0
    dedupe_mag_delta: float = 1.2

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
