"""Configuration SOSForge, pilotee par l'environnement (prefixe SOS_)."""

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

    # --- stockage ---
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
    # agences regionales: elles apportent ce que l'EMSC n'a pas (shindo japonais,
    # potentiel tsunami BMKG, seuil de detection local tres bas)
    enable_jma: bool = True
    enable_bmkg: bool = True
    enable_geonet: bool = True
    enable_ingv: bool = True
    enable_afad: bool = True
    # aleas non sismiques a forte valeur: cyclones NHC, cendres volcaniques
    enable_nhc: bool = True
    enable_ash: bool = True
    enable_geofon: bool = True
    enable_eonet: bool = True
    # alertes officielles hors USA
    enable_meteoalarm: bool = True
    enable_wmo: bool = True

    emsc_ws_url: str = "wss://www.seismicportal.eu/standing_order/websocket"
    usgs_feed_url: str = (
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
    )
    usgs_poll_seconds: float = 5.0
    tsunami_poll_seconds: float = 30.0
    gdacs_poll_seconds: float = 120.0
    # au dela, un evenement GDACS vert (typiquement un feu) n'est plus une info
    # du moment et ne fait qu'encombrer la carte. Orange et rouge passent toujours.
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
    # Meteoalarm: 1 vert, 2 jaune, 3 orange, 4 rouge. En dessous d'orange, c'est
    # du bulletin meteo, et il y en a plus de 2000 par cycle sur dix pays.
    meteoalarm_min_level: int = 3
    # OMM: rang CAP, 1 = Extreme, 2 = Severe. Reserve mesuree sur le flux reel:
    # l'echelle n'est PAS homogene d'un pays a l'autre (des "Small Craft
    # Advisory" americains arrivent en rang 1, quand l'Inde tague en rang 2 des
    # pluies de routine). On s'en tient donc au tiers superieur declare par
    # chaque pays -- 221 alertes sur 2258 -- plutot que de croire l'echelle.
    wmo_max_severity_rank: int = 1

    # backfill au demarrage: on ne veut pas d'une carte vide
    backfill_url: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"

    # --- vues live a proximite (optionnel) ---
    # sans cle, l'API ne rend que des liens profonds (Windy, YouTube, Worldview,
    # Google Maps) qui marchent partout; avec cle, la vraie liste des webcams.
    windy_api_key: str = ""
    nearby_radius_km: int = 100

    # --- diffusion ---
    heartbeat_seconds: float = 1.0
    # en deca de cet age, un evenement est annonce comme "en direct" cote UI
    # (halo, clignotement, son). Au dela, il est ajoute silencieusement.
    breaking_seconds: float = 900.0
    min_magnitude: float = 0.0
    # horizon d'ingestion: au dela, un evenement n'est plus une info du moment.
    # La liste JMA remonte a plus de neuf mois, GDACS garde ses alertes des
    # semaines. Les gravites severe et extreme ne sont jamais coupees.
    max_event_age_days: float = 3.0
    # une alerte "en cours" qu'aucune source ne mentionne plus depuis ce delai
    # est consideree terminee. Le cycle de polling le plus lent est de 300 s,
    # donc 6 h laissent une marge tres large avant de conclure au silence.
    # tolerance d'avance d'horloge avant de considerer un horodatage comme faux
    future_tolerance_seconds: float = 120.0
    stale_after_hours: float = 6.0
    sweep_seconds: float = 300.0

    # --- dedup inter-sources ---
    dedupe_window_seconds: float = 90.0
    dedupe_radius_km: float = 250.0
    dedupe_mag_delta: float = 1.2

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
