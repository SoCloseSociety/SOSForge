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
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, severity_for_quake, to_utc
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

    def restore(self) -> None:
        """Hook: reload what this source must remember across a restart."""

    def persist(self) -> None:
        """Hook: save it after a successful cycle."""

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        self.restore()
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
                    await self.flush_retractions()
                    self.persist()
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


def _km(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float:
    import math

    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    p1, p2 = math.radians(lat1), math.radians(lat2)  # type: ignore[arg-type]
    dl = math.radians(lon2 - lon1)  # type: ignore[operator]
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


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
            severity = severity_for_quake(magnitude, depth)
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


BMKG_BASE = "https://data.bmkg.go.id/DataMKG/TEWS/"
# Read in this order, and for a quake present in several, the FIRST one wins
# for place and tsunami potential: `gempaterkini` (the 15 latest M5+, always
# with a tsunami statement), `autogempa` (the single latest event, with a
# shakemap), `gempadirasakan` (the 15 latest FELT quakes, any magnitude).
BMKG_FEEDS = ("gempaterkini", "autogempa", "gempadirasakan")

RE_BMKG_PLACE_PREFIX = re.compile(r"^\s*Pusat gempa berada di (?:laut|darat)\s+", re.IGNORECASE)
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9,
         "X": 10, "XI": 11, "XII": 12}  # fmt: skip
RE_ROMAN = re.compile(r"\b(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b")


def bmkg_tsunami(potensi: str | None) -> bool:
    """The tsunami-potential flag, from BMKG's own sentence.

    `gempaterkini` always states it ("Tidak berpotensi tsunami" = no
    potential). `autogempa` reuses the SAME field for something else: on
    2026-10-26 it read "Gempa ini dirasakan untuk diteruskan pada masyarakat"
    ("this quake was felt; pass it on to the public"). The old rule -- any
    wording but "tidak berpotensi" is an alert -- would have published that
    felt M4.9 as an EXTREME tsunami. The word has to be there.
    """
    text = (potensi or "").strip().lower()
    return "tsunami" in text and "tidak berpotensi" not in text


def bmkg_felt(dirasakan: str | None) -> int | None:
    """Highest MMI in "III Bajawa, III Maumere, II - III Ruteng" -> 3."""
    levels = [ROMAN[m] for m in RE_ROMAN.findall(dirasakan or "")]
    return max(levels) if levels else None


