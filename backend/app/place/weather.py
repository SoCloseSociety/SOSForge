"""`weather_now`, `forecast_hourly`, `forecast_daily`, `wind`.

- `weather_now`: the nearest METAR station of 50 km or less whose report is
  3 h old or less, from AWC's global cache file (one read every 5 minutes
  serves every place). The file holds only its last 70 to 80 minutes of
  reports (measured 15:24-16:32 at 16:34Z): read alone, the 3 h window
  could never be reached, and Ko Samui's VTSM, 7 km away and reporting
  hourly, was "no station" 90 minutes after its 15:00Z report. So every
  read is MERGED into the reports already held (newest per station, none
  past 3 h), and the index says since when its cover is unbroken: after a
  restart, "no station" names that hour instead of claiming 3 h. Wind,
  gust, visibility and QNH from the raw report (`metar.py` says why the CSV
  columns are lossy).
- `forecast_*`: one MET Norway Locationforecast `complete` read per 0.01 deg
  point, revalidated with If-Modified-Since, fresh until MET's `Expires`.
  MET's first step can be the PAST hour (10:00Z in a body fetched at
  10:36Z): dropped. The daily view is ours, from the 6-hour blocks. The
  cache holds only the fields the blocks read (`slim_met`: 197 KB in memory
  where the full parse takes 259 KB, measured on four captures), counted
  by what it takes in memory and bounded at 64 MB (about 330 points): by
  count alone, 4096 clicked points were about 1.1 GB.
- `wind`: NWS gridpoint data for the US and its territories (`updateTime`,
  never `generatedAt`, which only says when the JSON was rendered), MET
  everywhere else and whenever NWS fails (`data.fallback` says so).
"""

from __future__ import annotations

import csv
import gzip
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from app.place import block as B
from app.place import fetch, metar
from app.place.context import Context
from app.place.geo import haversine_km

METAR_URL = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
NWS_POINTS = "https://api.weather.gov/points/{lat:.2f},{lon:.2f}"
NWS_GRID = "https://api.weather.gov/gridpoints/{office}/{x},{y}"
NWS_COUNTRIES = frozenset({"US", "PR", "VI", "GU", "AS", "MP"})

STATION_KM = 50.0
STATION_MAX_AGE = timedelta(hours=3)
HOURS = 48

SRC_METAR = B.Source(
    "NOAA Aviation Weather Center, METAR",
    "https://aviationweather.gov/data/api/#cache",
    "METAR reports relayed by NOAA/NWS Aviation Weather Center",
    "US Government work, public domain",
)
SRC_MET = B.Source(
    "MET Norway Locationforecast",
    "https://api.met.no/weatherapi/locationforecast/2.0/documentation",
    "Data from The Norwegian Meteorological Institute (MET Norway)",
    "CC BY 4.0 and NLOD 2.0",
)
SRC_NWS = B.Source(
    "NOAA National Weather Service",
    "https://www.weather.gov/documentation/services-web-api",
    "Forecast grid data from NOAA/National Weather Service",
    "US Government work, public domain",
)

SPEC_NOW = B.Spec("weather_now", "observation", "observed", 3 * 3600.0, SRC_METAR)
SPEC_HOURLY = B.Spec("forecast_hourly", "forecast", "issued", 12 * 3600.0, SRC_MET)
SPEC_DAILY = B.Spec("forecast_daily", "forecast", "issued", 12 * 3600.0, SRC_MET)
SPEC_WIND = B.Spec("wind", "forecast", "issued", 12 * 3600.0, SRC_MET)

# the forecast caches are bounded in BYTES too (deep size of what they hold):
# a count alone let 4096 clicked points reach a gigabyte
MET_CACHE_BYTES = 64 * 1024 * 1024
GRID_CACHE_BYTES = 16 * 1024 * 1024

_metar_cache = fetch.Cache("aviationweather.gov", 1)
_met_cache = fetch.Cache("api.met.no", max_bytes=MET_CACHE_BYTES)
_points_cache = fetch.Cache("api.weather.gov points")
_grid_cache = fetch.Cache("api.weather.gov gridpoints", max_bytes=GRID_CACHE_BYTES)


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _expires_ttl(response: Any, floor: float, ceiling: float) -> float:
    try:
        expires = parsedate_to_datetime(response.headers["expires"])
        ttl = (expires - datetime.now(UTC)).total_seconds()
    except (KeyError, TypeError, ValueError):
        ttl = floor
    return max(floor, min(ceiling, ttl))


