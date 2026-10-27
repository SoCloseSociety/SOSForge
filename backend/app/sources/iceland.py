"""Icelandic Meteorological Office (Veðurstofa Íslands) -- api.vedur.is

Why it exists here. Icelandic fissure eruptions announce themselves with
swarms of small earthquakes, and that swarm is the warning this product could
not see: over the 80 hours to 2026-10-26 19:00 UTC, EMSC carried **zero**
events in the Iceland box (62-68 N, 27-12 W) while IMO published **31** at M1
and above -- 71 over the week, and 149 of every size in 48 h. And not one
Icelandic volcano alert level reached the feed, while the office had
Reykjanes at orange.

Two feeds, one host, CC BY 4.0 (`info.license` of the OpenAPI document; the
office's terms add "no restrictions on the use of IMO data, neither for
private nor commercial use", with attribution and download date).

Traps verified against the real payloads (captured 2026-10-26):

- **The old API is gone.** `POST api.vedur.is/skjalftalisa/v1/quake/array`,
  which most open-source Iceland quake clients still call, answers 404. The
  live service is `GET /quakes/events`, documented at
  `/quakes/openapi.json` (version 2026-08-06).
- **Magnitudes go negative** (down to -1.38) and come with six decimals
  (`0.618056`). The API documents them as moment magnitude. Rounded to one
  decimal like every other source here.
- **`type` is NOT the closed vocabulary the OpenAPI says** (earthquake,
  explosion, not locatable, not existing, other): the live listing also
  carries `"anthropogenic event"`, and `null` on fresh automatic events not
  yet typed (23 of 149). Only `earthquake` and `null` go out as earthquakes;
  anything else is `OTHER`, never asserted to be a quake.
- **Deleted events stay in the listing.** "not existing" is an automatic
  detection an analyst has since DELETED, and the default query still returns
  it (2 of 14 events on 2026-09-30/10-01). It is the GeoNet "deleted" trap
  again, and it is withdrawn, not published. "not locatable" has coordinates in the payload, but the
  office is saying they mean nothing: lesson 15, no position rather than a
  wrong one -- and an unplaceable micro-quake is not worth a row, so it is
  skipped.
- **`evaluation_mode`** is manual or automatic (123 / 26). Automatic is a
  preliminary solution and is labelled as such.
- **Offshore is common.** 25 of the 71 M1+ events of the week sit on
  "Reykjaneshryggur", the Reykjanes RIDGE, out at sea; "Iceland Region" is
  IMO's own name for events further out still. So the source stamps no
  country (lesson 12): the pipeline resolves it from the place text, exactly
  as it does for EMSC's Flynn regions, and an Icelandic place name it does
  not know gets a globe, never a guessed flag.
- **Slow.** 1.5 to 10 s per call, and the first attempt of the week-long query
  was reset. The window is 24 h, the poll 120 s, the timeout wide.

The volcano feed (`/volcanoes/volcanoes`) gives, per volcano, the GROUND alert
level (`vals`: 0 Green "no unrest" ... 2 Orange "heightened volcanic unrest")
and the AVIATION colour code (`vona`). Of 34 volcanoes (33 in Iceland, plus
Beerenberg on Jan Mayen, which IMO also watches and which is Norwegian), three
were above Green on 2026-10-26: Reykjanes orange since 2025-10-15,
Bárðarbunga and Ljósufjöll yellow. Level 0 is the permanent state of most of
them and is not published, the same rule as GeoNet's level 0.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.models.event import Event, Kind, Severity, severity_for_quake, to_utc
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
QUAKES_URL = "https://api.vedur.is/quakes/events"
VOLCANOES_URL = "https://api.vedur.is/volcanoes/volcanoes"
# IMO's public quake map; there is no stable per-event page.
QUAKE_PAGE_URL = "https://en.vedur.is/earthquakes-and-volcanism/earthquakes/"
VOLCANO_PAGE_URL = "https://en.vedur.is/earthquakes-and-volcanism/volcanoes/"

# Types published as an earthquake. `None` is a fresh automatic event that no
# analyst has typed yet: every one seen was an ordinary quake.
QUAKE_TYPES = {None, "earthquake"}
# Withdrawn: an analyst deleted the automatic detection.
DELETED_TYPE = "not existing"
# Skipped: the office says the position is meaningless.
UNLOCATABLE_TYPE = "not locatable"

# Ground alert level (`vals`), as the office names it in `code_en`.
VOLCANO_SEVERITY = {
    "yellow": Severity.MODERATE,
    "orange": Severity.SEVERE,
    "red": Severity.EXTREME,
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


class ImoQuakeSource(Source):
    """Icelandic earthquakes, M >= `min_magnitude`, over a sliding window."""

    name = "imo"
    kind = "poll"

    def __init__(
        self,
        poll_seconds: float = 120.0,
        min_magnitude: float = 1.0,
        window_hours: float = 24.0,
        url: str = QUAKES_URL,
    ):
        super().__init__()
        self.poll_seconds = poll_seconds
        # Below M1 nobody feels anything, and the feed would carry ~75 rows a
        # day of micro-seismicity. M1+ is ~10 a day: enough for a Reykjanes
        # swarm to show, few enough not to drown the rest of the world.
        self.min_magnitude = min_magnitude
        self.window_hours = window_hours
        self.url = url

    def build_url(self, now: datetime | None = None) -> str:
        start = (now or datetime.now(UTC)) - timedelta(hours=self.window_hours)
        params = {
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "size_min": f"{self.min_magnitude:g}",
        }
        return f"{self.url}?{urlencode(params)}"

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for feature in (data or {}).get("features") or []:
            try:
                event = self.parse_feature(feature)
            except Exception as exc:
                log.warning("imo: unreadable feature (%s)", exc)
                continue
            if event is not None:
                events.append(event)
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    def parse_feature(self, feature: dict) -> Event | None:
        props = feature.get("properties") or {}
        event_id = props.get("event_id")
        if not event_id:
            return None
        event_type = props.get("type")
        if event_type == DELETED_TYPE:
            self.retractions.append(f"imo:{event_id}")
            return None
        if event_type == UNLOCATABLE_TYPE:
            return None

        time = to_utc(props.get("time"))
        if time is None:
            return None

        coords = (feature.get("geometry") or {}).get("coordinates") or []
        lon = _number(coords[0]) if len(coords) > 0 else None
        lat = _number(coords[1]) if len(coords) > 1 else None
        # lesson 15: the model raises on an out-of-range coordinate
        if lat is not None and not -90 <= lat <= 90:
            lat = None
        if lon is not None and not -180 <= lon <= 180:
            lon = None

        magnitude = _rounded(_number(props.get("magnitude")))
        depth = _rounded(_number(props.get("depth")))
        region = (props.get("region") or "").strip() or "Iceland"
        is_quake = event_type in QUAKE_TYPES
        automatic = props.get("evaluation_mode") == "automatic"

        return Event(
            id=f"imo:{event_id}",
            source="imo",
            source_id=str(event_id),
            # an explosion or an "other" is not asserted to be an earthquake
            kind=Kind.EARTHQUAKE if is_quake else Kind.OTHER,
            time=time,
            updated_at=to_utc(props.get("updated_time")),
            lat=lat,
            lon=lon,
            depth_km=depth,
            magnitude=magnitude,
            mag_type="Mw",
            place=region,
            region="Iceland",
            severity=severity_for_quake(magnitude, depth),
            preliminary=automatic,
            alert="automatic" if automatic else None,
            title=(
                f"M {magnitude} -- {region}, Iceland"
                if magnitude is not None
                else f"{region}, Iceland"
            ),
            url=QUAKE_PAGE_URL,
            raw={"type": event_type, "evaluation_mode": props.get("evaluation_mode")},
        )

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=45.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.build_url())
                    resp.raise_for_status()
                    events = self.parse_payload(resp.json())
                    for event in events:
                        await emit(event)
                    await self.flush_retractions()
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)


class ImoVolcanoSource(Source):
    """Ground alert level of every volcano IMO watches, above Green only."""

    name = "imo-volcano"
    kind = "poll"

    def __init__(self, poll_seconds: float = 600.0, url: str = VOLCANOES_URL):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for row in data or []:
            if not isinstance(row, dict):
                continue
            code = row.get("code")
            current = (row.get("vals") or {}).get("current") or {}
            level = current.get("alert_level_info") or {}
            colour = str(level.get("code_en") or "").strip().lower()
            severity = VOLCANO_SEVERITY.get(colour)
            if not code or severity is None:
                # Green (no unrest), or no level at all: Beerenberg carries
                # none. Lowered back to Green, a volcano simply stops being
                # published, and the silence sweep removes it -- the same exit
                # as HANS and GeoNet.
                continue
            time = to_utc(current.get("published_at"))
            if time is None:
                continue

            point = (row.get("geom_point") or {}).get("coordinates") or []
            lon = _number(point[0]) if len(point) > 0 else None
            lat = _number(point[1]) if len(point) > 1 else None
            name = row.get("name") or code
            aviation = ((row.get("vona") or {}).get("current") or {}).get("aviation_color") or {}
            aviation_code = aviation.get("code")
            country = (row.get("country") or {}).get("name_en")
            description = level.get("description_en")

            events.append(
                Event(
                    id=f"imo-volcano:{code}",
                    source="imo-volcano",
                    source_id=str(code),
                    kind=Kind.VOLCANO,
                    time=time,
                    lat=lat,
                    lon=lon,
                    place=name,
                    # the office states the country of each volcano itself
                    # (Beerenberg is NO), so this is not a guess
                    country=country,
                    severity=severity,
                    ongoing=True,
                    alert=f"{colour} ({description})" if description else colour,
                    title=f"{name} -- volcanic alert {colour.upper()}"
                    + (f", aviation {aviation_code}" if aviation_code else ""),
                    url=VOLCANO_PAGE_URL,
                    raw={
                        "level": level.get("level"),
                        "level_colour": colour,
                        "description": description,
                        "aviation_colour_code": aviation_code,
                        "smithsonian": row.get("si_code"),
                    },
                )
            )
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=45.0, headers=headers, follow_redirects=True
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
