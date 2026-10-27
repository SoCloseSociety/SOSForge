"""The place report: `GET /api/place?lat=&lon=` (see `report`).

For one point the reader searched or clicked -- never the device position:
the page promises "your location never leaves your device", and this
report sends the point to about fifteen third parties -- 22 blocks, each
from exactly one dataset, each with that dataset's own `as_of`, its source
and its licence, or the real reason it has no value.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.place import air, alerts, compose, cyclones, fetch, geo, history, risk, sea, weather
from app.place import block as B
from app.place.compose import Report
from app.place.context import Context
from app.place.fetch import Refused
from app.place.messages import LANGS

PARAMS = ("lat", "lon", "radius_km", "lang")
RADIUS_MIN, RADIUS_MAX, RADIUS_DEFAULT = 10.0, 300.0, 300.0

__all__ = ["PARAMS", "Query", "QueryError", "Report", "configure", "parse_query", "report"]


class QueryError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Query:
    lat: float
    lon: float
    radius_km: float
    lang: str


def _finite(text: str | None, name: str) -> float:
    try:
        value = float(text) if text is not None else math.nan
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise QueryError("bad_point", f"{name} must be a finite number")
    return value


def parse_query(params: Any) -> Query:
    """Strict, as on `/ws`: FastAPI ignores extra parameters, so the route
    hands its raw `request.query_params` here. An unknown parameter is a
    typo, and a parameter given twice is ambiguous: both refused."""
    keys = list(params.keys())
    unknown = sorted(set(keys) - set(PARAMS))
    if unknown:
        raise QueryError(
            "unknown_param",
            f"unknown parameter(s): {', '.join(unknown)} (accepted: {', '.join(PARAMS)})",
        )
    for key in set(keys):
        if len(params.getlist(key)) > 1:
            raise QueryError("unknown_param", f"{key} given more than once")
    lat = _finite(params.get("lat"), "lat")
    lon = _finite(params.get("lon"), "lon")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise QueryError("bad_point", "lat must be within -90..90 and lon within -180..180")
    radius = RADIUS_DEFAULT
    if params.get("radius_km") is not None:
        try:
            radius = float(params.get("radius_km"))
        except ValueError:
            radius = math.nan
        if not math.isfinite(radius) or not RADIUS_MIN <= radius <= RADIUS_MAX:
            raise QueryError(
                "bad_radius", f"radius_km must be within {RADIUS_MIN:g}..{RADIUS_MAX:g}"
            )
    lang = params.get("lang") or "en"
    if lang not in LANGS:
        raise QueryError("bad_lang", f"lang is one of {', '.join(LANGS)}")
    return Query(lat, lon, radius, lang)


def builders(provider: alerts.AlertsProvider) -> list[tuple[B.Spec, compose.Builder]]:
    async def alerts_now(ctx: Context) -> B.Block:
        return alerts.build(ctx, provider)

    return [
        (weather.SPEC_NOW, weather.build_now),
        (weather.SPEC_HOURLY, weather.build_hourly),
        (weather.SPEC_DAILY, weather.build_daily),
        (weather.SPEC_WIND, weather.build_wind),
        (sea.SPEC_WAVES, sea.build_waves),
        (sea.SPEC_LEVEL, sea.build_level),
        (sea.SPEC_SST, sea.build_sst),
        (sea.SPEC_BUOY, sea.build_buoy),
        (air.SPEC_AIR, air.build_air),
        (air.SPEC_POLLEN, air.build_pollen),
        (risk.SPEC_DROUGHT, risk.build_drought),
        (risk.SPEC_HOTSPOTS, risk.build_hotspots),
        (risk.SPEC_DANGER, risk.build_danger),
        (risk.SPEC_WATER, risk.build_water),
        (risk.SPEC_COUNTRY, risk.build_country),
        (history.SPEC_EQ, history.build_earthquakes),
        (history.SPEC_SIG, history.build_significant),
        (history.SPEC_TS, history.build_tsunamis),
        (history.SPEC_ERUPT, history.build_eruptions),
        (history.SPEC_VOLC, history.build_volcanoes),
        (cyclones.SPEC, cyclones.build),
        (alerts.SPEC, alerts_now),
    ]


BLOCK_IDS = tuple(spec.id for spec, _ in builders(lambda: ([], [])))


def configure(
    *, data_dir: Path, max_upstream: int, cache_entries: int, history_cache_mb: float
) -> None:
    fetch.configure(max_upstream)
    for cache in fetch.Cache.registry:
        if cache.scalable:
            cache.max_entries = cache_entries
    history.configure(history_cache_mb)
    cyclones.configure(data_dir)


async def report(
    query: Query,
    provider: alerts.AlertsProvider,
    *,
    budget_s: float,
    take_token: Callable[[], float] | None = None,
    now: datetime | None = None,
) -> Report:
    lat, lon = geo.round_point(query.lat, query.lon)
    place = await fetch.in_thread(geo.place_of, lat, lon)
    ctx = Context(
        lat=lat,
        lon=lon,
        radius_km=query.radius_km,
        lang=query.lang,
        now=now or datetime.now(UTC),
        place=place,
        take_token=take_token,
    )
    try:
        blocks, complete = await compose.run(ctx, builders(provider), budget_s)
    except Refused as refused:
        return Report(None, None, refused=True, retry_after=refused.retry_after)
    body = compose.body(ctx, blocks, complete)
    return Report(body, compose.etag(body))
