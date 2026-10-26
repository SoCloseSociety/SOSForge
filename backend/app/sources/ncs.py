"""National Center for Seismology (India) -- riseq.seismo.gov.in

Why it exists here. The Himalayan front, the Hindu Kush and the
Indo-Burman ranges had no national network in this product. Measured on
2026-10-26 against EMSC (80 h) and USGS (week) with this product's own dedup
thresholds (90 s, 250 km, 1.2 magnitude): of the 57 NCS events inside that
window, **20 (35%) are in neither**, ten of them M4 or more -- Afghanistan,
Pakistan, Tibet, western China, Maharashtra, Kashmir. (Some of the Chinese
ones will cluster with CENC; that is the deduper's job, and it is measured on
the live feed, not assumed here.)

Licence (seismo.gov.in/copyright-policy): "Material featured on this website
may be reproduced free of charge in any format on media without requiring
specific permission", accurately, not in a misleading context, and with the
source prominently acknowledged.

Traps verified against the real page (captured 2026-10-26):

- **There is no API.** No JSON, GeoJSON or FDSN endpoint answers. The events
  are in the home page itself: every `<li class="... event_list">` carries the
  whole event as a JSON string in a `data-json` attribute. The page is parsed
  as HTML (so entities in the attribute are decoded by the parser, not by a
  regex) and only that attribute is read.
- **Times are India Standard Time, and say so**: `"2026-10-26 23:37:01
  IST"`. Proven, not assumed: converted from +05:30, the M3.5+ events match
  EMSC/USGS at an offset of exactly 0 h 43 times, and at no other whole half
  hour more than 4 times. Any other suffix is refused rather than guessed.
- **The web server's clock is wrong.** Its `Date` header read 19:08 GMT at
  19:56 UTC -- 48 minutes slow -- while the origin times above are right to
  the second. Nothing here is ever dated from the server.
- **Magnitude and depth share one string**, `"M: 4.0 , D: 10km"`, with no
  magnitude type. Depth is positive km.
- **`lat_long` is one string, latitude first**: `"38.626, 70.823"`.
- **A national feed full of foreign events**: Tajikistan, Afghanistan,
  Tibet, Myanmar, Nepal. Lesson 16: the publisher is not the place. No
  country is stamped here; the pipeline resolves it from the region name.
- **630 KB per page** decoded (63 KB on the wire), ten days of events. The
  window is the page; the poll is slow to match.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

from app.models.event import Event, Kind, severity_for_quake
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
URL = "https://riseq.seismo.gov.in/"
IST = timezone(timedelta(hours=5, minutes=30), "IST")

RE_TIME = re.compile(r"^\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+IST\s*$")
RE_MAG_DEPTH = re.compile(
    r"M:\s*(?P<mag>-?\d+(?:\.\d+)?)\s*,\s*D:\s*(?P<depth>-?\d+(?:\.\d+)?)\s*km", re.IGNORECASE
)
RE_NAME = re.compile(r"^\s*M:\s*-?\d+(?:\.\d+)?\s*-\s*(?P<place>.+?)\s*$")


class _EventListParser(HTMLParser):
    """Collects the `data-json` attribute of every event list item."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "li":
            return
        values = dict(attrs)
        if "event_list" in (values.get("class") or "").split() and values.get("data-json"):
            self.payloads.append(values["data-json"] or "")


def extract_rows(html: str) -> list[dict[str, Any]]:
    parser = _EventListParser()
    parser.feed(html)
    rows: list[dict[str, Any]] = []
    for payload in parser.payloads:
        try:
            row = json.loads(payload)
        except json.JSONDecodeError:
            log.warning("ncs: unreadable data-json attribute, skipped")
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def parse_time(value: str | None) -> datetime | None:
    match = RE_TIME.match(value or "")
    if not match:
        return None
    try:
        local = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return local.replace(tzinfo=IST).astimezone(UTC)


def parse_row(row: dict[str, Any]) -> Event | None:
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        return None
    time = parse_time(row.get("origin_time"))
    if time is None:
        return None

    lat = lon = None
    parts = str(row.get("lat_long") or "").split(",")
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            lat = lon = None
    # lesson 15
    if lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None

    magnitude = depth = None
    mag_depth = RE_MAG_DEPTH.search(str(row.get("magnitude_depth") or ""))
    if mag_depth:
        magnitude = round(float(mag_depth.group("mag")), 1)
        depth = round(float(mag_depth.group("depth")), 1)

    name = RE_NAME.match(str(row.get("event_name") or ""))
    place = name.group("place") if name else "South Asia"
    reviewed = str(row.get("event_type") or "").strip().lower() == "reviewed"

    return Event(
        id=f"ncs:{event_id}",
        source="ncs",
        source_id=event_id,
        kind=Kind.EARTHQUAKE,
        time=time,
        lat=lat,
        lon=lon,
        depth_km=depth,
        magnitude=magnitude,
        mag_type="M",
        place=place,
        severity=severity_for_quake(magnitude, depth),
        preliminary=not reviewed,
        title=f"M {magnitude} -- {place}" if magnitude is not None else place,
        url=URL,
        raw={
            "origin_time_ist": row.get("origin_time"),
            "event_type": row.get("event_type"),
            "has_intensity_map": row.get("is_intensity"),
        },
    )


class NcsSource(Source):
    """India's national seismic network, read from its public event list.

    Every event on the page is re-emitted each cycle (150 over ten days);
    the store turns the unchanged ones into no-ops.
    """

    name = "ncs"
    kind = "poll"

    def __init__(self, poll_seconds: float = 180.0, url: str = URL):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url

    def parse_payload(self, html: str) -> list[Event]:
        events: list[Event] = []
        seen: set[str] = set()
        for row in extract_rows(html):
            try:
                event = parse_row(row)
            except Exception as exc:
                log.warning("ncs: unreadable row (%s)", exc)
                continue
            if event is None or event.id in seen:
                continue
            seen.add(event.id)
            events.append(event)
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
        async with httpx.AsyncClient(
            timeout=60.0, headers=headers, follow_redirects=True
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
