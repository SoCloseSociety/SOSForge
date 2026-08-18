"""Volcanoes -- USGS HANS (alert levels) + Smithsonian catalog (coordinates).

HANS `getElevatedVolcanoes` lists the US volcanoes currently on alert with
their aviation color code and level, but **without coordinates**. The
Smithsonian (GVP) Holocene catalog provides lat/lon by `Volcano_Number`, which
is the same key as HANS's `vnum`. The catalog is loaded once at startup and
joined on.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from app.models.event import Event, Kind, Severity
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

HANS_URL = "https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes"
GVP_URL = (
    "https://webservices.volcano.si.edu/geoserver/GVP-VOTW/ows"
    "?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=GVP-VOTW:Smithsonian_VOTW_Holocene_Volcanoes"
    "&outputFormat=application/json"
    "&propertyName=Volcano_Number,Volcano_Name,Latitude,Longitude,Country"
)

# aviation color code: GREEN normal, YELLOW unrest, ORANGE likely or minor
# eruption, RED major eruption ongoing or imminent
COLOR_SEVERITY = {
    "GREEN": Severity.INFO,
    "YELLOW": Severity.MODERATE,
    "ORANGE": Severity.SEVERE,
    "RED": Severity.EXTREME,
    "UNASSIGNED": Severity.INFO,
}


def _parse_sent(value: str | None) -> datetime:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


class VolcanoSource(Source):
    name = "volcano"
    kind = "poll"

    def __init__(self, poll_seconds: float = 300.0):
        super().__init__()
        self.poll_seconds = poll_seconds
        self._catalog: dict[str, tuple[float, float, str | None]] = {}

    async def _load_catalog(self, client: httpx.AsyncClient) -> None:
        if self._catalog:
            return
        try:
            resp = await client.get(GVP_URL, timeout=60.0)
            resp.raise_for_status()
            for feature in (resp.json() or {}).get("features") or []:
                props = feature.get("properties") or {}
                vnum = props.get("Volcano_Number")
                lat, lon = props.get("Latitude"), props.get("Longitude")
                if vnum is None or lat is None or lon is None:
                    continue
                self._catalog[str(vnum)] = (float(lat), float(lon), props.get("Country"))
            log.info("volcano catalog loaded: %d entries", len(self._catalog))
        except Exception as exc:
            # without the catalog we lose the coordinates, not the alert itself
            log.warning("volcano catalog unavailable (%s), alerts without position", exc)

    def parse(self, row: dict) -> Event | None:
        vnum = str(row.get("vnum") or "")
        identifier = row.get("notice_identifier") or vnum
        if not identifier:
            return None

        color = (row.get("color_code") or "UNASSIGNED").upper()
        level = (row.get("alert_level") or "").upper()
        name = row.get("volcano_name") or "unknown volcano"
        lat, lon, country = self._catalog.get(vnum, (None, None, None))

        return Event(
            # Key = the VOLCANO, not the bulletin. Each new bulletin for the
            # same volcano must update the existing entry: otherwise bulletins
            # piled up as stacked markers, and since a volcanic alert is
            # "ongoing" (never cut off by the horizon), the old ones never
            # left.
            id=f"volcano:{vnum or identifier}",
            source="volcano",
            source_id=vnum or identifier,
            kind=Kind.VOLCANO,
            time=_parse_sent(row.get("sent_utc")),
            lat=lat,
            lon=lon,
            place=name,
            country=country,
            severity=COLOR_SEVERITY.get(color, Severity.INFO),
            # getElevatedVolcanoes only returns volcanoes currently on alert
            ongoing=True,
            alert=color.lower(),
            title=f"{name} -- alert {level or color} ({row.get('obs_abbr', '').upper()})",
            url=row.get("notice_url"),
            raw={
                "notice": identifier,
                "color_code": color,
                "alert_level": level,
                "observatory": row.get("obs_fullname"),
                "notice_type": row.get("notice_type_cd"),
            },
        )

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": "SOSForge/1.0 (+https://soclose.co)"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            await self._load_catalog(client)
            while True:
                try:
                    resp = await client.get(HANS_URL)
                    resp.raise_for_status()
                    rows = resp.json() or []
                    for row in rows:
                        event = self.parse(row)
                        if event:
                            await emit(event)
                    self.health.ok(len(rows))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("volcanoes: %s", exc)
                await asyncio.sleep(self.poll_seconds)
