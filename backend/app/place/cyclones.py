"""`history.cyclones`: every tropical cyclone track of IBTrACS v04r01 that
passed within the radius.

The ALL file is 333 MB. It is fetched in the background (HEAD with
If-Modified-Since every 24 h, a download only when it changed), streamed to
a temporary file in `data_dir` after checking 1 GB is free, parsed in a
SEPARATE PROCESS (12.7 s of CPU must not compete with the websocket loop
for the GIL), and reduced to a packed index of track points with a 1-degree
grid of track SEGMENTS, written to `data_dir/ibtracs-index.bin` (temporary
file, then rename). The header holds a format version and the file's
Last-Modified; a mismatch rebuilds it. The CSV is then deleted.

Measured traps:

- the second line is a UNITS row, not a storm;
- a blank value is a single space `' '`;
- `spur` and `PROVISIONAL_spur` tracks are alternate positions of a main
  track ("when counting storms, these should not likely be counted",
  IBTrACS column documentation): excluded. PROVISIONAL tracks (real time,
  not yet reanalysed) are kept and marked;
- `NATURE` ET is extratropical: a storm seen here only as ET is a
  post-tropical remnant, counted apart;
- closest approach is measured to track SEGMENTS, not to the 3-hourly
  points (a point-only distance overestimates the pass and misses it at
  small radii), and the wind is the maximum along the part of the track
  INSIDE the radius: a lifetime peak reached far at sea never stands for
  the wind a storm brought here;
- WMO_WIND carries "NO adjustment ... for differences in wind speed
  averaging periods": the agency and its averaging are named per storm.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import multiprocessing
import os
import shutil
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.place import block as B
from app.place import fetch
from app.place.context import Context

log = logging.getLogger(__name__)

URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-"
    "ibtracs/v04r01/access/csv/ibtracs.ALL.list.v04r01.csv"
)
FORMAT = 1
INDEX_NAME = "ibtracs-index.bin"
MIN_FREE = 1 << 30
CHECK_EVERY = 86400.0
EPOCH = datetime(1800, 1, 1, tzinfo=UTC)
NATURES = ("DS", "TS", "ET", "SS", "NR", "MX")
# IBTrACS v04 column documentation (captured 2026-10-27): WMO_WIND averaging
# per agency; "US agencies provide 1-minute wind speeds" for USA_WIND.
AVERAGING = {
    "hurdat_atl": "1-min",
    "hurdat_epa": "1-min",
    "atcf": "1-min",
    "tokyo": "10-min",
    "newdelhi": "3-min",
    "reunion": "10-min",
    "bom": "10-min",
    "nadi": "10-min",
    "wellington": "10-min",
}

SRC = B.Source(
    "NOAA NCEI IBTrACS v04r01",
    "https://www.ncei.noaa.gov/products/international-best-track-archive",
    "Knapp, K. R. et al., International Best Track Archive for Climate Stewardship "
    "(IBTrACS) v04r01, NOAA National Centers for Environmental Information; distances and "
    "winds within the radius computed by SOSForge",
    "US Government work, public domain",
)
SPEC = B.Spec("history.cyclones", "catalogue", "through", 14 * 86400.0, SRC)


# ---------------------------------------------------------------- ingest


def _minutes(text: str) -> int:
    when = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return int((when - EPOCH).total_seconds() // 60)


def _wind(text: str) -> int:
    text = text.strip()
    return int(float(text)) if text else -1


def build_index(csv_path: str, out_path: str, last_modified: str) -> dict[str, Any]:
    """Runs in the ingest SUBPROCESS: CSV -> packed index file. Returns the
    header it wrote."""
    lat: list[float] = []
    lon: list[float] = []
    tmin: list[int] = []
    usa: list[int] = []
    wmo: list[int] = []
    nature: list[int] = []
    storm: list[int] = []
    sids: list[str] = []
    names: list[str] = []
    seasons: list[int] = []
    provisional: list[bool] = []
    agencies: list[Counter[str]] = []
    index: dict[str, int] = {}
    newest = -(1 << 31)
    since = 9999
    rows = 0
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader)
        col = {name: i for i, name in reversed(list(enumerate(header)))}
        next(reader)  # the units row
        c_sid, c_season, c_name = col["SID"], col["SEASON"], col["NAME"]
        c_time, c_nature, c_lat, c_lon = col["ISO_TIME"], col["NATURE"], col["LAT"], col["LON"]
        c_wmo, c_agency, c_track, c_usa = (
            col["WMO_WIND"],
            col["WMO_AGENCY"],
            col["TRACK_TYPE"],
            col["USA_WIND"],
        )
        last_lon: dict[int, float] = {}
        for row in reader:
            if len(row) <= c_usa:
                continue
            track = row[c_track].strip()
            if "spur" in track.lower():
                continue
            rows += 1
            sid = row[c_sid]
            k = index.get(sid)
            if k is None:
                k = len(sids)
                index[sid] = k
                sids.append(sid)
                names.append(row[c_name].strip())
                season = int(row[c_season])
                seasons.append(season)
                since = min(since, season)
                provisional.append(track.upper() == "PROVISIONAL")
                agencies.append(Counter())
            try:
                minutes = _minutes(row[c_time])
                la, lo = float(row[c_lat]), float(row[c_lon])
            except ValueError:
                continue
            # continuous longitudes along a track: a segment never spans the globe
            if k in last_lon:
                lo = last_lon[k] + ((lo - last_lon[k] + 180.0) % 360.0 - 180.0)
            last_lon[k] = lo
            newest = max(newest, minutes)
            lat.append(la)
            lon.append(lo)
            tmin.append(minutes)
            usa.append(_wind(row[c_usa]))
            wmo.append(_wind(row[c_wmo]))
            n = row[c_nature].strip()
            nature.append(NATURES.index(n) if n in NATURES else NATURES.index("NR"))
            storm.append(k)
            agency = row[c_agency].strip()
            if agency:
                agencies[k][agency] += 1
    p_lat = np.asarray(lat, dtype=np.float32)
    p_lon = np.asarray(lon, dtype=np.float32)
    p_storm = np.asarray(storm, dtype=np.int32)
    # 1-degree grid of SEGMENTS (point j -> j+1 of the same storm)
    seg = np.nonzero(p_storm[:-1] == p_storm[1:])[0].astype(np.int32)
    lat_lo = np.floor(np.minimum(p_lat[seg], p_lat[seg + 1])).astype(np.int32)
    lat_hi = np.floor(np.maximum(p_lat[seg], p_lat[seg + 1])).astype(np.int32)
    lon_lo = np.floor(np.minimum(p_lon[seg], p_lon[seg + 1])).astype(np.int32)
    lon_hi = np.floor(np.maximum(p_lon[seg], p_lon[seg + 1])).astype(np.int32)
    cells: list[int] = []
    owners: list[int] = []
    for j, a, b, c, d in zip(
        seg.tolist(),
        lat_lo.tolist(),
        lat_hi.tolist(),
        lon_lo.tolist(),
        lon_hi.tolist(),
        strict=True,
    ):
        for y in range(max(-90, a), min(89, b) + 1):
            for x in range(c, d + 1):
                cells.append((y + 90) * 360 + (x + 180) % 360)
                owners.append(j)
    order = np.argsort(np.asarray(cells, dtype=np.int32), kind="stable")
    cell_sorted = np.asarray(cells, dtype=np.int32)[order]
    seg_sorted = np.asarray(owners, dtype=np.int32)[order]
    offsets = np.searchsorted(cell_sorted, np.arange(180 * 360 + 1), side="left").astype(np.int32)
    agency_of = [c.most_common(1)[0][0] if c else "" for c in agencies]
    head = {
        "format": FORMAT,
        "last_modified": last_modified,
        "newest": (EPOCH + timedelta(minutes=newest)).isoformat(),
        "since_season": since,
        "rows": rows,
        "storms": len(sids),
    }
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as out:
        np.savez(
            out,
            head=np.asarray(json.dumps(head)),
            lat=p_lat,
            lon=p_lon,
            tmin=np.asarray(tmin, dtype=np.int32),
            usa=np.asarray(usa, dtype=np.int16),
            wmo=np.asarray(wmo, dtype=np.int16),
            nature=np.asarray(nature, dtype=np.uint8),
            storm=p_storm,
            sid=np.asarray(sids),
            name=np.asarray(names),
            season=np.asarray(seasons, dtype=np.int16),
            provisional=np.asarray(provisional, dtype=bool),
            agency=np.asarray(agency_of),
            offsets=offsets,
            segments=seg_sorted,
        )
    os.replace(tmp, out_path)
    return head


@dataclass
class Index:
    head: dict[str, Any]
    a: dict[str, np.ndarray]
    storm_max_usa: np.ndarray
    storm_max_wmo: np.ndarray

    @property
    def newest(self) -> datetime:
        return datetime.fromisoformat(self.head["newest"]).astimezone(UTC)

    @property
    def modified(self) -> datetime | None:
        try:
            return parsedate_to_datetime(self.head["last_modified"]).astimezone(UTC)
        except (TypeError, ValueError):
            return None


def load_index(path: str) -> Index | None:
    try:
        with np.load(path, allow_pickle=False) as z:
            head = json.loads(str(z["head"]))
            if head.get("format") != FORMAT:
                return None
            arrays = {k: z[k] for k in z.files if k != "head"}
    except (OSError, ValueError, KeyError):
        return None
    n = len(arrays["sid"])
    max_usa = np.full(n, -1, dtype=np.int16)
    max_wmo = np.full(n, -1, dtype=np.int16)
    np.maximum.at(max_usa, arrays["storm"], arrays["usa"])
    np.maximum.at(max_wmo, arrays["storm"], arrays["wmo"])
    return Index(head, arrays, max_usa, max_wmo)


# ------------------------------------------------------------------- state


@dataclass
class _State:
    data_dir: Path | None = None
    index: Index | None = None
    task: asyncio.Task[None] | None = None
    error: fetch.FetchError | None = None
    checked: float = 0.0
    disk_loaded: bool = False
    read_at: datetime | None = None  # the last successful HEAD or download


_state = _State()
_pool: ProcessPoolExecutor | None = None


def configure(data_dir: Path) -> None:
    _state.data_dir = Path(data_dir)


def _executor() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        # spawn, never fork: the parent runs an event loop and threads
        _pool = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"))
    return _pool


async def _ingest() -> None:
    assert _state.data_dir is not None
    data_dir = _state.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    current = _state.index
    headers = {}
    if current is not None and current.head.get("last_modified"):
        headers["If-Modified-Since"] = current.head["last_modified"]
    if shutil.disk_usage(data_dir).free < MIN_FREE:
        raise fetch.FetchError("busy", "less than 1 GB free in the data directory for IBTrACS")
    csv_path = data_dir / "ibtracs-ALL.csv.tmp"
    try:
        response = await fetch.upstream.download(URL, csv_path, headers=headers)
        _state.read_at = datetime.now(UTC)
        if response.status_code == 304:
            return
        modified = response.headers.get("last-modified") or format_datetime(
            datetime.now(UTC), usegmt=True
        )
        loop = asyncio.get_running_loop()
        out = str(data_dir / INDEX_NAME)
        await loop.run_in_executor(_executor(), build_index, str(csv_path), out, modified)
        index = await fetch.in_thread(load_index, out)
        if index is None:
            raise fetch.FetchError("upstream_parse", "the IBTrACS index could not be read back")
        _state.index = index
        log.info("IBTrACS index built: %s storms", index.head.get("storms"))
    finally:
        csv_path.unlink(missing_ok=True)


async def _run_ingest() -> None:
    try:
        await _ingest()
        _state.error = None
    except fetch.FetchError as exc:
        _state.error = exc
    except Exception as exc:  # noqa: BLE001 -- a failed ingest is a block error, never a crash
        _state.error = fetch.FetchError("upstream_parse", f"{type(exc).__name__} building IBTrACS")
        log.warning("IBTrACS ingest: %s", type(exc).__name__)
    finally:
        _state.checked = time.monotonic()
        _state.task = None


async def ensure(ctx: Context) -> None:
    """Loads the index from disk once, and starts the 24 h check or the
    first ingest when due. Never waits for the ingest itself."""
    if _state.data_dir is None:
        return
    if not _state.disk_loaded:
        _state.disk_loaded = True
        path = _state.data_dir / INDEX_NAME
        if path.exists():
            _state.index = await fetch.in_thread(load_index, str(path))
            if _state.index is not None:
                _state.checked = time.monotonic()
                _state.read_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    due = _state.index is None or time.monotonic() - _state.checked > CHECK_EVERY
    if _state.task is None and due:
        if (
            _state.index is None
            and _state.error is not None
            and (time.monotonic() - _state.checked < fetch.FAILURE_MEMORY_S)
        ):
            return  # a failure is remembered 60 s, never longer
        ctx.claim()
        _state.task = fetch.spawn(_run_ingest(), "place:ibtracs")


# ------------------------------------------------------------------- query


def _wind_source(index: Index, k: int) -> tuple[str, str | None]:
    if index.storm_max_usa[k] >= 0:
        return "usa", "USA_WIND, 1-min"
    if index.storm_max_wmo[k] >= 0:
        agency = str(index.a["agency"][k])
        averaging = AVERAGING.get(agency)
        label = f"WMO_WIND ({agency})" if agency else "WMO_WIND"
        return "wmo", f"{label}, {averaging}" if averaging else label
    return "none", None


def query(index: Index, lat: float, lon: float, radius_km: float, now_year: int) -> dict[str, Any]:
    a = index.a
    km_lat = 111.32
    km_lon = 111.32 * max(0.05, math.cos(math.radians(lat)))
    dlat = radius_km / km_lat
    dlon = min(180.0, radius_km / km_lon)
    offsets, segments = a["offsets"], a["segments"]
    found: set[int] = set()
    for y in range(math.floor(max(-90.0, lat - dlat)), math.floor(min(89.999, lat + dlat)) + 1):
        for x in range(math.floor(lon - dlon), math.floor(lon + dlon) + 1):
            cell = (y + 90) * 360 + (x + 180) % 360
            found.update(segments[offsets[cell] : offsets[cell + 1]].tolist())
    storms: dict[int, dict[str, Any]] = {}
    if found:
        j = np.fromiter(found, dtype=np.int64)
        lat1, lat2 = a["lat"][j].astype(np.float64), a["lat"][j + 1].astype(np.float64)
        lon1, lon2 = a["lon"][j].astype(np.float64), a["lon"][j + 1].astype(np.float64)
        d1 = (lon1 - lon + 540.0) % 360.0 - 180.0
        d2 = d1 + (lon2 - lon1)
        ax, ay = d1 * km_lon, (lat1 - lat) * km_lat
        bx, by = d2 * km_lon, (lat2 - lat) * km_lat
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(length == 0, 0.0, np.clip(-(ax * dx + ay * dy) / length, 0.0, 1.0))
        dist = np.hypot(ax + t * dx, ay + t * dy)
        inside = dist <= radius_km
        for row in np.nonzero(inside)[0].tolist():
            seg = int(j[row])
            k = int(a["storm"][seg])
            kind, _ = _wind_source(index, k)
            # the part of the segment inside the circle: |A + s(B-A)| = R
            qa = float(length[row])
            qb = 2 * float(ax[row] * dx[row] + ay[row] * dy[row])
            qc = float(ax[row] ** 2 + ay[row] ** 2) - radius_km**2
            if qa == 0:
                s0 = s1 = 0.0
            else:
                disc = max(0.0, qb * qb - 4 * qa * qc)
                s0 = max(0.0, (-qb - math.sqrt(disc)) / (2 * qa))
                s1 = min(1.0, (-qb + math.sqrt(disc)) / (2 * qa))
            wind_in = None
            if kind != "none":
                column = a["usa"] if kind == "usa" else a["wmo"]
                w1, w2 = int(column[seg]), int(column[seg + 1])
                values = []
                if w1 >= 0 and w2 >= 0:
                    values = [w1 + s * (w2 - w1) for s in (s0, s1)]
                else:
                    if w1 >= 0 and s0 == 0.0:
                        values.append(w1)
                    if w2 >= 0 and s1 == 1.0:
                        values.append(w2)
                wind_in = round(max(values)) if values else None
            tt = float(t[row])
            when = a["tmin"][seg] + tt * (a["tmin"][seg + 1] - a["tmin"][seg])
            nature = NATURES[int(a["nature"][seg] if tt < 0.5 else a["nature"][seg + 1])]
            record = storms.get(k)
            if record is None:
                record = storms[k] = {"closest": math.inf, "wind": None, "natures": set()}
            record["natures"].update(
                {NATURES[int(a["nature"][seg])], NATURES[int(a["nature"][seg + 1])]}
            )
            if wind_in is not None and (record["wind"] is None or wind_in > record["wind"]):
                record["wind"] = wind_in
            if float(dist[row]) < record["closest"]:
                record["closest"] = float(dist[row])
                record["time"] = EPOCH + timedelta(minutes=float(when))
                record["nature"] = nature
    rows = []
    tropical = post = other = 0
    for k, rec in storms.items():
        natures = rec["natures"]
        if natures & {"TS", "SS"}:
            tropical += 1
        elif natures <= {"ET"}:
            post += 1
        else:
            other += 1
        kind, label = _wind_source(index, k)
        lifetime = int(index.storm_max_usa[k] if kind == "usa" else index.storm_max_wmo[k])
        rows.append(
            {
                "sid": str(a["sid"][k]),
                "name": str(a["name"][k]) or None,
                "season": int(a["season"][k]),
                "max_wind_within_radius_kt": rec["wind"],
                "lifetime_max_wind_kt": lifetime if lifetime >= 0 else None,
                "wind_source": label,
                "closest_km": round(rec["closest"]),
                "closest_time": B.iso(rec["time"]),
                "nature_at_closest": rec["nature"],
                "post_tropical": not (natures & {"TS", "SS"}) and natures <= {"ET"},
                "provisional": bool(a["provisional"][k]),
            }
        )
    strongest = sorted(
        rows,
        key=lambda r: (
            r["max_wind_within_radius_kt"] if r["max_wind_within_radius_kt"] is not None else -1,
            r["lifetime_max_wind_kt"] or -1,
        ),
        reverse=True,
    )
    latest = sorted(rows, key=lambda r: r["closest_time"] or "", reverse=True)
    counts = Counter((r["season"] // 10) * 10 for r in rows)
    current = (now_year // 10) * 10
    modified = index.modified
    return {
        "radius_km": int(radius_km) if float(radius_km).is_integer() else radius_km,
        "catalogue_since_season": index.head.get("since_season"),
        "total_storms": len(rows),
        "tropical": tropical,
        "post_tropical_only": post,
        "other_nature_only": other,
        "strongest": strongest[:10],
        "latest": latest[:5],
        "by_decade": [
            {"decade": d, "count": counts[d], "early": d < 1970, "partial": d == current}
            for d in sorted(counts)
        ],
        "file_modified": B.iso(modified),
        "derived": ["closest_km", "max_wind_within_radius_kt", "by_decade"],
    }


async def build(ctx: Context) -> B.Block:
    await ensure(ctx)
    index = _state.index
    if index is None:
        if _state.task is not None:
            return B.unavailable(
                SPEC, "warming", "the IBTrACS file is being read for the first time"
            )
        error = _state.error or fetch.FetchError("upstream_parse", "no IBTrACS index")
        return B.unavailable(SPEC, error.code, error.reason)
    data = await fetch.in_thread(query, index, ctx.lat, ctx.lon, ctx.radius_km, ctx.now.year)
    block = B.ok(SPEC, index.newest, data, fetched_at=_state.read_at)
    if _state.error is not None:
        block.error = B.Problem(_state.error.code, _state.error.reason)
    return block