# -------------------------------------------------------------------- METAR


@dataclass
class Station:
    icao: str
    time: datetime
    lat: float
    lon: float
    elevation_m: float | None
    raw: str
    temp_c: float | None
    dewpoint_c: float | None
    wx: str | None
    clouds: list[dict[str, Any]]
    flight_category: str | None


@dataclass
class MetarIndex:
    cells: dict[tuple[int, int], list[Station]]
    newest: datetime | None
    rows: int
    # every report from here on is held: the oldest one of the first file
    # read, carried across merges while each read overlaps the last
    since: datetime | None = None


def _index(latest: dict[str, Station], rows: int, since: datetime | None) -> MetarIndex:
    cells: dict[tuple[int, int], list[Station]] = defaultdict(list)
    for station in latest.values():
        cells[(math.floor(station.lat), math.floor(station.lon))].append(station)
    newest = max((s.time for s in latest.values()), default=None)
    return MetarIndex(dict(cells), newest, rows, since)


def merge_metar(held: MetarIndex | None, fresh: MetarIndex) -> MetarIndex:
    """The fresh file over the reports already held: the newest report per
    station, none older than STATION_MAX_AGE before the newest one. A
    same-time tie goes to the fresh file: it is the issuer's correction
    (ETAR's 17:10Z SPECI re-issued as COR, 4000 BR -> 9999), and the
    original it retracts is never served as current. A file that does not
    reach back to the last one held (the reads failed for longer than the
    file's window) starts the cover over."""
    if (
        held is None
        or held.newest is None
        or fresh.newest is None
        or fresh.since is None
        or held.newest < fresh.since
    ):
        return fresh
    horizon = fresh.newest - STATION_MAX_AGE
    latest = {s.icao: s for cell in held.cells.values() for s in cell if s.time >= horizon}
    for cell in fresh.cells.values():
        for station in cell:
            kept = latest.get(station.icao)
            if kept is None or station.time >= kept.time:
                latest[station.icao] = station
    since = max(held.since or fresh.since, horizon)
    return _index(latest, fresh.rows, since)


def parse_metar_cache(gz: bytes) -> MetarIndex:
    """Positional, not by name: `sky_cover` and `cloud_base_ft_agl` repeat
    four times in the header."""
    text = gzip.decompress(gz).decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    col = {name: i for i, name in reversed(list(enumerate(header)))}  # first occurrence
    sky = [i for i, name in enumerate(header) if name == "sky_cover"]
    base = [i for i, name in enumerate(header) if name == "cloud_base_ft_agl"]
    latest: dict[str, Station] = {}
    rows = 0
    for row in reader:
        if len(row) < len(header):
            continue
        rows += 1
        lat, lon = B.number(row[col["latitude"]]), B.number(row[col["longitude"]])
        raw = row[col["raw_text"]].strip()
        try:
            when = parse_time(row[col["observation_time"]])
        except ValueError:
            continue
        if lat is None or lon is None or not raw:
            continue
        clouds = []
        for s, b in zip(sky, base, strict=True):
            cover = row[s].strip()
            if cover:
                height = B.number(row[b])
                clouds.append(
                    {"cover": cover, "base_ft": int(height) if height is not None else None}
                )
        station = Station(
            icao=row[col["station_id"]].strip(),
            time=when,
            lat=lat,
            lon=lon,
            elevation_m=B.number(row[col["elevation_m"]]),
            raw=raw,
            temp_c=B.number(row[col["temp_c"]]),
            dewpoint_c=B.number(row[col["dewpoint_c"]]),
            wx=row[col["wx_string"]].strip() or None,
            clouds=clouds,
            flight_category=row[col["flight_category"]].strip() or None,
        )
        held = latest.get(station.icao)
        if held is None or station.time > held.time:
            latest[station.icao] = station
    return _index(latest, rows, min((s.time for s in latest.values()), default=None))


