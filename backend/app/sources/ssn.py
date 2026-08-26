"""SSN UNAM -- Servicio Sismologico Nacional (Mexico).

Why this source exists. Mexico had no national coverage in this product at all.
Measured on 2026-08-26 over a three-day window on the whole Mexican box
(14..24 N, -107..-92 E): **USGS returned zero events**, EMSC returned 52 down to
M3.0, and the SSN feed carries events down to M1.4. The M4.0 that the SSN placed
27 km south-west of Coyuca de Benitez, Guerrero, is in EMSC and in no US
catalogue. Twenty million people in a basin with extreme site amplification were
getting worse coverage than New Zealand's five million.

Traps verified against the real payload (captured 2026-08-26):

- **The timestamps are local, not UTC.** The description says so in words
  ("Hora de Mexico") and nothing in the feed carries an offset. Cross-checked
  against EMSC on three separate events, second for second:
  `2026-08-26 01:39:32` == `2026-08-26T07:39:32Z` (M4.0, 16.876/-100.304),
  `2026-08-25 16:43:57` == `22:43:57Z` (M3.7, 18.218/-105.677),
  `2026-08-25 14:08:45` == `20:08:45Z` (M3.4, 16.189/-93.663). The offset is
  exactly **UTC-6**, which is what `America/Mexico_City` resolves to since
  Mexico abolished DST nationally in 2022. Reading these as UTC would have put
  every Mexican earthquake six hours in the past.
- **No `guid`, no `pubDate`.** There is no identifier of any kind in the feed,
  so the key has to be derived. The origin time to the second is the field SSN
  itself uses to address an event in its own permalink, and it is the most
  stable single field available.
- **The permalink in `<link>` is dead**: `www2.ssn.unam.mx:8080/jsp/...`
  answers 404. We link to the SSN's own recent-seismicity page instead of
  shipping a link that goes nowhere.
- **The feed is plain HTTP only.** `https://www.ssn.unam.mx/` does not complete
  a TLS handshake at all. This is the one source here that cannot be fetched
  over TLS; the payload is public and read-only, but it is worth knowing.
- **The description is CDATA carrying HTML entities** (`M&eacute;xico`). CDATA
  is literal, so the XML parser does not decode them: we unescape ourselves.
- The place label ends with a Mexican state abbreviation ("..., GRO"), and
  `app.countries.resolve` cannot deduce Mexico from it (measured: returns
  None). But SSN also locates events across the border, and it CAN resolve
  those from the text ("..., GUATEMALA" -> GT). So the state abbreviation is
  what we stamp Mexico on, and anything else is left to the pipeline. Same
  discipline as the JMA distant-quake trap: a national feed is not a claim
  about the country.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import httpx

from app.models.event import Event, Kind, severity_from_magnitude
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

URL = "http://www.ssn.unam.mx/rss/ultimos-sismos.xml"
USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
PAGE_URL = "http://www.ssn.unam.mx/sismicidad/ultimos/"

NS = {"geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}

# "4.0, 27 km al SUROESTE de  COYUCA DE BENITEZ, GRO"
RE_TITLE = re.compile(r"^\s*([\d.]+)\s*,\s*(.+?)\s*$")
# "Fecha:2026-08-26 01:39:32 (Hora de M&eacute;xico)"
RE_DATE = re.compile(r"Fecha:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
# "Profundidad: 13.4 km"
RE_DEPTH = re.compile(r"Profundidad:\s*([\d.]+)")

# The 32 federal entities as the SSN abbreviates them. An entry MISSING from
# this table fails safe -- the event simply gets no country here and the
# pipeline resolves it from the place text -- so the cost of an omission is a
# missing flag, never a wrong one.
MEXICAN_STATES = frozenset(
    {
        "AGS",
        "BC",
        "BCS",
        "CAMP",
        "CDMX",
        "CHIH",
        "CHIS",
        "COAH",
        "COL",
        "DF",
        "DGO",
        "GRO",
        "GTO",
        "HGO",
        "JAL",
        "MEX",
        "MICH",
        "MOR",
        "NAY",
        "NL",
        "OAX",
        "PUE",
        "QRO",
        "QROO",
        "SIN",
        "SLP",
        "SON",
        "TAB",
        "TAMS",
        "TLAX",
        "VER",
        "YUC",
        "ZAC",
    }
)

# Fallback for a runtime with no tzdata (a slim container): the offset verified
# against EMSC. ZoneInfo is preferred because it would follow Mexico if it ever
# reinstated daylight saving, which a hard-coded offset never would.
MEXICO_FIXED = timezone(timedelta(hours=-6))


def _mexico_tz() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/Mexico_City")
    except Exception:  # pragma: no cover - only on a runtime without tzdata
        log.warning("ssn: tzdata unavailable, falling back to a fixed UTC-6")
        return MEXICO_FIXED


def _number(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _country(place: str) -> str | None:
    """Mexico only when the label names a Mexican state. See the docstring."""
    tail = place.rsplit(",", 1)[-1].strip().upper()
    return "Mexico" if tail in MEXICAN_STATES else None


def parse_item(item: ET.Element, tz: tzinfo) -> Event | None:
    title = html.unescape((item.findtext("title") or "").strip())
    description = html.unescape(item.findtext("description") or "")
    if not title:
        return None

    date_match = RE_DATE.search(description)
    if not date_match:
        # Without an origin time there is neither a date nor a key.
        return None
    try:
        local = datetime.strptime(date_match.group(1).replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    # Stored in UTC like every other source: the instant is the same either
    # way, but a feed that mixes offsets makes every comparison a puzzle.
    time = local.replace(tzinfo=tz).astimezone(UTC)

    title_match = RE_TITLE.match(title)
    if title_match:
        magnitude = _number(title_match.group(1))
        place = re.sub(r"\s+", " ", title_match.group(2))
    else:
        # Never drop an event because its headline is shaped unusually: the
        # position and the time are what matter, the magnitude is a bonus.
        magnitude, place = None, re.sub(r"\s+", " ", title)

    lat = _number(item.findtext("geo:lat", namespaces=NS))
    lon = _number(item.findtext("geo:long", namespaces=NS))
    # Lesson 15: a wrong position is far worse than a missing one, and since
    # the model now RAISES on an out-of-range coordinate, an unchecked value
    # would take the whole batch down with it.
    if lat is not None and not -90 <= lat <= 90:
        lat = None
    if lon is not None and not -180 <= lon <= 180:
        lon = None

    depth_match = RE_DEPTH.search(description)
    depth = _number(depth_match.group(1)) if depth_match else None

    source_id = local.strftime("%Y%m%d%H%M%S")
    return Event(
        id=f"ssn:{source_id}",
        source="ssn",
        source_id=source_id,
        kind=Kind.EARTHQUAKE,
        time=time,
        lat=lat,
        lon=lon,
        depth_km=depth,
        magnitude=magnitude,
        mag_type="M",
        place=place,
        country=_country(place),
        severity=severity_from_magnitude(magnitude),
        title=f"M {magnitude} -- {place}" if magnitude is not None else place,
        url=PAGE_URL,
        raw={"local_time": date_match.group(1), "local_zone": "America/Mexico_City"},
    )


class SsnSource(Source):
    name = "ssn"
    kind = "poll"

    def __init__(self, poll_seconds: float = 60.0, url: str = URL):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url
        self.tz = _mexico_tz()

    def parse_payload(self, text: str) -> list[Event]:
        root = ET.fromstring(text)
        events: list[Event] = []
        for item in root.iter("item"):
            try:
                event = parse_item(item, self.tz)
            except Exception as exc:
                # One malformed entry must not cost us the other fourteen.
                log.warning("ssn: unreadable item (%s)", exc)
                continue
            if event is not None:
                events.append(event)
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    events = self.parse_payload(resp.text)
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)
