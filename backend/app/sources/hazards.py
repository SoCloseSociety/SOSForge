"""Aleas non sismiques a forte valeur: cyclones tropicaux et cendres volcaniques.

GDACS voit les cyclones, mais grossierement et avec du retard. Le NHC publie la
position, les vents et la categorie de chaque tempete active a chaque advisory.
Et pour les cendres volcaniques, les VAAC ne publient que du texte heterogene:
les SIGMET de l'aviation en sont la traduction operationnelle structuree, et
c'est le seul flux mondial machine-lisible qui existe pour cet alea.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.models.event import Event, Kind, Severity
from app.sources.regional import JsonPollSource

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- NHC


# Echelle de Saffir-Simpson, en noeuds. Une tempete tropicale n'est pas un
# ouragan majeur: la gravite doit suivre le vent, pas le fait qu'elle soit nommee.
def cyclone_severity(wind_kt: float | None, classification: str) -> Severity:
    if classification in ("HU", "MH", "TY", "STY"):
        if wind_kt is not None and wind_kt >= 96:  # categorie 3+
            return Severity.EXTREME
        return Severity.SEVERE
    if classification in ("TS", "STS"):
        return Severity.MODERATE
    return Severity.MINOR


class NhcSource(JsonPollSource):
    """National Hurricane Center: bassins Atlantique, Pacifique Est et Central."""

    name = "nhc"
    kind = "poll"
    url = "https://www.nhc.noaa.gov/CurrentStorms.json"

    def parse_payload(self, data) -> list[Event]:
        events: list[Event] = []
        for storm in (data or {}).get("activeStorms") or []:
            storm_id = storm.get("id")
            if not storm_id:
                continue

            # tout est en string dans ce flux, y compris les nombres
            def number(value) -> float | None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            wind_kt = number(storm.get("intensity"))
            pressure = number(storm.get("pressure"))
            classification = (storm.get("classification") or "").upper()

            # latitude "20.4N" est un string: les champs *Numeric sont les bons
            lat = storm.get("latitudeNumeric")
            lon = storm.get("longitudeNumeric")

            try:
                time = datetime.fromisoformat(
                    (storm.get("lastUpdate") or "").replace("Z", "+00:00")
                ).astimezone(UTC)
            except (TypeError, ValueError):
                continue

            name = storm.get("name") or "sans nom"
            advisory = (storm.get("publicAdvisory") or {}).get("url")

            events.append(
                Event(
                    # `id` est stable toute la saison; `binNumber` (CP2) est recycle
                    # d'une tempete a l'autre et ne doit surtout pas servir de cle
                    id=f"nhc:{storm_id}",
                    source="nhc",
                    source_id=str(storm_id),
                    kind=Kind.CYCLONE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    magnitude=wind_kt,
                    mag_type="kt",
                    place=name,
                    severity=cyclone_severity(wind_kt, classification),
                    alert=classification.lower() or None,
                    title=f"{classification} {name} -- {wind_kt or '?'} kt",
                    url=advisory or "https://www.nhc.noaa.gov/",
                    raw={
                        "classification": classification,
                        "pressure_mb": pressure,
                        "movement_dir": storm.get("movementDir"),
                        "movement_speed_kt": storm.get("movementSpeed"),
                        "basin": storm.get("binNumber"),
                    },
                )
            )
        return events


# ------------------------------------------------------------------ cendres (VA)


class AshSource(JsonPollSource):
    """SIGMET internationaux de cendres volcaniques (Aviation Weather Center).

    Un SIGMET n'a pas d'identifiant d'evenement: il est reemis toutes les six
    heures avec un nouveau numero de serie. La cle est donc composite
    (FIR + serie + debut de validite), sinon chaque reemission creerait un
    doublon sur la carte.
    """

    name = "ash"
    kind = "poll"
    url = "https://aviationweather.gov/api/data/isigmet?format=json&hazard=VA"

    def parse_payload(self, data) -> list[Event]:
        events: list[Event] = []
        for sigmet in data or []:
            fir = sigmet.get("firId") or sigmet.get("icaoId")
            series = sigmet.get("seriesId") or "0"
            valid_from = sigmet.get("validTimeFrom")
            if not fir or valid_from is None:
                continue

            try:
                # ce flux melange epoch SECONDES (validTime*) et ISO Z (receiptTime)
                time = datetime.fromtimestamp(float(valid_from), tz=UTC)
            except (TypeError, ValueError, OSError):
                continue

            # le polygone donne l'emprise du nuage; on pose le point en son centre
            coords = sigmet.get("coords") or []
            lat = lon = None
            if coords:
                lats = [c.get("lat") for c in coords if c.get("lat") is not None]
                lons = [c.get("lon") for c in coords if c.get("lon") is not None]
                if lats and lons:
                    lat = sum(lats) / len(lats)
                    lon = sum(lons) / len(lons)

            volcano = sigmet.get("qualifier") or "volcan non nomme"
            top_ft = sigmet.get("top")
            # un panache qui monte haut est un panache dangereux
            severity = Severity.SEVERE if (top_ft or 0) >= 25000 else Severity.MODERATE

            events.append(
                Event(
                    id=f"ash:{fir}:{series}:{int(float(valid_from))}",
                    source="ash",
                    source_id=f"{fir}-{series}",
                    kind=Kind.VOLCANO,
                    time=time,
                    lat=lat,
                    lon=lon,
                    place=volcano.title(),
                    severity=severity,
                    alert="cendres",
                    title=f"Cendres volcaniques -- {volcano.title()}",
                    url="https://aviationweather.gov/gfa/#sigmet",
                    raw={
                        "fir": sigmet.get("firName"),
                        # `top` est en pieds, pas en metres
                        "top_ft": top_ft,
                        "base_ft": sigmet.get("base"),
                        "direction": sigmet.get("dir"),
                        "speed_kt": sigmet.get("spd"),
                    },
                )
            )
        return events
