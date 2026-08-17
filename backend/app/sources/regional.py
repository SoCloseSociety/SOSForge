"""Agences sismiques regionales.

Pourquoi ces quatre-la et pas les sept trouvees: l'EMSC relaie deja les solutions
de la plupart des agences nationales (on voit passer `"auth": "BMKG"` dans ses
propres frames). Une source regionale ne merite donc sa place que si elle apporte
quelque chose que l'EMSC n'a pas:

- **JMA**: l'intensite japonaise (shindo, echelle 0 a 7) mesure ce qui a ete
  *ressenti au sol*, la ou la magnitude mesure l'energie liberee. C'est
  l'information qui compte au Japon, et elle n'existe nulle part ailleurs.
- **BMKG**: le drapeau officiel de potentiel tsunami pour l'Indonesie, premier
  pays expose au monde. Il arrive avant les bulletins du PTWC.
- **INGV** et **GeoNet**: seuil de detection local tres bas (M1 et moins) sur deux
  zones tres actives, et une redondance utile le jour ou l'EMSC tombe.

Ecartes volontairement, avec leurs endpoints verifies, dans le README: AFAD
(Turquie), IGN (Espagne), SSN (Mexique). Redondants avec l'EMSC, et payes au prix
d'un parsing fragile (fuseau local implicite, magnitude a extraire d'une phrase).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, severity_from_magnitude, to_utc
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"


class JsonPollSource(Source):
    """Socle commun: on poll une URL JSON, on normalise, on emet."""

    url: str = ""

    def __init__(self, poll_seconds: float = 60.0, url: str | None = None):
        super().__init__()
        self.poll_seconds = poll_seconds
        if url:
            self.url = url

    def parse_payload(self, data: Any) -> list[Event]:  # pragma: no cover - abstrait
        raise NotImplementedError

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    events = self.parse_payload(resp.json())
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)


# --------------------------------------------------------------------------- JMA

# ISO 6709 tel que le publie la JMA: "+32.5+130.6-10000/" = lat, lon, puis la
# profondeur en METRES et en negatif (sous le niveau de la mer).
RE_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?/?")

# Le shindo n'est pas lineaire et n'est pas une magnitude: a partir de 5-, les
# degats aux batiments commencent; 6+ et 7 sont destructeurs.
SHINDO_SEVERITY = {
    "5-": Severity.SEVERE,
    "5+": Severity.SEVERE,
    "6-": Severity.EXTREME,
    "6+": Severity.EXTREME,
    "7": Severity.EXTREME,
}

# 震度速報 est une alerte d'intensite emise AVANT localisation: pas d'epicentre,
# donc rien a poser sur une carte.
JMA_SKIPPED_TITLES = {"震度速報"}


def parse_iso6709(value: str | None) -> tuple[float | None, float | None, float | None]:
    if not value:
        return None, None, None
    match = RE_ISO6709.match(value.strip())
    if not match:
        return None, None, None
    lat = float(match.group(1))
    lon = float(match.group(2))
    depth_m = match.group(3)
    depth_km = abs(float(depth_m)) / 1000.0 if depth_m is not None else None
    return lat, lon, depth_km


class JmaSource(JsonPollSource):
    name = "jma"
    kind = "poll"
    url = "https://www.jma.go.jp/bosai/quake/data/list.json"

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for row in data or []:
            if row.get("ttl") in JMA_SKIPPED_TITLES:
                continue
            eid = row.get("eid")
            lat, lon, depth = parse_iso6709(row.get("cod"))
            if not eid or lat is None:
                continue

            try:
                magnitude = float(row["mag"]) if row.get("mag") not in (None, "") else None
            except (TypeError, ValueError):
                magnitude = None

            # `at` porte deja son decalage (+09:00): on ne devine aucun fuseau
            time = to_utc(row.get("at") or row.get("rdt"))
            if time is None:
                continue

            place = row.get("en_anm") or row.get("anm") or "Japon"
            shindo = row.get("maxi") or None
            severity = severity_from_magnitude(magnitude)
            if shindo in SHINDO_SEVERITY:
                # l'intensite ressentie prime sur la magnitude quand elle est forte
                severity = max(
                    severity,
                    SHINDO_SEVERITY[shindo],
                    key=lambda s: list(Severity).index(s),
                )

            events.append(
                Event(
                    id=f"jma:{eid}",
                    source="jma",
                    source_id=str(eid),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=depth,
                    magnitude=magnitude,
                    mag_type="Mj",
                    place=place,
                    country="Japan",
                    severity=severity,
                    alert=f"shindo {shindo}" if shindo else None,
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url="https://www.jma.go.jp/bosai/map.html#contents=earthquake_map",
                    raw={"shindo_max": shindo, "report": row.get("ttl")},
                )
            )
        return events


# -------------------------------------------------------------------------- BMKG


class BmkgSource(JsonPollSource):
    name = "bmkg"
    kind = "poll"
    url = "https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json"

    def parse_payload(self, data: Any) -> list[Event]:
        rows = ((data or {}).get("Infogempa") or {}).get("gempa") or []
        events: list[Event] = []
        for row in rows:
            time = to_utc(row.get("DateTime"))
            if time is None:
                continue

            lat = lon = None
            coords = (row.get("Coordinates") or "").split(",")
            if len(coords) == 2:
                try:
                    lat, lon = float(coords[0]), float(coords[1])
                except ValueError:
                    lat = lon = None

            try:
                magnitude = float(row.get("Magnitude"))
            except (TypeError, ValueError):
                magnitude = None

            depth = None
            depth_match = re.search(r"([\d.]+)", row.get("Kedalaman") or "")
            if depth_match:
                depth = float(depth_match.group(1))

            # "Tidak berpotensi tsunami" = aucun potentiel. Toute autre formulation
            # ("Berpotensi tsunami...") est une alerte, et elle arrive avant le PTWC.
            potential = (row.get("Potensi") or "").strip()
            tsunami = bool(potential) and "tidak berpotensi" not in potential.lower()

            place = row.get("Wilayah") or "Indonesie"
            events.append(
                Event(
                    id=f"bmkg:{row.get('DateTime')}",
                    source="bmkg",
                    source_id=str(row.get("DateTime")),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=depth,
                    magnitude=magnitude,
                    mag_type="M",
                    place=place,
                    country="Indonesia",
                    severity=severity_from_magnitude(magnitude, tsunami),
                    tsunami=tsunami,
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url="https://www.bmkg.go.id/gempabumi/gempabumi-terkini.bmkg",
                    raw={"potensi": potential, "dirasakan": row.get("Dirasakan")},
                )
            )
        return events


# ------------------------------------------------------------------------ GeoNet


class GeonetSource(JsonPollSource):
    name = "geonet"
    kind = "poll"
    # MMI=3: en dessous, ce sont des micro-seismes que personne ne ressent
    url = "https://api.geonet.org.nz/quake?MMI=3"

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for feature in (data or {}).get("features") or []:
            props = feature.get("properties") or {}
            public_id = props.get("publicID")
            if not public_id:
                continue
            time = to_utc(props.get("time"))
            if time is None:
                continue

            # attention: GeoNet ne met que [lon, lat] dans la geometrie, la
            # profondeur est une propriete a part
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None

            magnitude = props.get("magnitude")
            place = props.get("locality") or "Nouvelle-Zelande"
            events.append(
                Event(
                    id=f"geonet:{public_id}",
                    source="geonet",
                    source_id=str(public_id),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=props.get("depth"),
                    magnitude=round(magnitude, 1) if isinstance(magnitude, (int, float)) else None,
                    mag_type="M",
                    place=place,
                    country="New Zealand",
                    severity=severity_from_magnitude(magnitude),
                    alert=f"MMI {props.get('mmi')}" if props.get("mmi") is not None else None,
                    title=f"M {magnitude:.1f} -- {place}" if magnitude else place,
                    url=f"https://www.geonet.org.nz/earthquake/{public_id}",
                    raw={"mmi": props.get("mmi"), "quality": props.get("quality")},
                )
            )
        return events


# -------------------------------------------------------------------------- INGV


class IngvSource(JsonPollSource):
    name = "ingv"
    kind = "poll"
    # FDSN standard: exactement le meme contrat que l'USGS, a la casse des
    # champs pres. C'est la source la moins chere a maintenir du lot.
    url = "https://webservices.ingv.it/fdsnws/event/1/query?format=geojson&limit=200&orderby=time"

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for feature in (data or {}).get("features") or []:
            props = feature.get("properties") or {}
            event_id = props.get("eventId")
            if event_id is None:
                continue

            # l'INGV publie en UTC mais SANS suffixe de fuseau: on le pose nous-memes
            # l'INGV publie en UTC mais SANS suffixe de fuseau
            time = to_utc(props.get("time"))
            if time is None:
                continue

            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None
            depth = coords[2] if len(coords) > 2 else None

            magnitude = props.get("mag")
            place = props.get("place") or "Italie"
            events.append(
                Event(
                    id=f"ingv:{event_id}",
                    source="ingv",
                    source_id=str(event_id),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=depth,
                    magnitude=magnitude,
                    mag_type=props.get("magType"),
                    place=place,
                    country="Italy",
                    severity=severity_from_magnitude(magnitude),
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url=f"https://terremoti.ingv.it/event/{event_id}",
                    raw={"author": props.get("author"), "type": props.get("type")},
                )
            )
        return events


# -------------------------------------------------------------------------- AFAD


class AfadSource(JsonPollSource):
    """AFAD (Turquie).

    Deux pieges verifies sur l'API reelle:

    1. `date` est en **UTC**, pas en heure de Turquie. Prouve par recoupement:
       le seisme AFAD de 17:30:37 correspond au meme evenement EMSC horodate
       17:30:38 UTC. Un decalage de 3 h aurait saute aux yeux.
    2. `limit` tronque **avant** le tri: `orderby=timedesc&limit=3` ne rend pas
       les 3 plus recents mais les 3 plus VIEUX de la fenetre, ensuite tries.
       On demande donc une fenetre courte avec une limite large, et on trie ici.
    """

    name = "afad"
    kind = "poll"
    base_url = "https://deprem.afad.gov.tr/apiv2/event/filter"

    def __init__(self, poll_seconds: float = 60.0, window_hours: float = 12.0):
        super().__init__(poll_seconds)
        self.window_hours = window_hours

    def build_url(self) -> str:
        from datetime import timedelta
        from urllib.parse import urlencode

        now = datetime.now(UTC)
        params = {
            "start": (now - timedelta(hours=self.window_hours)).strftime("%Y-%m-%d %H:%M:%S"),
            "end": now.strftime("%Y-%m-%d %H:%M:%S"),
            "orderby": "timedesc",
            "limit": "500",
        }
        return f"{self.base_url}?{urlencode(params)}"

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        # follow_redirects est indispensable: l'API repond 302 (BigFIP) avant JSON
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.build_url())
                    resp.raise_for_status()
                    events = self.parse_payload(resp.json())
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for row in data or []:
            event_id = row.get("eventID")
            if not event_id:
                continue

            def number(value) -> float | None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            # tous les nombres arrivent en string
            lat, lon = number(row.get("latitude")), number(row.get("longitude"))
            magnitude = number(row.get("magnitude"))
            if lat is None or lon is None:
                continue

            # naif mais UTC (prouve par recoupement EMSC)
            time = to_utc(row.get("date"))
            if time is None:
                continue

            place = row.get("location") or "Turquie"
            events.append(
                Event(
                    id=f"afad:{event_id}",
                    source="afad",
                    source_id=str(event_id),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=number(row.get("depth")),
                    magnitude=magnitude,
                    mag_type=row.get("type"),
                    place=place,
                    country=row.get("country") or "Türkiye",
                    severity=severity_from_magnitude(magnitude),
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url=f"https://deprem.afad.gov.tr/event-detail/{event_id}",
                    raw={
                        "province": row.get("province"),
                        "district": row.get("district"),
                        "is_update": row.get("isEventUpdate"),
                    },
                )
            )
        # le tri par fraicheur nous appartient, l'API ne le garantit pas
        events.sort(key=lambda e: e.time, reverse=True)
        return events


# ------------------------------------------------------------------------ GEOFON


class GeofonSource(JsonPollSource):
    """GEOFON (GFZ Potsdam) -- troisieme catalogue mondial, a cote de l'EMSC et
    de l'USGS.

    Son interet n'est pas la couverture (les trois se recoupent) mais le **vote**:
    avec trois solutions independantes, le dedup inter-sources confirme un
    evenement au lieu de le supposer, et un desaccord de magnitude devient
    visible au lieu d'etre invisible.

    Le service FDSN de GEOFON refuse `format=json` (400): il ne parle que `text`
    et QuakeML. On passe donc par son service eqinfo, qui rend du GeoJSON de la
    meme famille que l'USGS.
    """

    name = "geofon"
    kind = "poll"
    url = "https://geofon.gfz.de/eqinfo/list.php?fmt=geojson&nmax=100"

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for feature in (data or {}).get("features") or []:
            props = feature.get("properties") or {}
            event_id = feature.get("id")
            if not event_id:
                continue

            # horodatage sans suffixe de fuseau, comme l'INGV
            time = to_utc(props.get("time"))
            if time is None:
                continue

            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None
            depth = coords[2] if len(coords) > 2 else None

            magnitude = props.get("mag")
            place = props.get("place") or "region inconnue"
            events.append(
                Event(
                    id=f"geofon:{event_id}",
                    source="geofon",
                    source_id=str(event_id),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=depth,
                    magnitude=magnitude,
                    mag_type=props.get("magType"),
                    place=place,
                    severity=severity_from_magnitude(magnitude),
                    # "C:confirmed" vs "A:automatic": une solution revue par un
                    # analyste ne vaut pas une detection automatique
                    alert=(props.get("status") or "").split(":")[-1] or None,
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url=props.get("url"),
                    raw={"status": props.get("status"), "has_moment_tensor": props.get("hasMT")},
                )
            )
        return events
