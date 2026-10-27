"""`sea.waves`, `sea.level`, `sea.temperature`, `sea.buoy`.

The sea gate: these apply when the place is at sea or its nearest ocean
coastline is 50 km away or less. Otherwise they are `not_applicable` /
`inland` with the distance (Paris 150 km, Kathmandu 647 km). A sea value
shown for a coastal place is the model's or the buoy's, never the place's
own: every block says how far its cell or station is.

- `sea.waves` (Meteo-France MFWAM) and `sea.level` (Meteo-France SMOC, tide
  plus surge relative to the MODEL mean sea level, not chart datum) come
  from the Open-Meteo bucket (`om.py`). Land cells are NaN: the nearest
  cell holding a value within 30 km is used, measured from the requester.
- `sea.temperature`: NOAA CoastWatch Geo-Polar Blended SST, one daily
  analysis stamped 12:00Z. Its `mask` is a BIT mask (dataset comment: "b0:
  1=grid cell is water, b1: 1=grid cell is land, b2: 1=grid cell is ice"),
  so 5 is water plus ice: read as bits, never compared to 1.
- `sea.buoy`: NDBC's latest observation of every station. Ids are lowercase
  in `station_table.txt` and uppercase in `latest_obs.txt`; the table
  carries an owner CODE, not a name; a "Coastal Marine Station" is not a
  buoy. The 8 KMA buoys (owner KO) report a dominant period of 0-1 s with
  1-2.5 m of significant height: that period is dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.place import block as B
from app.place import fetch, om
from app.place.context import Context
from app.place.geo import cell_centre, haversine_km
from app.place.weather import serve

NEAR_KM = 5.0
CELL_KM = 30.0
BUOY_KM = 100.0
BUOY_MAX_AGE = timedelta(hours=3)
FT_TO_M = 0.3048

SRC_OM = "https://open-meteo.com/en/docs/marine-weather-api"


def _om_source(name: str, model: str, year: str) -> B.Source:
    tail = f" {year}" if year else ""
    return B.Source(
        name,
        SRC_OM,
        f"Weather data by Open-Meteo.com (open-data bucket); {model}. Generated using "
        f"E.U. Copernicus Marine Service information{tail}",
        "CC BY 4.0",
    )


SRC_WAVES = _om_source("Meteo-France MFWAM via Open-Meteo", "Meteo-France MFWAM wave model", "")
SRC_LEVEL = _om_source("Meteo-France SMOC via Open-Meteo", "Meteo-France SMOC ocean model", "")
SRC_SST = B.Source(
    "NOAA CoastWatch Geo-Polar Blended SST",
    "https://coastwatch.noaa.gov/erddap/griddap/noaacwBLENDEDsstDNDaily.html",
    "NOAA CoastWatch/OSPO Geo-Polar Blended Sea Surface Temperature analysis",
    "US Government work, public domain (GHRSST: free and open)",
)
SRC_BUOY = B.Source(
    "NOAA National Data Buoy Center",
    "https://www.ndbc.noaa.gov/",
    "Observations relayed by NOAA's National Data Buoy Center; station owners named per station",
    "US Government work, public domain; partner stations per their owner",
)

SPEC_WAVES = B.Spec("sea.waves", "model", "model_run", 36 * 3600.0, SRC_WAVES)
SPEC_LEVEL = B.Spec("sea.level", "model", "model_run", 60 * 3600.0, SRC_LEVEL)
SPEC_SST = B.Spec("sea.temperature", "observation", "valid_for", 5 * 86400.0, SRC_SST)
SPEC_BUOY = B.Spec("sea.buoy", "observation", "observed", 3 * 3600.0, SRC_BUOY)

SST_URL = (
    "https://coastwatch.noaa.gov/erddap/griddap/noaacwBLENDEDsstDNDaily.json?"
    "analysed_sst[(last)][({s:.3f}):({n:.3f})][({w:.3f}):({e:.3f})],"
    "mask[(last)][({s:.3f}):({n:.3f})][({w:.3f}):({e:.3f})]"
)
NDBC_LATEST = "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt"
NDBC_STATIONS = "https://www.ndbc.noaa.gov/data/stations/station_table.txt"

_sst_cache = fetch.Cache("coastwatch.noaa.gov")
_latest_cache = fetch.Cache("www.ndbc.noaa.gov latest", 1)
_stations_cache = fetch.Cache("www.ndbc.noaa.gov stations", 1)


def gate(spec: B.Spec, ctx: Context) -> B.Block | None:
    if ctx.place.sea_gate:
        return None
    km = ctx.place.coast_km
    if km is None:
        return B.not_applicable(spec, "inland", "no ocean coastline within 1000 km")
    return B.not_applicable(spec, "inland", f"nearest ocean coastline {km:g} km", km=round(km))


def _source_for(spec: B.Spec, run: datetime) -> B.Source:
    model = (
        "Meteo-France MFWAM wave model" if spec is SPEC_WAVES else "Meteo-France SMOC ocean model"
    )
    return _om_source(spec.source.name, model, B.years_of(run))


def _cell(series: om.Series) -> dict[str, Any]:
    return {"lat": series.cell[0], "lon": series.cell[1], "distance_km": series.cell_km}


async def build_waves(ctx: Context) -> B.Block:
    if (na := gate(SPEC_WAVES, ctx)) is not None:
        return na
    start = ctx.now
    try:
        s = await om.series(
            ctx,
            "meteofrance_wave",
            ["wave_height"],
            start,
            start + timedelta(hours=48),
            window_km=CELL_KM,
        )
    except om.NoCell as exc:
        return B.unavailable(SPEC_WAVES, exc.code, exc.reason)
    hours = [
        {"time": B.iso(t), "height_m": B.rounded(v, 2)}
        for t, v in zip(s.times, s.values["wave_height"], strict=True)
    ]
    valid = [(h["height_m"], h["time"]) for h in hours if h["height_m"] is not None]
    if not valid:
        return B.unavailable(SPEC_WAVES, "masked", "MFWAM has no wave height at this cell")
    top_height, top_time = max(valid, key=lambda pair: float(pair[0] or 0.0))
    data = {
        "cell": _cell(s),
        "hours": hours,
        "max_48h": {"height_m": top_height, "time": top_time},
    }
    block = B.ok(
        SPEC_WAVES, s.run, data, fetched_at=s.fetched_at, source=_source_for(SPEC_WAVES, s.run)
    )
    return om.with_refresh_error(block, s, SPEC_WAVES)


def extremes(
    times: list[datetime], levels: list[float | None], min_range: float = 0.10
) -> list[dict[str, Any]]:
    """Local highs and lows of an hourly series that differ from the
    neighbouring opposite extreme by at least `min_range` metres. The ends
    of the series are never extremes: what lies beyond is unknown."""
    points = [(i, v) for i, v in enumerate(levels) if v is not None]
    turns: list[tuple[int, float, str]] = []
    for k in range(1, len(points) - 1):
        (_, a), (i, b), (_, c) = points[k - 1], points[k], points[k + 1]
        kind = "high" if b > a and b >= c else "low" if b < a and b <= c else None
        if kind is None:
            continue
        if turns and turns[-1][2] == kind:
            if (kind == "high") == (b > turns[-1][1]):
                turns[-1] = (i, b, kind)
            continue
        turns.append((i, b, kind))
    changed = True
    while changed and len(turns) > 1:
        changed = False
        for k in range(len(turns) - 1):
            if abs(turns[k][1] - turns[k + 1][1]) < min_range:
                del turns[k : k + 2]
                changed = True
                break
        # removing a pair can leave two of a kind side by side: keep the more extreme
        merged: list[tuple[int, float, str]] = []
        for t in turns:
            if merged and merged[-1][2] == t[2]:
                if (t[2] == "high") == (t[1] > merged[-1][1]):
                    merged[-1] = t
                continue
            merged.append(t)
        turns = merged
    return [{"time": B.iso(times[i]), "level_m": round(v, 2), "type": kind} for i, v, kind in turns]


async def build_level(ctx: Context) -> B.Block:
    if (na := gate(SPEC_LEVEL, ctx)) is not None:
        return na
    start = ctx.now
    try:
        s = await om.series(
            ctx,
            "meteofrance_currents",
            ["sea_level_height_msl"],
            start,
            start + timedelta(hours=48),
            window_km=CELL_KM,
        )
    except om.NoCell as exc:
        return B.unavailable(SPEC_LEVEL, exc.code, exc.reason)
    levels = [B.rounded(v, 2) for v in s.values["sea_level_height_msl"]]
    if all(v is None for v in levels):
        return B.unavailable(SPEC_LEVEL, "masked", "SMOC has no sea level at this cell")
    data = {
        "cell": _cell(s),
        "datum": "model_msl",
        "hours": [{"time": B.iso(t), "level_m": v} for t, v in zip(s.times, levels, strict=True)],
        "extremes": extremes(s.times, levels),
        "derived": ["extremes"],
    }
    block = B.ok(
        SPEC_LEVEL, s.run, data, fetched_at=s.fetched_at, source=_source_for(SPEC_LEVEL, s.run)
    )
    return om.with_refresh_error(block, s, SPEC_LEVEL)


# ------------------------------------------------------------------ SST


def sst_box(lat: float, lon: float) -> tuple[float, float, float, float, tuple[float, float]]:
    """(south, north, west, east, cell key): the 0.05 deg cell of the point
    plus 30 km on every side, so any requester in the cell finds its own
    nearest water cell inside the box."""
    clat, clon = cell_centre(lat, 0.05), cell_centre(lon, 0.05)
    dlat = 0.025 + CELL_KM / 111.32
    dlon = 0.025 + CELL_KM / (111.32 * max(0.05, math.cos(math.radians(abs(clat) + 0.3))))
    south, north = max(-89.975, clat - dlat), min(89.975, clat + dlat)
    # clamped at the dataset's edges: across the antimeridian the box loses
    # its far side (a known limit, the open ocean there has no coast)
    west, east = max(-179.975, clon - dlon), min(179.975, clon + dlon)
    return south, north, west, east, (clat, clon)


@dataclass
class SstCell:
    time: datetime
    lat: float
    lon: float
    sst: float | None
    mask: int | None


def parse_sst(payload: dict[str, Any]) -> list[SstCell]:
    table = payload["table"]
    names = table["columnNames"]
    i = {n: names.index(n) for n in ("time", "latitude", "longitude", "analysed_sst", "mask")}
    out = []
    for row in table["rows"]:
        mask = row[i["mask"]]
        out.append(
            SstCell(
                time=datetime.fromisoformat(str(row[i["time"]]).replace("Z", "+00:00")),
                lat=float(row[i["latitude"]]),
                lon=float(row[i["longitude"]]),
                # -327.68 is the dataset's _FillValue
                sst=B.number(row[i["analysed_sst"]]),
                mask=int(mask) if mask is not None else None,
            )
        )
    if not out:
        raise fetch.FetchError("upstream_parse", "coastwatch.noaa.gov box with no row")
    return out


def water(mask: int | None) -> bool:
    # _FillValue 0: no information, never water
    return mask is not None and bool(mask & 1) and not mask & 2


def sst_block(cells: list[SstCell], ctx: Context) -> B.Block:
    best: tuple[float, SstCell] | None = None
    for c in cells:
        if c.sst is None or not water(c.mask):
            continue
        km = haversine_km(ctx.lat, ctx.lon, c.lat, c.lon)
        if km <= CELL_KM and (best is None or km < best[0]):
            best = (km, c)
    if best is None:
        return B.unavailable(
            SPEC_SST,
            "coast_not_resolved",
            f"no water cell of the SST analysis within {CELL_KM:g} km",
        )
    km, c = best
    data = {
        "cell": {"lat": round(c.lat, 3), "lon": round(c.lon, 3), "distance_km": round(km, 1)},
        "sst_c": round(c.sst, 1) if c.sst is not None else None,
        "ice": bool(c.mask and c.mask & 4),
        "day": c.time.date().isoformat(),
    }
    return B.ok(SPEC_SST, c.time.astimezone(UTC), data)


async def build_sst(ctx: Context) -> B.Block:
    if (na := gate(SPEC_SST, ctx)) is not None:
        return na
    south, north, west, east, key = sst_box(ctx.lat, ctx.lon)
    url = SST_URL.format(s=south, n=north, w=west, e=east)

    async def load(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(url)
        cells = await fetch.in_thread(parse_sst, response.json())
        return fetch.Loaded(cells, 6 * 3600.0, size=len(response.content))

    entry = await fetch.obtain(_sst_cache, key, load, ctx.claim)
    return serve(SPEC_SST, entry, lambda cells: sst_block(cells, ctx))


# ----------------------------------------------------------------- buoys


@dataclass
class StationInfo:
    owner_code: str | None
    type: str | None
    name: str | None


@dataclass
class Obs:
    id: str
    lat: float
    lon: float
    time: datetime
    values: dict[str, float | None]


def parse_station_table(text: str) -> dict[str, StationInfo]:
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        out[parts[0].strip().upper()] = StationInfo(
            owner_code=parts[1].strip() or None,
            type=parts[2].strip() or None,
            name=parts[4].strip() or None,
        )
    if not out:
        raise fetch.FetchError("upstream_parse", "www.ndbc.noaa.gov station table with no row")
    return out


def parse_latest(text: str) -> list[Obs]:
    lines = text.splitlines()
    header = lines[0].lstrip("#").split()
    out = []
    for line in lines[2:]:
        parts = line.split()
        if len(parts) != len(header):
            continue
        row = dict(zip(header, parts, strict=True))
        try:
            when = datetime(
                int(row["YYYY"]),
                int(row["MM"]),
                int(row["DD"]),
                int(row["hh"]),
                int(row["mm"]),
                tzinfo=UTC,
            )
            lat, lon = float(row["LAT"]), float(row["LON"])
        except (KeyError, ValueError):
            continue
        values = {k: B.number(row.get(k)) for k in header[8:]}
        out.append(Obs(row["STN"].upper(), lat, lon, when, values))
    if not out:
        raise fetch.FetchError("upstream_parse", "www.ndbc.noaa.gov latest_obs with no row")
    return out


def buoy_block(obs: list[Obs], stations: dict[str, StationInfo], ctx: Context) -> B.Block:
    best: tuple[float, Obs] | None = None
    for o in obs:
        if ctx.now - o.time > BUOY_MAX_AGE:
            continue
        km = haversine_km(ctx.lat, ctx.lon, o.lat, o.lon)
        if km <= BUOY_KM and (best is None or km < best[0]):
            best = (km, o)
    if best is None:
        return B.unavailable(
            SPEC_BUOY,
            "no_station",
            f"no NDBC station within {BUOY_KM:g} km reported in the last 3 h",
            km=int(BUOY_KM),
            hours=3,
        )
    km, o = best
    info = stations.get(o.id, StationInfo(None, None, None))
    v = o.values
    period = v.get("DPD")
    if info.owner_code == "KO":
        period = None  # KMA buoys: 0-1 s periods with 1-2.5 m waves
    tide = v.get("TIDE")
    direction = v.get("WDIR")
    wave_dir = v.get("MWD")
    data = {
        "station": {
            "id": o.id,
            "name": info.name,
            "type": info.type,
            "owner_code": info.owner_code,
            "owner": None,
            "distance_km": round(km, 1),
        },
        "wave_height_m": v.get("WVHT"),
        "dominant_period_s": period,
        "mean_wave_from_deg": int(wave_dir) if wave_dir is not None else None,
        "wind_ms": v.get("WSPD"),
        "wind_from_deg": int(direction) if direction is not None else None,
        "gust_ms": v.get("GST"),
        "water_temp_c": v.get("WTMP"),
        "air_temp_c": v.get("ATMP"),
        "pressure_hpa": v.get("PRES"),
        "tide_m": round(tide * FT_TO_M, 2) if tide is not None else None,
        "derived": ["tide_m"] if tide is not None else [],
    }
    return B.ok(SPEC_BUOY, o.time, data)


async def build_buoy(ctx: Context) -> B.Block:
    if (na := gate(SPEC_BUOY, ctx)) is not None:
        return na

    async def load_latest(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(NDBC_LATEST)
        rows = await fetch.in_thread(parse_latest, response.text)
        return fetch.Loaded(rows, 600.0, size=len(response.content))

    async def load_stations(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(NDBC_STATIONS)
        table = await fetch.in_thread(parse_station_table, response.text)
        return fetch.Loaded(table, 24 * 3600.0, size=len(response.content))

    wait = ctx.waiting(SPEC_BUOY.id, "warming")
    latest = await fetch.obtain(
        _latest_cache, "global", load_latest, ctx.claim, swr=True, on_first_wait=wait
    )
    stations = await fetch.obtain(
        _stations_cache, "global", load_stations, ctx.claim, swr=True, on_first_wait=wait
    )
    table = stations.value or {}
    return serve(SPEC_BUOY, latest, lambda rows: buoy_block(rows, table, ctx), ttl_s=600.0)
