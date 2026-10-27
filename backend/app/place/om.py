"""Point series from the Open-Meteo open-data bucket (`.om` files, CC BY 4.0).

This is the bucket, not Open-Meteo's free API (non-commercial only): the
files Open-Meteo itself serves from, one per domain, variable and time
chunk, read by byte range. `omfiles` decodes them in the place executor's
threads, but every byte-range GET it asks for comes back to the event loop
through `asyncio.run_coroutine_threadsafe` and `fetch.py`: a sync client in a
thread would bypass the User-Agent, the caps, the timeouts and the retry.

Traps, measured on 2026-10-27:

- a chunk file is OVERWRITTEN IN PLACE on each run (chunk modified 09:51:36,
  `static/meta.json` 09:51:51). Every range GET carries `If-Match` with the
  ETag from the HEAD: a 412 means the file changed mid-read, and the read
  restarts once. `meta.json` is read again after a cell's reads; if the run
  changed in between, the cell is read again once, so `as_of` never names a
  run other than the one the values came from;
- `cams_europe` rows run NORTH to south (its BBOX is written
  `[71.95,-24.95,30.05,44.95]`, lat_min > lat_max): the row index comes from
  the BBOX as written, never from an assumed south-up grid;
- a time chunk holds the whole time axis of a 1 x 14 (cams) or 1 x 25
  (MFWAM) block of cells: any time slice of a cell costs the same ranges;
- past `data_end_time` the file holds NaN, which becomes null;
- the Linux wheel of omfiles 1.2.0 uses AVX2 instructions: under an
  emulator without AVX2 the read dies with SIGILL, which kills the whole
  process, not the block. `decoder_ready()` checks the CPU first.
"""

from __future__ import annotations

import asyncio
import math
import platform
import re
from collections.abc import Coroutine
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import numpy as np

from app.place import block as B
from app.place import fetch
from app.place.context import Context
from app.place.geo import haversine_km

BUCKET = "https://openmeteo.s3.amazonaws.com/data"
META_TTL = 1800.0  # meta.json polled every 30 min at most per domain
SMALL_RANGE = 512  # trailer, metadata and index reads are cached by ETag

_meta_cache = fetch.Cache("openmeteo.s3.amazonaws.com meta", 64)
_head_cache = fetch.Cache("openmeteo.s3.amazonaws.com head", 4096)
_range_cache = fetch.Cache("openmeteo.s3.amazonaws.com ranges", 4096)
_cell_cache = fetch.Cache("openmeteo.s3.amazonaws.com cells", 4096)
_last_good = fetch.Cache("openmeteo.s3.amazonaws.com last good", 4096)
_shapes: dict[str, tuple[int, int]] = {}


class Replaced(fetch.FetchError):
    """HTTP 412 on If-Match: the chunk was rewritten during the read."""


@lru_cache(maxsize=1)
def decoder_ready() -> str | None:
    """None when the .om decoder can run here, else why not."""
    if platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                flags = f.read()
        except OSError:
            return None
        if not re.search(r"^flags\s*:.*\bavx2\b", flags, re.M):
            return "this server's CPU has no AVX2, which the omfiles decoder needs"
    return None


@dataclass(frozen=True)
class Meta:
    run: datetime
    run_epoch: int
    chunk_len: int
    step_s: int
    data_end: int
    bbox: tuple[float, float, float, float]  # as written: lat0, lon0, lat1, lon1


def parse_meta(payload: dict[str, Any]) -> Meta:
    m = re.search(r"BBOX\[([^\]]*)\]", payload["crs_wkt"])
    if not m:
        raise fetch.FetchError("upstream_parse", "openmeteo meta.json without a BBOX")
    lat0, lon0, lat1, lon1 = (float(v) for v in m.group(1).split(","))
    run = int(payload["last_run_initialisation_time"])
    return Meta(
        run=datetime.fromtimestamp(run, UTC),
        run_epoch=run,
        chunk_len=int(payload["chunk_time_length"]),
        step_s=int(payload["temporal_resolution_seconds"]),
        data_end=int(payload["data_end_time"]),
        bbox=(lat0, lon0, lat1, lon1),
    )