class BmkgSource(JsonPollSource):
    """BMKG (Indonesia): three feeds, one event per quake.

    `gempaterkini` alone lists only the 15 latest M5+ quakes. On 2026-10-26
    the newest of them was 4.4 days old, so the ingestion horizon dropped all
    15 and BMKG contributed NOTHING -- 30 seen, 0 ingested -- for as long as
    Indonesia had no M5. The felt-quake feed (`gempadirasakan`) carries every
    quake people felt, whatever its magnitude: three of its fifteen were
    inside the horizon that day. All three feeds key a quake by the same
    `DateTime`, so one quake is one event however many feeds list it.
    """

    name = "bmkg"
    kind = "poll"
    url = BMKG_BASE + "gempaterkini.json"

    @staticmethod
    def rows(data: Any) -> list[dict]:
        """The quakes of one feed: a list, or `autogempa`'s single object."""
        gempa = ((data or {}).get("Infogempa") or {}).get("gempa") or []
        return [
            row
            for row in (gempa if isinstance(gempa, list) else [gempa])
            if isinstance(row, dict) and row.get("DateTime")
        ]

    def merge(self, payloads: dict[str, Any]) -> list[dict]:
        rows: dict[str, dict] = {}
        for feed in BMKG_FEEDS:
            for row in self.rows(payloads.get(feed)):
                merged = rows.setdefault(row["DateTime"], {"_feeds": []})
                merged["_feeds"].append(feed)
                for key, value in row.items():
                    merged.setdefault(key, value)
        return list(rows.values())

    def parse_payload(self, data: Any) -> list[Event]:
        # One feed alone (gempaterkini's shape), or a {feed: payload} mapping.
        payloads = (
            data if isinstance(data, dict) and "Infogempa" not in data else {"gempaterkini": data}
        )
        events: list[Event] = []
        for row in self.merge(payloads):
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
                magnitude = float(row.get("Magnitude") or "")
            except (TypeError, ValueError):
                magnitude = None

            depth = None
            depth_match = re.search(r"([\d.]+)", row.get("Kedalaman") or "")
            if depth_match:
                depth = float(depth_match.group(1))

            potential = (row.get("Potensi") or "").strip()
            tsunami = bmkg_tsunami(potential)
            felt = bmkg_felt(row.get("Dirasakan"))

            place = RE_BMKG_PLACE_PREFIX.sub("", row.get("Wilayah") or "").strip() or "Indonesia"
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
                    # BMKG's own tsunami-potential flag outranks everything,
                    # depth included: it is the agency saying the sea may move.
                    severity=Severity.EXTREME if tsunami else severity_for_quake(magnitude, depth),
                    tsunami=tsunami,
                    alert=f"felt MMI {felt}" if felt else None,
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url="https://www.bmkg.go.id/gempabumi/gempabumi-terkini.bmkg",
                    raw={
                        "potensi": potential,
                        "dirasakan": row.get("Dirasakan"),
                        "feeds": row.get("_feeds"),
                    },
                )
            )
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                payloads: dict[str, Any] = {}
                last_error: Exception | None = None
                for feed in BMKG_FEEDS:
                    try:
                        resp = await client.get(f"{BMKG_BASE}{feed}.json")
                        resp.raise_for_status()
                        payloads[feed] = resp.json()
                        # the ids this feed lists (one quake, one id, in
                        # every feed): a feed back after failing since
                        # startup lists its baseline
                        self.health.feed_ok(
                            feed, (f"bmkg:{row['DateTime']}" for row in self.rows(payloads[feed]))
                        )
                    except Exception as exc:
                        last_error = exc
                        self.health.feed_failed(feed, exc)
                        log.warning("bmkg %s: %s", feed, exc)
                if payloads:
                    events = self.parse_payload(payloads)
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                elif last_error is not None:
                    self.health.fail(last_error)
                await asyncio.sleep(self.poll_seconds)


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

            # GeoNet withdraws solutions, and says so in `quality`. A fifth of
            # the live feed is marked "deleted" -- including, the day this was
            # found, a phantom M5.2 at MMI 5 next to Wellington. Publishing
            # those is bad enough; re-publishing them at every 60 s poll with
            # no way out is the version we shipped.
            if str(props.get("quality") or "").strip().lower() == "deleted":
                self.retractions.append(f"geonet:{public_id}")
                continue

            # careful: GeoNet only puts [lon, lat] in the geometry, the depth
            # is a separate property
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None

            magnitude = props.get("magnitude")
            depth = props.get("depth")
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
                    depth_km=depth,
                    magnitude=round(magnitude, 1) if isinstance(magnitude, (int, float)) else None,
                    mag_type="M",
                    place=place,
                    country="New Zealand",
                    severity=severity_for_quake(
                        magnitude, depth if isinstance(depth, (int, float)) else None
                    ),
                    alert=f"MMI {props.get('mmi')}" if props.get("mmi") is not None else None,
                    title=f"M {magnitude:.1f} -- {place}" if magnitude else place,
                    url=f"https://www.geonet.org.nz/earthquake/{public_id}",
                    raw={"mmi": props.get("mmi"), "quality": props.get("quality")},
                )
            )
        return events


# -------------------------------------------------------------------------- INGV


# Italian province codes (sigle). INGV names an Italian epicentre after its
# municipality and province -- "7 km NE Lauria (PZ)" -- and everything else
# differently: "Tirreno Meridionale [Mare]", "Vanuatu Islands [Sea: Vanuatu]",
# "China [Land]". `country="Italy"` was stamped on every event, so an M6.1 in
# Vanuatu wore an Italian flag (lane Q, 2026-10-27). A missing code fails safe
# to no country.
ITALIAN_PROVINCES = frozenset(
    [
        "AG",
        "AL",
        "AN",
        "AO",
        "AP",
        "AQ",
        "AR",
        "AT",
        "AV",
        "BA",
        "BG",
        "BI",
        "BL",
        "BN",
        "BO",
        "BR",
        "BS",
        "BT",
        "BZ",
        "CA",
        "CB",
        "CE",
        "CH",
        "CI",
        "CL",
        "CN",
        "CO",
        "CR",
        "CS",
        "CT",
        "CZ",
        "EN",
        "FC",
        "FE",
        "FG",
        "FI",
        "FM",
        "FR",
        "GE",
        "GO",
        "GR",
        "IM",
        "IS",
        "KR",
        "LC",
        "LE",
        "LI",
        "LO",
        "LT",
        "LU",
        "MB",
        "MC",
        "ME",
        "MI",
        "MN",
        "MO",
        "MS",
        "MT",
        "NA",
        "NO",
        "NU",
        "OG",
        "OR",
        "OT",
        "PA",
        "PC",
        "PD",
        "PE",
        "PG",
        "PI",
        "PN",
        "PO",
        "PR",
        "PT",
        "PU",
        "PV",
        "PZ",
        "RA",
        "RC",
        "RE",
        "RG",
        "RI",
        "RM",
        "RN",
        "RO",
        "SA",
        "SI",
        "SO",
        "SP",
        "SR",
        "SS",
        "SU",
        "SV",
        "TA",
        "TE",
        "TN",
        "TO",
        "TP",
        "TR",
        "TS",
        "TV",
        "UD",
        "VA",
        "VB",
        "VC",
        "VE",
        "VI",
        "VR",
        "VS",
        "VT",
        "VV",
    ]
)
RE_INGV_PROVINCE = re.compile(r"\(([A-Z]{2})\)\s*$")


