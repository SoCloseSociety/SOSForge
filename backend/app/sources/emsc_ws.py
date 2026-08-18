"""EMSC seismicportal -- websocket push.

This is the source that makes the tracker truly "live": EMSC pushes the quake
as soon as it is located, without waiting for a polling cycle. The message
carries an action (`create` / `update`) because EMSC revises its solutions in
the following minutes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import websockets

from app.models.event import Event, Kind, severity_from_magnitude
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_message(payload: dict) -> Event | None:
    """Normalizes an EMSC websocket message.

    Envelope: {"action": "create|update", "data": {<GeoJSON Feature>}}
    The Feature carries `properties` (lat/lon/depth/mag/magtype/time/flynn_region/unid)
    and `geometry.coordinates` = [lon, lat, -depth_km].
    """
    data = payload.get("data") or payload
    props = data.get("properties") or {}
    geom = data.get("geometry") or {}
    coords = geom.get("coordinates") or []

    unid = props.get("unid") or props.get("source_id") or data.get("id")
    if not unid:
        return None

    time = _parse_time(props.get("time"))
    if time is None:
        return None

    lon = props.get("lon")
    lat = props.get("lat")
    if lat is None and len(coords) >= 2:
        lon, lat = coords[0], coords[1]

    depth = props.get("depth")
    if depth is None and len(coords) >= 3:
        depth = abs(coords[2])

    mag = props.get("mag")
    region = props.get("flynn_region") or props.get("region") or "unknown region"

    return Event(
        id=f"emsc:{unid}",
        source="emsc",
        source_id=str(unid),
        kind=Kind.EARTHQUAKE,
        time=time,
        updated_at=_parse_time(props.get("lastupdate")),
        lat=lat,
        lon=lon,
        depth_km=abs(depth) if depth is not None else None,
        magnitude=mag,
        mag_type=props.get("magtype"),
        place=region,
        region=region,
        severity=severity_from_magnitude(mag),
        title=f"M {mag} -- {region}" if mag is not None else region,
        url=f"https://www.seismicportal.eu/eventdetails.html?unid={unid}",
        raw=payload,
    )


class EmscWebsocketSource(Source):
    name = "emsc"
    kind = "push"

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    async def run(self, emit: Emit) -> None:
        # ping_interval forces dead-connection detection: without it a
        # websocket can stay "open" for hours while delivering nothing.
        async with websockets.connect(
            self.url, ping_interval=20, ping_timeout=20, close_timeout=5, max_queue=256
        ) as ws:
            self.health.ok()
            log.info("emsc websocket connected")
            async for raw in ws:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event = parse_message(payload)
                if event is None:
                    continue
                self.health.ok(1)
                await emit(event)
            raise ConnectionError("emsc websocket closed by the server")

    async def supervise(self, emit: Emit) -> None:
        delay = 1.0
        while True:
            try:
                await self.run(emit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health.fail(exc)
                log.warning("emsc ws disconnected (%s), reconnecting in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            else:
                delay = 1.0