async def meta(domain: str, ctx: Context, *, force: bool = False) -> Meta:
    url = f"{BUCKET}/{domain}/static/meta.json"

    async def load(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(url)
        return fetch.Loaded(parse_meta(response.json()), META_TTL)

    if force:
        entry = _meta_cache.get(domain)
        if entry.task is None:
            entry.fresh_until = 0.0  # read it again now
            entry.error_until = 0.0
    entry = await fetch.obtain(_meta_cache, domain, load, ctx.claim)
    if entry.value is None:
        assert entry.error is not None
        raise entry.error
    value: Meta = entry.value
    return value


class _RangeFS:
    """The file-system object omfiles reads through, called from a worker
    thread. Each call runs its request on the event loop and waits for it."""

    def __init__(self, loop: asyncio.AbstractEventLoop, run_epoch: int) -> None:
        self.loop = loop
        self.run_epoch = run_epoch
        self.failure: fetch.FetchError | None = None
        self.requests = 0

    def _call(self, coro: Coroutine[Any, Any, Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=60)
        except fetch.FetchError as exc:
            self.failure = exc
            raise

    def size(self, path: str) -> int:
        return int(self._call(self._head(path))[1])

    def cat_file(
        self, path: str, start: int | None = None, end: int | None = None, **_: Any
    ) -> bytes:
        return bytes(self._call(self._range(path, start or 0, end)))

    async def _head(self, path: str) -> tuple[str, int]:
        key = (path, self.run_epoch)

        async def load(previous: fetch.Entry) -> fetch.Loaded:
            response = await fetch.get(path, method="HEAD", accept=(200,))
            self.requests += 1
            etag = response.headers.get("etag")
            size = response.headers.get("content-length")
            if not etag or not size:
                raise fetch.FetchError("upstream_parse", "openmeteo chunk HEAD without ETag")
            return fetch.Loaded((etag, int(size)), 6 * 3600.0)

        entry = await fetch.obtain(_head_cache, key, load, lambda: None)
        if entry.value is None:
            assert entry.error is not None
            raise entry.error
        value: tuple[str, int] = entry.value
        return value

    async def _range(self, path: str, start: int, end: int | None) -> bytes:
        etag, size = await self._head(path)
        stop = size if end is None else end
        key = (path, etag, start, stop)
        hit = _range_cache.peek(key)
        if hit is not None and hit.value is not None:
            return bytes(hit.value)
        headers = {"Range": f"bytes={start}-{stop - 1}", "If-Match": etag}
        try:
            response = await fetch.get(path, headers=headers, accept=(206, 200))
        except fetch.FetchError as exc:
            if exc.status == 412:
                _head_cache.entries.pop((path, self.run_epoch), None)
                raise Replaced(exc.code, exc.reason, 412) from None
            raise
        self.requests += 1
        body = response.content
        if response.status_code == 200 and (start or stop != size):
            body = body[start:stop]
        if stop - start <= SMALL_RANGE:
            entry = _range_cache.get(key)
            entry.value = body
            entry.fresh_until = float("inf")
        return body


def _read_block(fs: _RangeFS, url: str, ys: tuple[int, int], xs: tuple[int, int]) -> np.ndarray:
    import omfiles  # imported where it runs: a missing wheel fails this block only

    reader = omfiles.OmFileReader.from_fsspec(fs, url)
    values = reader[ys[0] : ys[1], xs[0] : xs[1], :]
    return np.asarray(values, dtype=np.float32)


def _read_shape(fs: _RangeFS, url: str) -> tuple[int, int]:
    import omfiles

    reader = omfiles.OmFileReader.from_fsspec(fs, url)
    shape = reader.shape
    return int(shape[0]), int(shape[1])


async def _threaded(fs: _RangeFS, fn: Any, *args: Any) -> Any:
    try:
        return await fetch.in_thread(fn, fs, *args)
    except Exception as exc:
        if fs.failure is not None:
            raise fs.failure from None
        raise fetch.FetchError(
            "upstream_parse", f"{type(exc).__name__} decoding an openmeteo chunk"
        ) from None


@dataclass(frozen=True)
class Grid:
    lat0: float
    lon0: float
    dlat: float
    dlon: float
    ny: int
    nx: int
    wraps: bool

    def index(self, lat: float, lon: float) -> tuple[int, int] | None:
        y = round((lat - self.lat0) / self.dlat)
        x = round((lon - self.lon0) / self.dlon)
        if self.wraps:
            x %= self.nx
        if not (0 <= y < self.ny and 0 <= x < self.nx):
            return None
        return y, x

    def centre(self, y: int, x: int) -> tuple[float, float]:
        lon = self.lon0 + x * self.dlon
        lon = (lon + 180.0) % 360.0 - 180.0
        return round(self.lat0 + y * self.dlat, 4), round(lon, 4)


def grid_of(m: Meta, shape: tuple[int, int]) -> Grid:
    lat0, lon0, lat1, lon1 = m.bbox
    ny, nx = shape
    dlat = (lat1 - lat0) / (ny - 1)
    dlon = (lon1 - lon0) / (nx - 1)
    return Grid(lat0, lon0, dlat, dlon, ny, nx, wraps=abs((lon1 - lon0) + dlon - 360.0) < 1e-3)


@dataclass
class Series:
    domain: str
    run: datetime
    times: list[datetime]
    values: dict[str, list[float | None]]
    cell: tuple[float, float]
    cell_km: float
    fetched_at: datetime | None
    # set when a refresh failed and this is the last good series (block.py:
    # served with its own run until that run is older than 2x max_age)
    error: fetch.FetchError | None = None


class NoCell(Exception):
    """No valid model cell within the search radius (`coast_not_resolved`),
    or the point outside the domain (`no_coverage`)."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


async def series(
    ctx: Context,
    domain: str,
    variables: list[str],
    start: datetime,
    end: datetime,
    *,
    window_km: float = 0.0,
) -> Series:
    """The series of `variables` at the model cell of the requester's point
    (or, with `window_km`, the nearest cell holding a value at `start`),
    from `start` to `end`. Every distance is measured from ctx's own point."""
    problem = decoder_ready()
    if problem:
        raise fetch.FetchError("upstream_parse", problem)
    key = (domain, tuple(variables), ctx.lat, ctx.lon, window_km)
    try:
        result = await _series(ctx, domain, variables, start, end, window_km)
    except fetch.FetchError as exc:
        last = _last_good.peek(key)
        if last is None or last.value is None:
            raise
        stale: Series = last.value
        return replace(stale, error=exc)
    entry = _last_good.get(key)
    entry.value = result
    entry.fresh_until = float("inf")
    return result


async def _series(
    ctx: Context,
    domain: str,
    variables: list[str],
    start: datetime,
    end: datetime,
    window_km: float,
) -> Series:
    m = await meta(domain, ctx)
    for attempt in range(2):
        try:
            result, read = await _series_once(ctx, domain, m, variables, start, end, window_km)
        except Replaced:
            if attempt:
                raise
            m = await meta(domain, ctx, force=True)
            continue
        if not read:
            return result
        fresh = await meta(domain, ctx, force=True)
        if fresh.run_epoch == m.run_epoch:
            return result
        m = fresh  # the run changed while we read: once more, under the new run
    return result


async def _series_once(
    ctx: Context,
    domain: str,
    m: Meta,
    variables: list[str],
    start: datetime,
    end: datetime,
    window_km: float,
) -> tuple[Series, bool]:
    loop = asyncio.get_running_loop()
    i0 = int(start.timestamp()) // m.step_s
    i1 = int(end.timestamp()) // m.step_s
    chunks = list(range(i0 // m.chunk_len, i1 // m.chunk_len + 1))
    shape = _shapes.get(domain)
    if shape is None:
        url = f"{BUCKET}/{domain}/{variables[0]}/chunk_{chunks[0]}.om"
        ctx.claim()
        shape = await _threaded(_RangeFS(loop, m.run_epoch), _read_shape, url)
        _shapes[domain] = shape
    grid = grid_of(m, shape)
    own = grid.index(ctx.lat, ctx.lon)
    if own is None:
        raise NoCell("no_coverage", f"outside the {domain} domain")
    read = False
    fetched: list[datetime] = []

    async def cells(var: str, chunk: int, ys: tuple[int, int], xs: tuple[int, int]) -> np.ndarray:
        nonlocal read
        url = f"{BUCKET}/{domain}/{var}/chunk_{chunk}.om"
        key = (domain, var, chunk, m.run_epoch, ys, xs)

        async def load(previous: fetch.Entry) -> fetch.Loaded:
            values = await _threaded(_RangeFS(loop, m.run_epoch), _read_block, url, ys, xs)
            return fetch.Loaded(values, float("inf"), size=values.nbytes)

        hit = _cell_cache.peek(key)
        if hit is None or hit.value is None:
            read = True
        entry = await fetch.obtain(_cell_cache, key, load, ctx.claim)
        if entry.value is None:
            assert entry.error is not None
            if isinstance(entry.error, Replaced):
                entry.error_until = 0.0  # not a failure to remember: read it again
            raise entry.error
        if entry.fetched_at is not None:
            fetched.append(entry.fetched_at)
        block: np.ndarray = entry.value
        return block

    y, x = own
    chosen = (y, x)
    ti = i0 - chunks[0] * m.chunk_len
    first = await cells(variables[0], chunks[0], (y, y + 1), (x, x + 1)) if window_km else None
    if first is not None and math.isnan(float(first[0, 0, ti])):
        ky = max(1, math.ceil(window_km / (111.32 * abs(grid.dlat))))
        kx = max(
            1,
            math.ceil(
                window_km / (111.32 * abs(grid.dlon) * max(0.05, math.cos(math.radians(ctx.lat))))
            ),
        )
        ys = (max(0, y - ky), min(grid.ny, y + ky + 1))
        xs = (max(0, x - kx), min(grid.nx, x + kx + 1))
        window = await cells(variables[0], chunks[0], ys, xs)
        best: tuple[float, int, int] | None = None
        for j in range(window.shape[0]):
            for i in range(window.shape[1]):
                if math.isnan(float(window[j, i, ti])):
                    continue
                lat, lon = grid.centre(ys[0] + j, xs[0] + i)
                km = haversine_km(ctx.lat, ctx.lon, lat, lon)
                if km <= window_km and (best is None or km < best[0]):
                    best = (km, ys[0] + j, xs[0] + i)
        if best is None:
            raise NoCell("coast_not_resolved", f"no {domain} sea cell within {window_km:g} km")
        chosen = (best[1], best[2])
    cy, cx = chosen
    values: dict[str, list[float | None]] = {}
    times: list[datetime] = []
    # every variable and chunk at once: one at a time, a cold CAMS cell (7
    # variables, 5 ranges each) did not fit the report's 8 s
    reads = await asyncio.gather(
        *(cells(var, chunk, (cy, cy + 1), (cx, cx + 1)) for var in variables for chunk in chunks)
    )
    for n, var in enumerate(variables):
        parts = reads[n * len(chunks) : (n + 1) * len(chunks)]
        flat = np.concatenate([p[0, 0, :] for p in parts])
        base = chunks[0] * m.chunk_len
        series_values: list[float | None] = []
        if not times:
            times = [
                datetime.fromtimestamp((base + k) * m.step_s, UTC)
                for k in range(i0 - base, i1 - base + 1)
            ]
        for k in range(i0 - base, i1 - base + 1):
            v = float(flat[k])
            series_values.append(None if math.isnan(v) else v)
        values[var] = series_values
    lat, lon = grid.centre(cy, cx)
    return (
        Series(
            domain=domain,
            run=m.run,
            times=times,
            values=values,
            cell=(lat, lon),
            cell_km=round(haversine_km(ctx.lat, ctx.lon, lat, lon), 1),
            fetched_at=min(fetched) if fetched else None,
        ),
        read,
    )


def with_refresh_error(block: B.Block, series: Series, spec: B.Spec) -> B.Block:
    """The failed-refresh rule for a model block: the last good series keeps
    serving with its own run, and the refresh's error, until that run is
    older than 2x `max_age_s`; then `unavailable` with the failure's code."""
    if series.error is None:
        return block
    assert spec.max_age_s is not None
    if block.as_of is not None and (
        datetime.now(block.as_of.tzinfo) - block.as_of > timedelta(seconds=2 * spec.max_age_s)
    ):
        return B.unavailable(spec, series.error.code, series.error.reason)
    block.error = B.Problem(series.error.code, series.error.reason)
    return block