def ingv_country(place: str) -> str | None:
    match = RE_INGV_PROVINCE.search(place or "")
    return "Italy" if match and match.group(1) in ITALIAN_PROVINCES else None


class IngvSource(JsonPollSource):
    """INGV (Italy), FDSN GeoJSON.

    INGV RE-IDENTIFIES events: `eventid=46940061` now answers a 302 to
    `47630362`, the same quake (same origin to the tenth of a second), and the
    feed only lists the new id. The old one stayed in the store as a second
    quake. An id that arrives with the origin time (within a second) and the
    position (within 10 km) of one this source already emitted withdraws the
    old one -- but only once the page no longer lists the old id.

    The catalogue also keeps TWO solutions of one quake under two ids, both
    listed at the same time (Sefro 47459342/47459372: 0.2 s, 0.12 km; Eolie
    47569802/47569772: 0.4 s, 1.7 km, 2026-10). Withdrawing on the match
    alone made each one withdraw the other every cycle: the quake vanished
    from INGV and came back as new every 60 s.
    """

    name = "ingv"
    kind = "poll"

    def __init__(self, poll_seconds: float = 60.0, url: str | None = None):
        super().__init__(poll_seconds, url)
        # event id -> (origin, lat, lon) of what this source has emitted
        self._emitted: dict[str, tuple[datetime, float | None, float | None]] = {}

    def supersede(self, events: list[Event]) -> None:
        listed = {event.id for event in events}
        for event in events:
            for old_id, (origin, lat, lon) in list(self._emitted.items()):
                # still listed: a second solution, not a re-identification
                if old_id in listed or abs((origin - event.time).total_seconds()) > 1.0:
                    continue
                if (
                    None in (lat, lon, event.lat, event.lon)
                    or _km(lat, lon, event.lat, event.lon) <= 10.0
                ):
                    self.expirations.append(old_id)
                    del self._emitted[old_id]
            self._emitted[event.id] = (event.time, event.lat, event.lon)
        # forget what is long gone from the feed
        if len(self._emitted) > 5000:
            for old_id in list(self._emitted)[: len(self._emitted) - 5000]:
                del self._emitted[old_id]

    def restore(self) -> None:
        # INGV re-identifies days later: a restarted process must still know
        # the origins it published under the old ids
        if self._emitted:
            return
        for key, (origin, lat, lon) in (self.load_memory().get("emitted") or {}).items():
            try:
                self._emitted[key] = (datetime.fromisoformat(origin), lat, lon)
            except (TypeError, ValueError):
                continue

    def persist(self) -> None:
        horizon = datetime.now(UTC) - timedelta(days=7)
        self.save_memory(
            {
                "emitted": {
                    key: [origin.isoformat(), lat, lon]
                    for key, (origin, lat, lon) in self._emitted.items()
                    if origin > horizon
                }
            }
        )

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
                    country=ingv_country(place),
                    severity=severity_for_quake(magnitude, depth),
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url=f"https://terremoti.ingv.it/event/{event_id}",
                    raw={"author": props.get("author"), "type": props.get("type")},
                )
            )
        self.supersede(events)
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
            depth = number(row.get("depth"))
            events.append(
                Event(
                    id=f"afad:{event_id}",
                    source="afad",
                    source_id=str(event_id),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=depth,
                    magnitude=magnitude,
                    mag_type=row.get("type"),
                    place=place,
                    country=row.get("country") or "Türkiye",
                    severity=severity_for_quake(magnitude, depth),
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


def _geofon_status(status: str | None) -> str | None:
    """ "A:automatic. Disclaimer: ... may be erroneous!" -> "automatic"."""
    if not status:
        return None
    label = status.partition(":")[2].partition(".")[0].strip().lower()
    return label or None


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
                    severity=severity_for_quake(magnitude, depth),
                    # "C:confirmed" vs "A:automatic": a solution reviewed by
                    # an analyst is not the same thing as an automatic
                    # detection. Careful with the real value -- it is
                    # "A:automatic. Disclaimer: Unless revised by a
                    # geophysicist, ... may be erroneous!". Splitting on the
                    # LAST colon rendered that whole sentence as the badge.
                    alert=_geofon_status(props.get("status")),
                    preliminary=_geofon_status(props.get("status")) != "confirmed",
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url=props.get("url"),
                    raw={"status": props.get("status"), "has_moment_tensor": props.get("hasMT")},
                )
            )
        return events
