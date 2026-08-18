"""Regional seismic agencies.

Why these four and not the seven found: EMSC already relays the solutions of
most national agencies (we see `"auth": "BMKG"` go by in its own frames). A
regional source therefore only earns its place if it brings something EMSC
does not have:

- **JMA**: the Japanese intensity (shindo, scale 0 to 7) measures what was
  *felt on the ground*, where magnitude measures the energy released. It is
  the information that matters in Japan, and it exists nowhere else.
- **BMKG**: the official tsunami-potential flag for Indonesia, the most
  exposed country in the world. It arrives before the PTWC bulletins.
- **INGV** and **GeoNet**: very low local detection threshold (M1 and below)
  over two very active areas, and useful redundancy the day EMSC goes down.

Deliberately set aside, with their verified endpoints, in the README: AFAD
(Turkey), IGN (Spain), SSN (Mexico). Redundant with EMSC, and paid for with
fragile parsing (implicit local timezone, magnitude to extract from a
sentence).
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
    """Common base: poll a JSON URL, normalize, emit."""

    url: str = ""

    def __init__(self, poll_seconds: float = 60.0, url: str | None = None):
        super().__init__()
        self.poll_seconds = poll_seconds
        if url:
            self.url = url

    def parse_payload(self, data: Any) -> list[Event]:  # pragma: no cover - abstract
        raise NotImplementedError

    def build_url(self) -> str:
        """URL to query this cycle. Overridden by sources whose URL depends
        on the current moment (AFAD wants a sliding time window)."""
        return self.url

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        # follow_redirects: AFAD answers 302 before its JSON
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


# --------------------------------------------------------------------------- JMA

# ISO 6709 as JMA publishes it: "+32.5+130.6-10000/" = lat, lon, then the
# depth in METERS and negative (below sea level).
RE_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?/?")

# Shindo is not linear and is not a magnitude: from 5- on, building damage
# begins; 6+ and 7 are destructive.
SHINDO_SEVERITY = {
    "5-": Severity.SEVERE,
    "5+": Severity.SEVERE,
    "6-": Severity.EXTREME,
    "6+": Severity.EXTREME,
    "7": Severity.EXTREME,
}

# 震度速報 is an intensity alert issued BEFORE localization: no epicenter,
# so nothing to place on a map.
JMA_SKIPPED_TITLES = {"震度速報"}

# 遠地地震に関する情報 = information about a DISTANT earthquake. JMA relays
# them (an Indonesian M7.7, an M7.1 in Central America), and sticking
# country="Japan" on them made quakes on the other side of the world carry
# the Japanese flag -- which was the case in production.
JMA_DISTANT_TITLE = "遠地地震に関する情報"


def parse_iso6709(value: str | None) -> tuple[float | None, float | None, float | None]:
    if not value:
        return None, None, None
    match = RE_ISO6709.match(value.strip())
    if not match:
        return None, None, None
    lat = float(match.group(1))
    lon = float(match.group(2))

    # JMA also emits ISO 6709 in degrees-MINUTES ("+3237.5+13040.7" =
    # 32 deg 37.5 min N). Interpreted as decimal degrees, that gives
    # lat=3237.5: off the globe, and nothing downstream bounded the value. A
    # wrong position is far worse than a rejection, so we reject.
    if abs(lat) > 90 or abs(lon) > 180:
        return None, None, None

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

            # `at` already carries its offset (+09:00): no timezone guessing
            time = to_utc(row.get("at") or row.get("rdt"))
            if time is None:
                continue

            place = row.get("en_anm") or row.get("anm") or "Japan"
            distant = row.get("ttl") == JMA_DISTANT_TITLE
            shindo = row.get("maxi") or None
            severity = severity_from_magnitude(magnitude)
            if shindo in SHINDO_SEVERITY:
                # felt intensity beats magnitude when it is strong
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
                    # a distant quake relayed by JMA is not in Japan: let the
                    # pipeline deduce the country from the place label
                    country=None if distant else "Japan",
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

            # "Tidak berpotensi tsunami" = no potential. Any other wording
            # ("Berpotensi tsunami...") is an alert, and it arrives before the
            # PTWC.
            potential = (row.get("Potensi") or "").strip()
            tsunami = bool(potential) and "tidak berpotensi" not in potential.lower()

            place = row.get("Wilayah") or "Indonesia"
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
    # MMI=3: below that, these are micro-quakes nobody feels
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

            # careful: GeoNet only puts [lon, lat] in the geometry, the depth
            # is a separate property
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None

            magnitude = props.get("magnitude")
            place = props.get("locality") or "New Zealand"
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
    # Standard FDSN: exactly the same contract as USGS, down to the field
    # casing. The cheapest source to maintain of the lot.
    url = "https://webservices.ingv.it/fdsnws/event/1/query?format=geojson&limit=200&orderby=time"

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for feature in (data or {}).get("features") or []:
            props = feature.get("properties") or {}
            event_id = props.get("eventId")
            if event_id is None:
                continue

            # INGV publishes in UTC but WITHOUT a timezone suffix: we attach it
            time = to_utc(props.get("time"))
            if time is None:
                continue

            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None
            depth = coords[2] if len(coords) > 2 else None

            magnitude = props.get("mag")
            place = props.get("place") or "Italy"
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
    """AFAD (Türkiye).

    Two traps verified against the real API:

    1. `date` is in **UTC**, not Türkiye time. Proven by cross-checking: the
       AFAD quake at 17:30:37 matches the same EMSC event timestamped 17:30:38
       UTC. A 3 h offset would have been obvious.
    2. `limit` truncates **before** sorting: `orderby=timedesc&limit=3` does not
       return the 3 most recent but the 3 OLDEST of the window, then sorted. So
       we ask for a short window with a large limit, and sort here.
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

            # all numbers arrive as strings
            lat, lon = number(row.get("latitude")), number(row.get("longitude"))
            magnitude = number(row.get("magnitude"))
            if lat is None or lon is None:
                continue

            # naive but UTC (proven by EMSC cross-check)
            time = to_utc(row.get("date"))
            if time is None:
                continue

            place = row.get("location") or "Türkiye"
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
        # sorting by freshness is our job, the API does not guarantee it
        events.sort(key=lambda e: e.time, reverse=True)
        return events


# ------------------------------------------------------------------------ GEOFON


class GeofonSource(JsonPollSource):
    """GEOFON (GFZ Potsdam) -- third worldwide catalog, next to EMSC and USGS.

    Its value is not coverage (all three overlap) but the **vote**: with three
    independent solutions, the cross-source dedup confirms an event instead
    of assuming it, and a magnitude disagreement becomes visible instead of
    invisible.

    GEOFON's FDSN service refuses `format=json` (400): it only speaks `text`
    and QuakeML. So we go through its eqinfo service, which returns GeoJSON
    of the same family as USGS.
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

            # timestamp without a timezone suffix, like INGV
            time = to_utc(props.get("time"))
            if time is None:
                continue

            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None
            depth = coords[2] if len(coords) > 2 else None

            magnitude = props.get("mag")
            place = props.get("place") or "unknown region"
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
                    # "C:confirmed" vs "A:automatic": a solution reviewed by an
                    # analyst is not the same thing as an automatic detection
                    alert=(props.get("status") or "").split(":")[-1] or None,
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url=props.get("url"),
                    raw={"status": props.get("status"), "has_moment_tensor": props.get("hasMT")},
                )
            )
        return events
