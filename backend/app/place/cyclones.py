"""`history.cyclones`: every tropical cyclone track of IBTrACS v04r01 that
passed within the radius.

The ALL file is 333 MB. It is fetched in the background (HEAD with
If-Modified-Since every 24 h, a download only when it changed), streamed to
a temporary file in `data_dir` after checking 1 GB is free, parsed in a
SEPARATE PROCESS (12.7 s of CPU must not compete with the websocket loop
for the GIL), and reduced to a packed index of track points with a 1-degree
grid of track SEGMENTS, written to `data_dir/ibtracs-index.bin` (temporary
file, then rename). The header holds a format version and the file's
Last-Modified; a mismatch rebuilds it. The CSV is deleted once an index is
built from it, and KEPT (with its Last-Modified) when the build fails, so
the next attempt revalidates it (a 304 costs no download) instead of
fetching 333 MB again. A failed first ingest backs off from 10 minutes to
6 hours: on the 60 s failure memory a parse error used to restart the full
download every minute (about 20 GB an hour from NCEI).

The build process is a fresh `python -m app.ibtracs_index` per build, gone
when it ends (peak 78-87 MiB RSS on the 2026-10-27 file, against 360-363
MiB for the first build in a `ProcessPoolExecutor` worker). The pool had
three faults: its worker imported the whole `app.place` package to unpickle
the function, it stayed alive and idle for the life of the API after its
first build, and once the kernel killed it (what the OOM killer does) the
pool was broken for good: every later ingest failed at once until a
restart. The child volunteers as the OOM killer's first victim, so an
overrun inside the container's memory limit kills the build, never the API;
the build is then retried like any other failure (`build_failed`, the
backoff above when there is no index yet, the 24 h check otherwise).

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
import contextlib
import json
import logging
import math
import os
import shutil
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.ibtracs_index import EPOCH, FORMAT, NATURES
from app.ibtracs_index import build_index as build_index
from app.place import block as B
from app.place import fetch
from app.place.context import Context

log = logging.getLogger(__name__)

URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-"
    "ibtracs/v04r01/access/csv/ibtracs.ALL.list.v04r01.csv"
)
INDEX_NAME = "ibtracs-index.bin"
CSV_NAME = "ibtracs-ALL.csv"  # a complete download whose index is not built yet
MIN_FREE = 1 << 30
CHECK_EVERY = 86400.0
RETRY_FIRST_S = 600.0  # a failed first ingest: 10 min, then doubling
RETRY_MAX_S = 6 * 3600.0
# The build process, started from the directory that holds the `app` package
# so it runs the same code as this process.
CHILD = (sys.executable, "-m", "app.ibtracs_index")
CHILD_CWD = Path(__file__).resolve().parents[2]
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
#
# The CSV -> index build itself is `app.ibtracs_index`, run in its own
# process (see `_build_in_child`). `build_index` stays reachable from here.


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
    # the disk load, shared: a report arriving while another one loads the
    # index waits for it instead of deciding "no index" and downloading
    loading: asyncio.Task[None] | None = None
    failures: int = 0  # consecutive failed ingests without an index
    retry_at: float = 0.0  # monotonic; no new ingest before it
    read_at: datetime | None = None  # the last successful HEAD or download


_state = _State()


def configure(data_dir: Path) -> None:
    _state.data_dir = Path(data_dir)


def _child_error(stdout: bytes) -> str | None:
    """The exception type the child reported on its last stdout line."""
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        try:
            said = json.loads(line)
        except ValueError:
            continue
        if isinstance(said, dict) and isinstance(said.get("error"), str):
            return str(said["error"])
        return None
    return None


async def _build_in_child(csv_path: Path, out_path: Path, modified: str) -> None:
    """Builds the index in a new process and waits for it off the loop.
    Raises a FetchError saying how it ended when it did not succeed."""
    out = out_path.resolve()
    try:
        proc = await asyncio.create_subprocess_exec(
            *CHILD,
            str(csv_path.resolve()),
            str(out),
            modified,
            cwd=CHILD_CWD,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
        )
    except OSError as exc:  # no process at all (ENOMEM, EAGAIN, ...): our side, not the file
        log.warning("IBTrACS build: cannot start the process: %s", exc)
        raise fetch.FetchError(
            "build_failed", f"the IBTrACS build process could not start ({type(exc).__name__})"
        ) from None
    try:
        stdout, _ = await proc.communicate()
    except BaseException:
        # cancelled (shutdown): a build nobody waits for is not left running
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        raise
    status = proc.returncode
    if status == 0:
        return
    Path(f"{out}.tmp").unlink(missing_ok=True)  # what a killed savez left
    if status is not None and status < 0:
        number = -status
        try:
            name = signal.Signals(number).name
        except ValueError:
            name = "a signal"
        reason = (
            f"the IBTrACS build process was killed by {name} (signal {number}) before it finished"
        )
        if number == signal.SIGKILL:
            # what sent it is not known here: named, not guessed
            reason += ", the signal of the kernel's out-of-memory killer or of an operator"
        log.warning("IBTrACS build: %s", reason)
        raise fetch.FetchError("build_failed", reason)
    error = _child_error(stdout)
    log.warning("IBTrACS build: %s (exit status %s)", error, status)
    if error is None:
        # main() never reported: it died before reading the file (an import,
        # the interpreter, its working directory), which is our side again
        raise fetch.FetchError(
            "build_failed",
            f"the IBTrACS build process exited with status {status} before reading the file",
        )
    if error == "MemoryError":
        raise fetch.FetchError("build_failed", "the IBTrACS build process ran out of memory")
    raise fetch.FetchError("upstream_parse", f"{error or f'exit status {status}'} building IBTrACS")


def _kept(data_dir: Path) -> tuple[Path, str | None]:
    """The complete download kept after a failed build, and its
    Last-Modified (None: nothing kept)."""
    kept = data_dir / CSV_NAME
    stamp = data_dir / (CSV_NAME + ".last-modified")
    try:
        modified = stamp.read_text(encoding="utf-8").strip() if kept.exists() else None
    except OSError:
        modified = None
    return kept, modified or None


async def _ingest() -> None:
    assert _state.data_dir is not None
    data_dir = _state.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    current = _state.index
    kept, kept_modified = _kept(data_dir)
    stamp = data_dir / (CSV_NAME + ".last-modified")
    headers = {}
    if kept_modified:
        # a kept download is newer than any index: revalidate IT
        headers["If-Modified-Since"] = kept_modified
    elif current is not None and current.head.get("last_modified"):
        headers["If-Modified-Since"] = current.head["last_modified"]
    if shutil.disk_usage(data_dir).free < MIN_FREE:
        raise fetch.FetchError("busy", "less than 1 GB free in the data directory for IBTrACS")
    tmp = data_dir / (CSV_NAME + ".tmp")
    try:
        response = await fetch.upstream.download(URL, tmp, headers=headers)
        _state.read_at = datetime.now(UTC)
        if response.status_code == 304:
            if not kept_modified:
                return  # the index is the upstream's current file
            modified = kept_modified  # unchanged upstream: build from the kept copy
        else:
            modified = response.headers.get("last-modified") or format_datetime(
                datetime.now(UTC), usegmt=True
            )
            os.replace(tmp, kept)  # complete: kept until an index is built from it
            stamp.write_text(modified, encoding="utf-8")
        out = data_dir / INDEX_NAME
        await _build_in_child(kept, out, modified)
        index = await fetch.in_thread(load_index, str(out))
        if index is None:
            raise fetch.FetchError("upstream_parse", "the IBTrACS index could not be read back")
        _state.index = index
        kept.unlink(missing_ok=True)
        stamp.unlink(missing_ok=True)
        log.info("IBTrACS index built: %s storms", index.head.get("storms"))
    finally:
        tmp.unlink(missing_ok=True)  # a partial download is never kept


def retry_delay(failures: int) -> float:
    """Seconds before another first ingest after `failures` in a row."""
    return min(RETRY_MAX_S, RETRY_FIRST_S * 2 ** max(0, failures - 1))


async def _run_ingest() -> None:
    try:
        await _ingest()
        _state.error = None
        _state.failures = 0
        _state.retry_at = 0.0
    except Exception as exc:  # noqa: BLE001 -- a failed ingest is a block error, never a crash
        if isinstance(exc, fetch.FetchError):
            _state.error = exc
        else:
            _state.error = fetch.FetchError(
                "upstream_parse", f"{type(exc).__name__} building IBTrACS"
            )
            log.warning("IBTrACS ingest: %s", type(exc).__name__)
        if _state.index is None:
            # no index to serve: back off, never the 60 s failure memory
            _state.failures += 1
            _state.retry_at = time.monotonic() + retry_delay(_state.failures)
    finally:
        _state.checked = time.monotonic()
        _state.task = None


async def _load_disk() -> None:
    assert _state.data_dir is not None
    try:
        path = _state.data_dir / INDEX_NAME
        if path.exists():
            index = await fetch.in_thread(load_index, str(path))
            if index is not None:
                _state.index = index
                _state.checked = time.monotonic()
                _state.read_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    finally:
        _state.disk_loaded = True
        _state.loading = None


async def ensure(ctx: Context) -> None:
    """Loads the index from disk once (every concurrent caller waits for
    that one load), and starts the 24 h check or the first ingest when due.
    Never waits for the ingest itself."""
    if _state.data_dir is None:
        return
    if not _state.disk_loaded:
        if _state.loading is None:
            _state.loading = fetch.spawn(_load_disk(), "place:ibtracs-disk")
        await asyncio.shield(_state.loading)
    due = _state.index is None or time.monotonic() - _state.checked > CHECK_EVERY
    if _state.task is None and due:
        if time.monotonic() < _state.retry_at:
            return  # a failed first ingest is backing off
        if _state.index is None:
            # the first ingest is a new fetch this report starts; the daily
            # check of an index already served is the product's, not the client's
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
