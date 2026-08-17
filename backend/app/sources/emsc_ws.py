"""EMSC seismicportal -- websocket push.

C'est la source qui rend le tracker vraiment "live": l'EMSC pousse le seisme des
qu'il est localise, sans attendre un cycle de polling. Le message porte une action
(`create` / `update`) car l'EMSC revise ses solutions dans les minutes qui suivent.
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
    """Normalise un message websocket EMSC.

    Enveloppe: {"action": "create|update", "data": {<Feature GeoJSON>}}
    Le Feature porte `properties` (lat/lon/depth/mag/magtype/time/flynn_region/unid)
    et `geometry.coordinates` = [lon, lat, -depth_km].
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
    region = props.get("flynn_region") or props.get("region") or "region inconnue"

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
        # ping_interval force la detection d'une connexion morte: sans ca un
        # websocket peut rester "ouvert" des heures sans plus rien livrer.
        async with websockets.connect(
            self.url, ping_interval=20, ping_timeout=20, close_timeout=5, max_queue=256
        ) as ws:
            self.health.ok()
            log.info("emsc websocket connecte")
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
            raise ConnectionError("emsc websocket ferme par le serveur")

    async def supervise(self, emit: Emit) -> None:
        delay = 1.0
        while True:
            try:
                await self.run(emit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health.fail(exc)
                log.warning("emsc ws deconnecte (%s), reconnexion dans %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            else:
                delay = 1.0
