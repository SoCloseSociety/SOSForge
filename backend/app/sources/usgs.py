"""USGS -- worldwide earthquake GeoJSON feed.

The all_hour.geojson file is regenerated continuously (metadata.generated
moves every few seconds). We poll it every 5s: the real latency between USGS
detection and publication is on the order of a minute, so polling is not the
limiting factor.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.event import Event, Kind, severity_for_quake
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)


def _ts(ms: int | float | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def parse_feature(feature: dict) -> Event | None:
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    # A GeoJSON position is [lon, lat] with an OPTIONAL third element. Assuming
    # three would put the depth at None on any feed that omits it, and indexing
    # blindly would raise.
    coords = list(geom.get("coordinates") or [])
    coords += [None] * (3 - len(coords))
    time = _ts(props.get("time"))
    if time is None:
        return None

    mag = props.get("mag")
    # USGS documents this field as: "set to 1 for large events in oceanic
    # regions... The existence or value of this flag does not indicate if a
    # tsunami actually did or will exist."
    #
    # We read it as a tsunami. It forced the severity to EXTREME, lit the
    # full-width TSUNAMI ALERT banner in five languages, sounded the alarm and
    # counted into the tsunami KPI -- for any large oceanic quake, sometimes
    # while PTWC was publishing "no tsunami danger" about the same event. Only
    # a warning centre declares a tsunami, and `sources/tsunami.py` already
    # ingests them properly. The flag stays as what it is: a hint that a
    # bulletin may follow.
    tsunami_flag = bool(props.get("tsunami"))
    place = props.get("place") or "unknown location"

    return Event(
        id=f"usgs:{feature.get('id')}",
        source="usgs",
        source_id=str(feature.get("id")),
        kind=Kind.EARTHQUAKE,
        time=time,
        updated_at=_ts(props.get("updated")),
        lat=coords[1],
        lon=coords[0],
        depth_km=coords[2],
        magnitude=mag,
        mag_type=props.get("magType"),
        place=place,
        severity=severity_for_quake(mag, coords[2], props.get("alert")),
        # NOT the USGS flag: see above. Only a warning centre declares one.
        tsunami=False,
        # "automatic" until a geophysicist has looked at it.
        preliminary=(props.get("status") or "").strip().lower() != "reviewed",
        intensity_mmi=_number(props.get("mmi")),
        # `cdi` is the intensity people REPORTED (DYFI), `mmi` the intensity
        # the ground-motion model ESTIMATED. Same scale, routinely different
        # answers, and both are worth showing: the modelled one is there in
        # seconds and is a guess, the reported one takes minutes and is what
        # actually happened to somebody.
        intensity_cdi=_number(props.get("cdi")),
        felt_reports=_int(props.get("felt")),
        alert=props.get("alert"),
        title=props.get("title") or f"M {mag} -- {place}",
        url=props.get("url"),
        raw={
            **feature,
            # kept, because it IS information -- an oceanic event large enough
            # that a centre may speak. It is simply not a tsunami.
            "tsunami_flag": tsunami_flag,
        },
    )


def _number(value: Any) -> float | None:
    """USGS omits `mmi`, `cdi` and `felt` far more often than it sends them:
    absent must stay absent, never become a zero we would then display as a
    fact ("nobody felt it")."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class UsgsSource(Source):
    name = "usgs"
    kind = "poll"

    def __init__(self, url: str, poll_seconds: float = 5.0):
        super().__init__()
        self.url = url
        self.poll_seconds = poll_seconds
        self._last_generated: int | None = None

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": "SOSForge/1.0 (+https://soclose.co)"}
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            while True:
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    data = resp.json()
                    generated = (data.get("metadata") or {}).get("generated")
                    if generated != self._last_generated:
                        features = data.get("features") or []
                        kept = 0
                        for feature in features:
                            # per-feature isolation: one malformed record must
                            # never cost the rest of the batch
                            try:
                                event = parse_feature(feature)
                            except Exception as exc:
                                log.warning("usgs: unusable feature skipped (%s)", exc)
                                continue
                            if event:
                                kept += 1
                                await emit(event)
                        # the cursor moves only once the batch is through: a
                        # crash mid-batch used to skip everything after it,
                        # permanently
                        self._last_generated = generated
                        self.health.ok(kept)
                    else:
                        self.health.ok()
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("usgs poll: %s", exc)
                await asyncio.sleep(self.poll_seconds)


async def backfill(url: str, emit: Emit) -> int:
    """Loads recent history at startup so we don't show an empty map."""
    headers = {"User-Agent": "SOSForge/1.0 (+https://soclose.co)"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        features = (resp.json() or {}).get("features") or []
    features.sort(key=lambda f: (f.get("properties") or {}).get("time") or 0)
    count = 0
    for feature in features:
        event = parse_feature(feature)
        if event:
            await emit(event)
            count += 1
    return count
