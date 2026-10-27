"""US Drought Monitor -- droughtmonitor.unl.edu

Why it exists here. `drought` sat at ZERO events in the live feed on
2026-10-27: the only drought source was GDACS, whose orange droughts had all
been closed. The US Drought Monitor map of 2026-10-22 had D3 (extreme) and
D4 (exceptional) drought over 30 separate regions of at least 0.25 square
degrees (about 2500 km2).

Licence (droughtmonitor.unl.edu/About/Permission.aspx): reproduction allowed
with the credit "The U.S. Drought Monitor is jointly produced by the National
Drought Mitigation Center at the University of Nebraska-Lincoln, the United
States Department of Agriculture, the National Oceanic and Atmospheric
Administration and the National Aeronautics and Space Administration. Map
courtesy of NDMC."

Traps verified against the real file (captured 2026-10-27):

- **It is a weekly MAP, not an event feed**: five features, one MultiPolygon
  per category `DM` 0 (abnormally dry) to 4 (exceptional), and nothing else
  -- no date, no name, no id. Each separate polygon of D3/D4 becomes one
  event, placed at the centre of its outer ring, with its extent as `bbox`.
  D0-D2 are not published (the feed would be a weather map).
- **The only date is the HTTP `Last-Modified`** (2026-10-22T10:15:57Z, a
  Thursday: the map is released weekly, "data through the previous Tuesday
  morning"). That release time is the event time; it is the dataset's date,
  never our fetch time.
- **17 MB, uncompressed, 20 s.** Polled with `If-Modified-Since`: a 304 costs
  nothing and the last map is re-emitted, so its regions are not swept as
  silent between two weekly releases.
- A region has no stable identity across weeks: its id is its category and
  rounded centre. When a new map arrives, every region of the previous one
  that is not in it is withdrawn. "The previous one" is the map last
  PUBLISHED, persisted in the source memory: held only in memory, a restart
  between a release and the next poll left last week's regions on the map
  next to the new ones until the 6 h silence sweep (between the maps valid
  2026-10-13 and 2026-10-20, 7 of the 9 D4 regions changed id).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity
from app.sources import geometry
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
URL = "https://droughtmonitor.unl.edu/data/json/usdm_current.json"
PAGE_URL = "https://droughtmonitor.unl.edu/"

CATEGORIES: dict[int, tuple[str, Severity]] = {
    3: ("extreme drought (D3)", Severity.MODERATE),
    4: ("exceptional drought (D4)", Severity.SEVERE),
}
MIN_AREA_DEG2 = 0.25


def _ring_area(ring: list) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False):
        total += (x2 - x1) * (y2 + y1)
    return abs(total) / 2


def parse_map(data: Any, released: datetime) -> list[Event]:
    events: list[Event] = []
    for feature in (data or {}).get("features") or []:
        props = feature.get("properties") or {}
        category = props.get("DM")
        if category not in CATEGORIES:
            continue
        label, severity = CATEGORIES[category]
        shape = feature.get("geometry") or {}
        polygons = shape.get("coordinates") or []
        if shape.get("type") == "Polygon":
            polygons = [polygons]
        for polygon in polygons:
            if not polygon or len(polygon[0]) < 4:
                continue
            outer = [pt for pt in polygon[0] if isinstance(pt, list) and len(pt) >= 2]
            area = _ring_area(outer)
            if area < MIN_AREA_DEG2:
                continue
            points = [(float(pt[1]), float(pt[0])) for pt in outer]
            middle = geometry.center(points)
            if middle is None:
                continue
            lat, lon = middle
            event_id = f"usdm:D{category}:{lat:.1f},{lon:.1f}"
            events.append(
                Event(
                    id=event_id,
                    source="usdm",
                    source_id=event_id.split(":", 1)[1],
                    kind=Kind.DROUGHT,
                    time=released,
                    lat=lat,
                    lon=lon,
                    bbox=geometry.extent(points),
                    place="United States",
                    country="United States",
                    country_code="US",
                    severity=severity,
                    ongoing=True,
                    alert=label,
                    title=f"{label[0].upper()}{label[1:]} -- United States",
                    url=PAGE_URL,
                    raw={"category": category, "area_deg2": round(area, 3)},
                )
            )
    events.sort(key=lambda e: (e.severity.value, e.raw["area_deg2"]), reverse=True)
    return events


class UsdmSource(Source):
    name = "usdm"
    kind = "poll"

    def __init__(self, poll_seconds: float = 6 * 3600.0, url: str = URL):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url
        self._last_modified: str | None = None
        self._events: list[Event] = []
        # ids of the map last published, by this process or the one before
        self._published: set[str] = set()

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        self._published |= {
            event_id
            for event_id in self.load_memory().get("published") or []
            if isinstance(event_id, str) and event_id.startswith("usdm:")
        }
        async with httpx.AsyncClient(
            timeout=180.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    conditional = dict(headers)
                    if self._last_modified:
                        conditional["If-Modified-Since"] = self._last_modified
                    resp = await client.get(self.url, headers=conditional)
                    if resp.status_code != 304:
                        resp.raise_for_status()
                        stamp = resp.headers.get("last-modified")
                        if not stamp:
                            raise ValueError("no Last-Modified: the map has no other date")
                        released = parsedate_to_datetime(stamp).astimezone(UTC)
                        fresh = parse_map(resp.json(), released)
                        ids = {e.id for e in fresh}
                        self.expirations.extend(sorted(self._published - ids))
                        self._events, self._last_modified = fresh, stamp
                        self._published = ids
                        self.save_memory({"published": sorted(ids), "released": stamp})
                    for event in self._events:
                        await emit(event.model_copy(deep=True))
                    await self.flush_retractions()
                    self.health.ok(len(self._events))
                    wait = self.poll_seconds
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                    # a weekly map polled every 6 h must not wait 6 h to
                    # retry a dropped connection
                    wait = min(self.poll_seconds, 900.0)
                await asyncio.sleep(wait)