async def _load_metar(previous: fetch.Entry) -> fetch.Loaded:
    response = await fetch.get(METAR_URL)
    index = await fetch.in_thread(parse_metar_cache, response.content)
    if not index.rows:
        raise fetch.FetchError("upstream_parse", "the aviationweather.gov cache file has no row")
    held = previous.value if isinstance(previous.value, MetarIndex) else None
    return fetch.Loaded(merge_metar(held, index), 300.0, size=len(response.content))


def rh_pct(temp: float, dew: float) -> int:
    """Magnus formula (Alduchov and Eskridge coefficients)."""

    def es(t: float) -> float:
        return 6.1094 * math.exp(17.625 * t / (t + 243.04))

    return round(100 * es(dew) / es(temp))


def nearest_station(
    index: MetarIndex, lat: float, lon: float, now: datetime
) -> tuple[Station, float] | None:
    best: tuple[Station, float] | None = None
    span = 1 + int(STATION_KM / (111.32 * max(0.05, math.cos(math.radians(lat)))))
    for dy in (-1, 0, 1):
        for dx in range(-span, span + 1):
            cell = (math.floor(lat) + dy, ((math.floor(lon) + dx + 180) % 360) - 180)
            for station in index.cells.get(cell, ()):
                if now - station.time > STATION_MAX_AGE:
                    continue
                km = haversine_km(lat, lon, station.lat, station.lon)
                if km <= STATION_KM and (best is None or km < best[1]):
                    best = (station, km)
    return best


def now_block(index: MetarIndex, ctx: Context) -> B.Block:
    found = nearest_station(index, ctx.lat, ctx.lon, ctx.now)
    if found is None and index.since is not None and ctx.now - index.since < STATION_MAX_AGE:
        since = index.since.strftime("%H:%M")
        return B.unavailable(
            SPEC_NOW,
            "no_station",
            f"no METAR within {STATION_KM:g} km reported since {since} UTC",
            km=int(STATION_KM),
            since=since,
        )
    if found is None:
        return B.unavailable(
            SPEC_NOW,
            "no_station",
            f"no METAR within {STATION_KM:g} km reported in the last 3 h",
            km=int(STATION_KM),
            hours=3,
        )
    station, km = found
    groups = metar.parse(station.raw)
    derived = []
    rh = None
    if station.temp_c is not None and station.dewpoint_c is not None:
        rh = rh_pct(station.temp_c, station.dewpoint_c)
        derived.append("rh_pct")
    if groups.qnh_derived:
        derived.append("qnh_hpa")
    data = {
        "station": {
            "icao": station.icao,
            "distance_km": round(km, 1),
            "elevation_m": int(station.elevation_m) if station.elevation_m is not None else None,
        },
        "raw": station.raw,
        "temp_c": station.temp_c,
        "dewpoint_c": station.dewpoint_c,
        "rh_pct": rh,
        "wind_ms": groups.wind_ms,
        "wind_from_deg": groups.wind_from_deg,
        "calm": groups.calm,
        "wind_variable": groups.wind_variable,
        "gust_ms": groups.gust_ms,
        "visibility_km": groups.visibility_km,
        "visibility_at_least": groups.visibility_at_least,
        "qnh_hpa": int(groups.qnh_hpa)
        if groups.qnh_hpa is not None and not groups.qnh_derived
        else groups.qnh_hpa,
        "wx": station.wx,
        "clouds": station.clouds,
        "flight_category": station.flight_category,
        "derived": derived,
    }
    return B.ok(SPEC_NOW, station.time, data)


async def build_now(ctx: Context) -> B.Block:
    entry = await fetch.obtain(
        _metar_cache,
        "global",
        _load_metar,
        ctx.claim,
        swr=True,
        on_first_wait=ctx.waiting(SPEC_NOW.id, "warming"),
        on_stale=ctx.serving_stale(SPEC_NOW.id),
    )
    return serve(SPEC_NOW, entry, lambda index: now_block(index, ctx), ttl_s=300.0)


