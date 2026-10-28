"""The IBTrACS index build: the ALL CSV (333 MB) -> the packed index that
`app.place.cyclones` loads and queries.

It runs as its own short-lived process, one per build:
`python -m app.ibtracs_index CSV OUT LAST_MODIFIED`. It lives outside
`app.place` on purpose: importing any module under that package runs its
`__init__`, which imports every block's module, and that cost the child
21 MiB of RSS it never uses (48.9 against 27.7 MiB, import only, measured
2026-10-28). Nothing here imports the rest of the app.

Memory, measured on the 2026-10-27 file (720 997 rows, 13 339 storms) with
`/usr/bin/time -l`, three runs each: the first version peaked at 360-363
MiB in its pool worker, because every value of every row was a Python
object (a float per latitude, an int per minute) and the segment grid then
built five Python lists of 707k ints and two lists of 1.26 million cells,
all alive together. Typed buffers during the parse and a chunked counting
sort for the grid peak at 78-87 MiB and write a byte-identical index (same
sha256 from that file, and from the test fixtures).
"""

from __future__ import annotations

import csv
import json
import os
import sys
import traceback
from array import array
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

FORMAT = 1
EPOCH = datetime(1800, 1, 1, tzinfo=UTC)
NATURES = ("DS", "TS", "ET", "SS", "NR", "MX")
CELLS = 180 * 360  # the 1-degree grid
# segments per step of the grid (about 30k pairs): 1 << 16 cost about 12 MiB
# more at the peak, for no measurable time
CHUNK = 1 << 14


def _minutes(text: str) -> int:
    when = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return int((when - EPOCH).total_seconds() // 60)


def _wind(text: str) -> int:
    text = text.strip()
    return int(float(text)) if text else -1


def _pairs(seg: np.ndarray, p_lat: np.ndarray, p_lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The (cell, segment) pairs of the 1-degree grid for these segments, in
    the order of the loop this replaces:

        for j in seg:  # point j -> j + 1 of the same storm
            a, b = floor(min(lat[j], lat[j + 1])), floor(max(...))
            c, d = floor(min(lon[j], lon[j + 1])), floor(max(...))
            for y in range(max(-90, a), min(89, b) + 1):
                for x in range(c, d + 1):
                    cells.append((y + 90) * 360 + (x + 180) % 360)
                    owners.append(j)

    segment, then latitude, then longitude; the same float32 arithmetic."""
    lat_a, lat_b = p_lat[seg], p_lat[seg + 1]
    lon_a, lon_b = p_lon[seg], p_lon[seg + 1]
    lat_lo = np.floor(np.minimum(lat_a, lat_b)).astype(np.int32)
    lat_hi = np.floor(np.maximum(lat_a, lat_b)).astype(np.int32)
    lon_lo = np.floor(np.minimum(lon_a, lon_b)).astype(np.int32)
    lon_hi = np.floor(np.maximum(lon_a, lon_b)).astype(np.int32)
    y0 = np.maximum(lat_lo, -90)
    nx = np.maximum(lon_hi - lon_lo + 1, 0)
    count = np.maximum(np.minimum(lat_hi, 89) - y0 + 1, 0) * nx
    # rank of each pair inside its own segment's block of ny * nx
    rank = np.arange(int(count.sum(dtype=np.int64))) - np.repeat(np.cumsum(count) - count, count)
    y, x = np.divmod(rank, np.repeat(nx, count))
    y += np.repeat(y0, count)
    x += np.repeat(lon_lo, count)
    # numpy's % on integers is Python's: west of -180 wraps to the east
    cells = ((y + 90) * 360 + (x + 180) % 360).astype(np.int32)
    return cells, np.repeat(seg, count)


def _grid(
    p_lat: np.ndarray, p_lon: np.ndarray, p_storm: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(offsets, segments): the segments crossing cell c are
    `segments[offsets[c]:offsets[c + 1]]`, each cell's in the loop's order.

    That is a stable sort of the pairs by cell, which the first version did
    on all of them at once (1.26 million pairs: lists of Python ints, an
    int64 argsort). Done here CHUNK segments at a time in two passes, a
    counting sort: count the pairs of every cell, then put each pair at the
    next free place of its cell, chunk after chunk in loop order (and in
    loop order inside a chunk, by a stable argsort), so every cell lists
    its segments in exactly the order the global stable sort did. Only the
    output is ever whole."""
    seg = np.nonzero(p_storm[:-1] == p_storm[1:])[0].astype(np.int32)
    counts = np.zeros(CELLS, dtype=np.int64)
    for i in range(0, len(seg), CHUNK):
        cells, _ = _pairs(seg[i : i + CHUNK], p_lat, p_lon)
        counts += np.bincount(cells, minlength=CELLS)
    offsets = np.zeros(CELLS + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    segments = np.empty(int(offsets[-1]), dtype=np.int32)
    free = offsets[:-1].copy()  # the next free place of every cell
    for i in range(0, len(seg), CHUNK):
        cells, owners = _pairs(seg[i : i + CHUNK], p_lat, p_lon)
        order = np.argsort(cells, kind="stable")
        ordered = cells[order]
        # rank of each pair among this chunk's pairs of the same cell
        rank = np.arange(len(ordered)) - np.searchsorted(ordered, ordered, side="left")
        segments[free[ordered] + rank] = owners[order]
        free += np.bincount(cells, minlength=CELLS)
    return offsets.astype(np.int32), segments


def build_index(csv_path: str, out_path: str, last_modified: str) -> dict[str, Any]:
    """CSV -> packed index file (temporary file, then rename). Returns the
    header it wrote."""
    lat = array("d")
    lon = array("d")
    tmin = array("i")  # C int: 32 bits wherever numpy runs
    usa = array("h")
    wmo = array("h")
    nature = array("B")
    storm = array("i")
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
    del index, last_lon
    # the same double -> float32 rounding as from a list of Python floats
    p_lat = np.asarray(lat, dtype=np.float32)
    p_lon = np.asarray(lon, dtype=np.float32)
    del lat, lon
    p_storm = np.asarray(storm, dtype=np.int32)
    # 1-degree grid of SEGMENTS (point j -> j+1 of the same storm)
    offsets, seg_sorted = _grid(p_lat, p_lon, p_storm)
    agency_of = [c.most_common(1)[0][0] if c else "" for c in agencies]
    del agencies
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


def _first_to_kill() -> None:
    """Inside a memory cgroup the OOM killer takes the task with the most
    RSS + swap + page tables, plus oom_score_adj thousandths of the cgroup's
    limit (mm/oom_kill.c, oom_badness). The API (220-290 MB) outweighs this
    build, so without this an overrun would kill the API and restart the
    container; at 1000 this child goes first whatever its size. Raising
    one's own score needs no privilege. No /proc (not Linux): nothing to do."""
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as f:
            f.write("1000")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    """One build. One JSON line on stdout for the parent: {"head": ...}, or
    {"error": "<exception type>"} with the traceback on stderr (the
    container log) and exit status 1."""
    _first_to_kill()
    csv_path, out_path, last_modified = argv
    try:
        head = build_index(csv_path, out_path, last_modified)
    except Exception as exc:  # noqa: BLE001 -- reported to the parent, which decides
        traceback.print_exc()
        print(json.dumps({"error": type(exc).__name__}), flush=True)
        return 1
    print(json.dumps({"head": head}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
