"""Environment Agency (England) flood warnings -- environment.data.gov.uk

Why it exists here. Floods were 10 events in the whole live feed on
2026-10-27 (Meteoalarm and NWS only), and England had none of them while the
Environment Agency had 40 flood alerts and warnings in force that morning.

Open Government Licence v3, no registration. Attribution required: "this
uses Environment Agency flood and river level data from the real-time data
API (Beta)".

Traps verified against the real payload (captured 2026-10-27):

- **The severity scale runs DOWN** (documented in the API reference): 1
  "Severe Flood Warning -- severe flooding, danger to life", 2 "Flood Warning
  -- flooding is expected, immediate action required", 3 "Flood Alert --
  flooding is possible, be prepared", **4 "Warning no longer in force"**.
- **Ended warnings stay listed**: 58 of 98 items were level 4. They are not
  published; one that was stored is withdrawn as `lifted`.
- **No position in the list.** Each item names a flood area; the area record
  (`/id/floodAreas/{id}`, static) has its centroid in WGS84 `lat`/`long`, and
  an `envelope` in **British National Grid metres** (`{x, y}`, EPSG:27700),
  converted here (inverse Transverse Mercator on Airy 1830; the OSGB36 datum
  shift of ~120 m is not applied -- irrelevant to a box kilometres wide). The
  polygon itself is up to 700 KB per area and is not fetched.
- **Times have no offset**, and the reference does not say which zone they
  are in. Read as Europe/London. That is exact today either way (GMT = UTC
  until 2027-03-28); whether the API means UTC or UK local time must be
  checked on the first day of British Summer Time.
- One warning per flood area at a time: the AREA is the key, so a warning
  re-issued for the same area revises it instead of stacking markers.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.models.event import Event, Kind, Severity
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
URL = "https://environment.data.gov.uk/flood-monitoring/id/floods"
AREA_URL = "https://environment.data.gov.uk/flood-monitoring/id/floodAreas/{area}"
PAGE_URL = "https://check-for-flooding.service.gov.uk/alerts-and-warnings"
LONDON = ZoneInfo("Europe/London")

# Product ladder, same reading as a CAP forecast (models/event.py): the
# agency's red/orange/yellow equivalents one rung below "observed".
LEVELS: dict[int, tuple[str, Severity]] = {
    1: ("severe flood warning", Severity.SEVERE),
    2: ("flood warning", Severity.MODERATE),
    3: ("flood alert", Severity.MINOR),
}
ENDED = 4
AREAS_PER_CYCLE = 25


def bng_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """British National Grid (OSGB36 TM) -> (lat, lon) degrees, Airy 1830.

    The Ordnance Survey's inverse Transverse Mercator. No datum shift to
    WGS84 (about 120 m): this only ever draws the corners of a box.
    """
    a, b = 6377563.396, 6356256.909
    f0, lat0, lon0 = 0.9996012717, math.radians(49.0), math.radians(-2.0)
    n0, e0 = -100000.0, 400000.0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)

    def meridional(phi: float) -> float:
        return (
            b
            * f0
            * (
                (1 + n + 5 / 4 * n**2 + 5 / 4 * n**3) * (phi - lat0)
                - (3 * n + 3 * n**2 + 21 / 8 * n**3) * math.sin(phi - lat0) * math.cos(phi + lat0)
                + (15 / 8 * n**2 + 15 / 8 * n**3)
                * math.sin(2 * (phi - lat0))
                * math.cos(2 * (phi + lat0))
                - 35 / 24 * n**3 * math.sin(3 * (phi - lat0)) * math.cos(3 * (phi + lat0))
            )
        )

    phi = lat0
    m = 0.0
    while True:
        phi = (northing - n0 - m) / (a * f0) + phi
        m = meridional(phi)
        if abs(northing - n0 - m) < 0.00001:
            break
    sin_phi, cos_phi, tan_phi = math.sin(phi), math.cos(phi), math.tan(phi)
    nu = a * f0 / math.sqrt(1 - e2 * sin_phi**2)
    rho = a * f0 * (1 - e2) / (1 - e2 * sin_phi**2) ** 1.5
    eta2 = nu / rho - 1
    vii = tan_phi / (2 * rho * nu)
    viii = tan_phi / (24 * rho * nu**3) * (5 + 3 * tan_phi**2 + eta2 - 9 * tan_phi**2 * eta2)
    ix = tan_phi / (720 * rho * nu**5) * (61 + 90 * tan_phi**2 + 45 * tan_phi**4)
    x = 1 / (cos_phi * nu)
    xi = 1 / (cos_phi * 6 * nu**3) * (nu / rho + 2 * tan_phi**2)
    xii = 1 / (cos_phi * 120 * nu**5) * (5 + 28 * tan_phi**2 + 24 * tan_phi**4)
    xiia = (
        1
        / (cos_phi * 5040 * nu**7)
        * (61 + 662 * tan_phi**2 + 1320 * tan_phi**4 + 720 * tan_phi**6)
    )
    de = easting - e0
    lat = phi - vii * de**2 + viii * de**4 - ix * de**6
    lon = lon0 + x * de - xi * de**3 + xii * de**5 - xiia * de**7
    return math.degrees(lat), math.degrees(lon)


def area_position(
    area: dict[str, Any],
) -> tuple[tuple[float, float] | None, tuple[float, float, float, float] | None]:
    """(lat, lon) centroid and (west, south, east, north) box of a flood area."""
    centre = None
    lat, lon = area.get("lat"), area.get("long")
    if (
        isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    ):
        centre = (float(lat), float(lon))
    box = None
    envelope = area.get("envelope") or {}
    low, high = envelope.get("lowerCorner") or {}, envelope.get("upperCorner") or {}
    try:
        south, west = bng_to_wgs84(float(low["x"]), float(low["y"]))
        north, east = bng_to_wgs84(float(high["x"]), float(high["y"]))
        # the grid is not aligned with meridians: take the other two corners too
        s2, w2 = bng_to_wgs84(float(low["x"]), float(high["y"]))
        n2, e2 = bng_to_wgs84(float(high["x"]), float(low["y"]))
        box = (
            round(min(west, w2), 4),
            round(min(south, n2), 4),
            round(max(east, e2), 4),
            round(max(north, s2), 4),
        )
    except (KeyError, TypeError, ValueError):
        box = None
    return centre, box


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=LONDON)


class UkEaFloodSource(Source):
    name = "ukea"
    kind = "poll"

    def __init__(self, poll_seconds: float = 300.0, url: str = URL, max_level: int = 2):
        super().__init__()
        # the list is regenerated every 15 min (API reference) behind a 300 s cache
        self.poll_seconds = poll_seconds
        self.url = url
        # Published down to this level (1 = most severe). 2 keeps Flood
        # Warnings and Severe Flood Warnings: "flooding is expected,
        # immediate action required" -- the orange-and-above rule Meteoalarm,
        # NWS and the WMO aggregate already follow. A Flood Alert (3,
        # "flooding is possible, be prepared") is the yellow tier: 35 of the
        # 40 in force on 2026-10-27. `SOS_UKEA_MAX_LEVEL=3` publishes them.
        self.max_level = max_level
        # flood area id -> (centroid, box); areas are static
        self.areas: dict[str, tuple[tuple[float, float] | None, Any]] = {}

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for item in (data or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            area_id = str(item.get("floodAreaID") or "").strip()
            if not area_id:
                continue
            event_id = f"ukea:{area_id}"
            level = item.get("severityLevel")
            if level == ENDED:
                self.lifts.append(event_id)
                continue
            if level not in LEVELS:
                log.warning("ukea: unknown severityLevel %r for %s", level, area_id)
                continue
            if level > self.max_level:
                # below the threshold now, possibly downgraded from above it
                self.expirations.append(event_id)
                continue
            label, severity = LEVELS[level]
            time = _time(item.get("timeRaised")) or _time(item.get("timeMessageChanged"))
            if time is None:
                continue
            centre, box = self.areas.get(area_id, (None, None))
            area = item.get("floodArea") or {}
            name = (item.get("description") or area.get("notation") or area_id).strip()
            county = (area.get("county") or "").strip()
            place = f"{name}, {county}" if county and county not in name else name
            events.append(
                Event(
                    id=event_id,
                    source="ukea",
                    source_id=area_id,
                    kind=Kind.FLOOD,
                    time=time,
                    updated_at=_time(item.get("timeMessageChanged")),
                    lat=centre[0] if centre else None,
                    lon=centre[1] if centre else None,
                    bbox=box,
                    place=place,
                    country="United Kingdom",
                    country_code="GB",
                    severity=severity,
                    ongoing=True,
                    alert=label,
                    title=f"{label.capitalize()} -- {name}",
                    description=item.get("message"),
                    url=PAGE_URL,
                    raw={
                        "severity_level": level,
                        "tidal": item.get("isTidal"),
                        "river_or_sea": area.get("riverOrSea"),
                        "ea_area": item.get("eaAreaName"),
                    },
                )
            )
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def _learn_areas(self, client: httpx.AsyncClient, data: Any) -> None:
        wanted = [
            str(item.get("floodAreaID"))
            for item in (data or {}).get("items") or []
            if isinstance(item, dict)
            and item.get("severityLevel") in LEVELS
            and item["severityLevel"] <= self.max_level
            and item.get("floodAreaID")
            and str(item.get("floodAreaID")) not in self.areas
        ][:AREAS_PER_CYCLE]
        failures = 0
        for area_id in wanted:
            try:
                resp = await client.get(AREA_URL.format(area=area_id))
                resp.raise_for_status()
                self.areas[area_id] = area_position((resp.json() or {}).get("items") or {})
            except Exception as exc:  # noqa: BLE001 -- the alert goes out unplaced
                failures += 1
                log.warning("ukea: flood area %s unavailable (%s)", area_id, exc)
                self.health.feed_failed("flood-areas", exc)
        if wanted and not failures:
            self.health.feed_ok("flood-areas")

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=45.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    data = resp.json()
                    await self._learn_areas(client, data)
                    events = self.parse_payload(data)
                    for event in events:
                        await emit(event)
                    await self.flush_retractions()
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)