def serve(
    spec: B.Spec,
    entry: fetch.Entry,
    build: Any,
    *,
    ttl_s: float | None = None,
) -> B.Block:
    """A block from a cache entry, with the failed-refresh rule: the last
    good copy keeps serving, its own `as_of` and the refresh's error with
    it, until that `as_of` is older than 2x `max_age_s` (or, for a static
    dataset, 2x its TTL has passed since it was read). Then `unavailable`
    with the failure's code."""
    if entry.value is None:
        error = entry.error or fetch.FetchError("upstream_parse", f"no value for {spec.id}")
        return B.unavailable(spec, error.code, error.reason)
    result: B.Block = build(entry.value)
    result.fetched_at = entry.fetched_at
    if entry.error is not None:
        now = datetime.now(UTC)
        too_old = False
        if spec.max_age_s is not None and result.as_of is not None:
            too_old = now - result.as_of > timedelta(seconds=2 * spec.max_age_s)
        elif ttl_s is not None and entry.fetched_at is not None:
            too_old = now - entry.fetched_at > timedelta(seconds=2 * ttl_s)
        if too_old:
            return B.unavailable(spec, entry.error.code, entry.error.reason)
        result.error = B.Problem(entry.error.code, entry.error.reason)
    return result


# ---------------------------------------------------------------- MET Norway

# Exactly the keys the blocks below read; `test_slim_payload_builds_the_same_blocks`
# builds every block from the full and the slim payload of four captures.
INSTANT_KEYS = (
    "air_temperature",
    "apparent_air_temperature",
    "relative_humidity",
    "dew_point_temperature",
    "air_pressure_at_sea_level",
    "cloud_area_fraction",
    "fog_area_fraction",
    "ultraviolet_index_clear_sky",
    "wind_speed",
    "wind_from_direction",
    "wind_speed_of_gust",
)
PERIOD_KEYS = {
    "next_1_hours": ("precipitation_amount", "probability_of_precipitation"),
    "next_6_hours": ("air_temperature_max", "air_temperature_min", "precipitation_amount"),
}


