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

from app.models.event import Event, Kind, Severity, to_utc
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

            time = to_utc(storm.get("lastUpdate"))
            if time is None:
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
                    # CurrentStorms ne liste QUE les tempetes actives
                    ongoing=True,
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


# ------------------------------------------------------------------------- EONET


def centroid(coords) -> tuple[float | None, float | None]:
    """Barycentre d'une geometrie GeoJSON de profondeur quelconque."""
    points: list[tuple[float, float]] = []

    def walk(node) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) == 2
            and all(isinstance(v, (int, float)) for v in node)
        ):
            points.append((float(node[0]), float(node[1])))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(coords)
    if not points:
        return None, None
    return (
        sum(p[1] for p in points) / len(points),
        sum(p[0] for p in points) / len(points),
    )


# EONET classe par categorie; on retombe sur nos propres types d'alea.
EONET_KIND = {
    "wildfires": Kind.WILDFIRE,
    "severeStorms": Kind.CYCLONE,
    "volcanoes": Kind.VOLCANO,
    "floods": Kind.FLOOD,
    "drought": Kind.DROUGHT,
    "earthquakes": Kind.EARTHQUAKE,
    "landslides": Kind.OTHER,
    "seaLakeIce": Kind.OTHER,
    "snow": Kind.STORM,
    "dustHaze": Kind.OTHER,
    "manmade": Kind.OTHER,
    "waterColor": Kind.OTHER,
    "temperatureExtremes": Kind.HEAT,
}


class EonetSource(JsonPollSource):
    """NASA EONET -- evenements naturels en cours, observes depuis l'espace.

    Ce qu'il apporte que rien d'autre n'a ici: les **feux de forets** suivis
    comme des evenements (avec un identifiant stable et une trajectoire), la ou
    FIRMS ne donne que des pixels chauds a clusteriser soi-meme et GDACS ne voit
    que les plus gros. C'est aussi un second avis mondial sur les tempetes.

    Latence: EONET agrege des sources qui vont de la minute a quelques heures.
    Ce n'est donc pas du "direct" au sens de l'EMSC, et le drapeau `breaking` du
    pipeline s'en charge tout seul en regardant l'age reel de l'evenement.
    """

    name = "eonet"
    kind = "poll"
    # `days=14`: EONET garde des evenements "open" tres longtemps (un feu non
    # clos reste ouvert des semaines apres sa derniere observation). Sans cette
    # borne, 250 feux dont beaucoup dorment depuis un mois noyaient la carte.
    url = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=14&limit=200"

    def parse_payload(self, data) -> list[Event]:
        events: list[Event] = []
        for row in (data or {}).get("events") or []:
            event_id = row.get("id")
            geometry = row.get("geometry") or []
            if not event_id or not geometry:
                continue

            # `geometry` est une TRAJECTOIRE: la derniere entree est la position
            # courante. Prendre la premiere afficherait un cyclone la ou il etait
            # il y a trois jours.
            last = geometry[-1]
            time = to_utc(last.get("date"))
            if time is None:
                continue

            coords = last.get("coordinates") or []
            lat = lon = None
            if last.get("type") == "Point" and len(coords) >= 2:
                lon, lat = coords[0], coords[1]
            elif coords:
                # emprise (Polygon): on pose le point en son centre
                lat, lon = centroid(coords)

            categories = row.get("categories") or []
            category = (categories[0] or {}).get("id") if categories else None
            kind = EONET_KIND.get(category or "", Kind.OTHER)

            magnitude = last.get("magnitudeValue")
            unit = last.get("magnitudeUnit")
            severity = Severity.MODERATE
            if kind is Kind.CYCLONE and isinstance(magnitude, (int, float)):
                severity = cyclone_severity(float(magnitude), "HU" if magnitude >= 64 else "TS")

            title = row.get("title") or "evenement EONET"
            events.append(
                Event(
                    id=f"eonet:{event_id}",
                    source="eonet",
                    source_id=str(event_id),
                    kind=kind,
                    time=time,
                    lat=lat,
                    lon=lon,
                    magnitude=float(magnitude) if isinstance(magnitude, (int, float)) else None,
                    mag_type=unit,
                    place=title,
                    severity=severity,
                    # on interroge EONET avec status=open: par definition en cours
                    ongoing=True,
                    title=title,
                    url=row.get("link"),
                    raw={
                        "category": category,
                        "sources": [s.get("id") for s in row.get("sources") or []],
                        "track_points": len(geometry),
                    },
                )
            )
        return events