def slim_met(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload reduced to what the blocks read, in the same shape (a
    key absent upstream stays absent: "no gust in this model" is read from
    the key, not the value)."""
    props = payload["properties"]
    steps = []
    for step in props["timeseries"]:
        data = step["data"]
        details = data["instant"]["details"]
        out: dict[str, Any] = {
            "instant": {"details": {k: details[k] for k in INSTANT_KEYS if k in details}}
        }
        for period, keys in PERIOD_KEYS.items():
            block = data.get(period)
            if block is None:
                continue
            kept: dict[str, Any] = {}
            if "summary" in block:
                kept["summary"] = {"symbol_code": (block["summary"] or {}).get("symbol_code")}
            if "details" in block:
                d = block["details"] or {}
                kept["details"] = {k: d[k] for k in keys if k in d}
            out[period] = kept
        steps.append({"time": step["time"], "data": out})
    coords = (payload.get("geometry") or {}).get("coordinates")
    return {
        "geometry": {"coordinates": coords} if coords is not None else {},
        "properties": {
            "meta": {"updated_at": props["meta"]["updated_at"]},
            "timeseries": steps,
        },
    }


def _parse_met(content: bytes) -> tuple[dict[str, Any], int]:
    import json

    payload = json.loads(content)
    try:
        props = payload["properties"]
        parse_time(props["meta"]["updated_at"])
        props["timeseries"][0]["data"]["instant"]["details"]
        slim = slim_met(payload)
    except (KeyError, IndexError, TypeError, ValueError):
        raise fetch.FetchError("upstream_parse", "api.met.no payload without timeseries") from None
    return slim, fetch.deep_size(slim)


async def met_entry(ctx: Context) -> fetch.Entry:
    key = (ctx.lat, ctx.lon)
    url = f"{MET_URL}?lat={ctx.lat:.2f}&lon={ctx.lon:.2f}"

    async def load(previous: fetch.Entry) -> fetch.Loaded:
        headers = {}
        if previous.value is not None and previous.hints.get("last-modified"):
            headers["If-Modified-Since"] = previous.hints["last-modified"]
        response = await fetch.get(url, headers=headers, accept=(200, 203))
        ttl = _expires_ttl(response, 600.0, 6 * 3600.0)
        hints = {"last-modified": response.headers.get("last-modified", "")}
        if response.status_code == 304:
            return fetch.Loaded(previous.value, ttl, size=previous.size, hints=hints)
        slim, size = await fetch.in_thread(_parse_met, response.content)
        return fetch.Loaded(slim, ttl, size=size, hints=hints)

    return await fetch.obtain(_met_cache, key, load, ctx.claim)


def _steps(payload: dict[str, Any], now: datetime, hourly: bool) -> list[dict[str, Any]]:
    out = []
    for step in payload["properties"]["timeseries"]:
        when = parse_time(step["time"])
        if when < now:
            # MET's first step can be in the past (10:00Z in a body read at
            # 10:36Z): an instant already gone is not a forecast
            continue
        if hourly and "next_1_hours" not in step["data"]:
            break  # the hourly part has ended
        out.append({**step, "_time": when})
        if hourly and len(out) >= HOURS:
            break
    return out


def _detail(step: dict[str, Any], key: str) -> float | None:
    return B.number(step["data"]["instant"]["details"].get(key))


def hourly_block(payload: dict[str, Any], ctx: Context) -> B.Block:
    steps = _steps(payload, ctx.now, hourly=True)
    with_pop = any(
        "probability_of_precipitation" in (s["data"].get("next_1_hours") or {}).get("details", {})
        for s in payload["properties"]["timeseries"]
    )
    hours = []
    for s in steps:
        n1 = s["data"].get("next_1_hours") or {}
        hour = {
            "time": B.iso(s["_time"]),
            "temp_c": _detail(s, "air_temperature"),
            "feels_like_c": _detail(s, "apparent_air_temperature"),
            "rh_pct": _detail(s, "relative_humidity"),
            "dewpoint_c": _detail(s, "dew_point_temperature"),
            "pressure_hpa": _detail(s, "air_pressure_at_sea_level"),
            "cloud_pct": _detail(s, "cloud_area_fraction"),
            "fog_pct": _detail(s, "fog_area_fraction"),
            "precip_mm": B.number((n1.get("details") or {}).get("precipitation_amount")),
            "symbol": (n1.get("summary") or {}).get("symbol_code"),
            "uv_clear_sky": _detail(s, "ultraviolet_index_clear_sky"),
        }
        if with_pop:
            hour["precip_probability_pct"] = B.number(
                (n1.get("details") or {}).get("probability_of_precipitation")
            )
        hours.append(hour)
    missing = [] if with_pop else [{"field": "precip_probability_pct", "reason": "not_in_source"}]
    coords = payload.get("geometry", {}).get("coordinates") or []
    data = {
        "model_elevation_m": B.number(coords[2]) if len(coords) > 2 else None,
        "hours": hours,
    }
    return B.ok(SPEC_HOURLY, _issued(payload), data, missing=missing)


def _issued(payload: dict[str, Any]) -> datetime:
    return parse_time(payload["properties"]["meta"]["updated_at"])


def day_start_hour(lon: float) -> int:
    """The 6-h step (00/06/12/18 UTC) nearest solar midnight, at lon/15 h
    east of Greenwich; on a tie the earlier step wins."""
    midnight = (-lon / 15.0) % 24.0
    best = None
    for h in (0, 6, 12, 18, 24):
        gap = abs(midnight - h)
        if best is None or gap < best[0] - 1e-9:
            best = (gap, h % 24)
    assert best is not None
    return best[1]


def daily_block(payload: dict[str, Any], ctx: Context) -> B.Block:
    start_hour = day_start_hour(ctx.lon)
    solar = timedelta(hours=ctx.lon / 15.0)
    blocks = []
    for s in _steps(payload, ctx.now, hourly=False):
        when = s["_time"]
        n6 = s["data"].get("next_6_hours")
        if when.hour % 6 or when.minute != 0 or not n6:
            continue
        blocks.append((when, n6))
    days: dict[datetime, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for when, n6 in blocks:
        offset = (when.hour - start_hour) % 24
        day_start = (when - timedelta(hours=offset)).replace(minute=0, second=0, microsecond=0)
        days[day_start].append((when, n6))
    out = []
    for day_start in sorted(days):
        members = days[day_start]
        noon = day_start + timedelta(hours=12)  # solar noon, by construction of day_start
        local_date = (noon + solar).date()
        holding_noon = None
        for when, n6 in members:
            if when <= noon - timedelta(minutes=0) < when + timedelta(hours=6):
                holding_noon = n6
        # the step nearest solar midnight is day_start; solar noon is ~12 h later
        maxes = [
            B.number((n6.get("details") or {}).get("air_temperature_max")) for _, n6 in members
        ]
        mins = [B.number((n6.get("details") or {}).get("air_temperature_min")) for _, n6 in members]
        rain = [
            B.number((n6.get("details") or {}).get("precipitation_amount")) for _, n6 in members
        ]
        maxes_v = [v for v in maxes if v is not None]
        mins_v = [v for v in mins if v is not None]
        rain_v = [v for v in rain if v is not None]
        out.append(
            {
                "date": local_date.isoformat(),
                "start": B.iso(day_start),
                "end": B.iso(day_start + timedelta(hours=24)),
                "temp_max_c": max(maxes_v) if maxes_v else None,
                "temp_min_c": min(mins_v) if mins_v else None,
                "precip_mm": round(sum(rain_v), 1) if len(rain_v) == len(rain) else None,
                "symbol": ((holding_noon or {}).get("summary") or {}).get("symbol_code"),
                "blocks": len(members),
                "complete": len(members) == 4,
            }
        )
    data = {
        "day_basis": "solar_6h",
        "day_start_utc_hour": start_hour,
        "days": out,
        "derived": ["days"],
    }
    return B.ok(SPEC_DAILY, _issued(payload), data)


async def build_hourly(ctx: Context) -> B.Block:
    entry = await met_entry(ctx)
    return serve(SPEC_HOURLY, entry, lambda payload: hourly_block(payload, ctx))


async def build_daily(ctx: Context) -> B.Block:
    entry = await met_entry(ctx)
    return serve(SPEC_DAILY, entry, lambda payload: daily_block(payload, ctx))


# --------------------------------------------------------------------- wind


def met_wind(payload: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], bool]:
    hours = []
    gusts = False
    for s in _steps(payload, ctx.now, hourly=True):
        gust = _detail(s, "wind_speed_of_gust")
        gusts = gusts or "wind_speed_of_gust" in s["data"]["instant"]["details"]
        direction = _detail(s, "wind_from_direction")
        hours.append(
            {
                "time": B.iso(s["_time"]),
                "wind_ms": _detail(s, "wind_speed"),
                "wind_from_deg": round(direction) if direction is not None else None,
                "gust_ms": gust,
            }
        )
    return hours, gusts


def _max48(hours: list[dict[str, Any]]) -> dict[str, Any]:
    winds = [h for h in hours if h["wind_ms"] is not None]
    gusts = [h for h in hours if h["gust_ms"] is not None]
    top = max(winds, key=lambda h: h["wind_ms"]) if winds else None
    top_gust = max(gusts, key=lambda h: h["gust_ms"]) if gusts else None
    return {
        "wind_ms": top["wind_ms"] if top else None,
        "time": top["time"] if top else None,
        "gust_ms": top_gust["gust_ms"] if top_gust else None,
        "gust_time": top_gust["time"] if top_gust else None,
    }


_DURATION = re.compile(r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?)?$")


def interval(valid_time: str) -> tuple[datetime, timedelta]:
    """NWS `validTime`: an ISO 8601 interval, `start/duration`."""
    start_text, _, duration = valid_time.partition("/")
    m = _DURATION.match(duration)
    if not m:
        raise ValueError(f"unreadable duration {duration!r}")
    span = timedelta(days=int(m["d"] or 0), hours=int(m["h"] or 0), minutes=int(m["m"] or 0))
    return parse_time(start_text), span


_UOM = {"wmoUnit:km_h-1": 1 / 3.6, "wmoUnit:m_s-1": 1.0, "wmoUnit:kn": metar.KT_TO_MS}


def nws_layer(props: dict[str, Any], name: str, start: datetime) -> dict[datetime, float | None]:
    """A gridpoint layer expanded to hourly values from `start` on, in m/s
    (or degrees), converted from the layer's own `uom`."""
    layer = props.get(name) or {}
    uom = layer.get("uom")
    factor = 1.0 if name == "windDirection" else _UOM.get(uom or "")
    if factor is None:
        raise fetch.FetchError("upstream_parse", f"api.weather.gov {name} in unknown unit {uom}")
    out: dict[datetime, float | None] = {}
    end = start + timedelta(hours=HOURS)
    for item in layer.get("values") or []:
        begin, span = interval(item["validTime"])
        value = B.number(item.get("value"))
        t = begin
        while t < begin + span:
            if start <= t < end:
                out[t] = round(value * factor, 1) if value is not None else None
            t += timedelta(hours=1)
    return out


GRID_KEYS = ("updateTime", "windSpeed", "windDirection", "windGust")


def _parse_grid(content: bytes) -> tuple[dict[str, Any], int]:
    """The gridpoint properties the wind block reads (4 of about 60 layers:
    330 KB in memory for the whole Anchorage capture)."""
    import json

    props = (json.loads(content) or {}).get("properties") or {}
    if "updateTime" not in props or "windSpeed" not in props:
        raise fetch.FetchError("upstream_parse", "api.weather.gov grid without wind")
    slim = {k: props[k] for k in GRID_KEYS if k in props}
    return slim, fetch.deep_size(slim)


async def _nws(ctx: Context) -> tuple[dict[str, Any], datetime, datetime | None]:
    point_url = NWS_POINTS.format(lat=ctx.lat, lon=ctx.lon)

    async def load_point(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(point_url, headers={"Accept": "application/geo+json"})
        props = response.json().get("properties") or {}
        try:
            grid = (str(props["gridId"]), int(props["gridX"]), int(props["gridY"]))
        except (KeyError, TypeError, ValueError):
            raise fetch.FetchError(
                "upstream_parse", "api.weather.gov point without a grid"
            ) from None
        return fetch.Loaded(grid, 24 * 3600.0)

    point = await fetch.obtain(_points_cache, (ctx.lat, ctx.lon), load_point, ctx.claim)
    if point.value is None:
        assert point.error is not None
        raise point.error
    office, x, y = point.value
    grid_url = NWS_GRID.format(office=office, x=x, y=y)

    async def load_grid(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(grid_url, headers={"Accept": "application/geo+json"})
        props, size = await fetch.in_thread(_parse_grid, response.content)
        return fetch.Loaded(props, _expires_ttl(response, 300.0, 3600.0), size=size)

    grid = await fetch.obtain(_grid_cache, point.value, load_grid, ctx.claim)
    if grid.value is None:
        assert grid.error is not None
        raise grid.error
    return grid.value, parse_time(grid.value["updateTime"]), grid.fetched_at


def nws_hours(props: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    start = now.replace(minute=0, second=0, microsecond=0)
    speed = nws_layer(props, "windSpeed", start)
    direction = nws_layer(props, "windDirection", start)
    gust = nws_layer(props, "windGust", start)
    hours = []
    for i in range(HOURS):
        t = start + timedelta(hours=i)
        if t not in speed:
            continue
        d = direction.get(t)
        hours.append(
            {
                "time": B.iso(t),
                "wind_ms": speed.get(t),
                "wind_from_deg": round(d) if d is not None else None,
                "gust_ms": gust.get(t),
            }
        )
    return hours


async def build_wind(ctx: Context) -> B.Block:
    fallback = None
    if NWS_COUNTRIES & set(ctx.place.country_codes):
        try:
            props, updated, fetched_at = await _nws(ctx)
            hours = nws_hours(props, ctx.now)
            if not hours:
                raise fetch.FetchError("upstream_parse", "api.weather.gov grid has no wind ahead")
            data = {"hours": hours, "max_48h": _max48(hours), "fallback": None}
            missing = (
                []
                if any(h["gust_ms"] is not None for h in hours)
                else [{"field": "gust_ms", "reason": "not_in_source"}]
            )
            return B.ok(
                SPEC_WIND, updated, data, fetched_at=fetched_at, missing=missing, source=SRC_NWS
            )
        except fetch.FetchError as exc:
            fallback = {"from": "NWS", "code": exc.code, "reason": exc.reason}
    entry = await met_entry(ctx)

    def build(payload: dict[str, Any]) -> B.Block:
        hours, gusts = met_wind(payload, ctx)
        data = {"hours": hours, "max_48h": _max48(hours), "fallback": fallback}
        missing = [] if gusts else [{"field": "gust_ms", "reason": "not_in_source"}]
        return B.ok(SPEC_WIND, _issued(payload), data, missing=missing, source=SRC_MET)

    return serve(SPEC_WIND, entry, build)
