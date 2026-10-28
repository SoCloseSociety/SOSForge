"""The IBTrACS index build: what it writes, what it costs, and what happens
when the kernel kills it (2026-10-28).

The build ran in a ProcessPoolExecutor worker and peaked at 360-363 MiB RSS
on the 333 MB ALL file. It now runs as `python -m app.ibtracs_index`, one
fresh process per build (78-87 MiB, same file, `/usr/bin/time -l`), and
writes the same bytes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import httpx
import pytest
import yaml

from app import main
from app.geocode import AddressBudget
from app.place import cyclones
from tests.place_helpers import FIXTURES, at, ctx_for, install

NOW = at("2026-10-27T11:00:00Z")
LAST_MODIFIED = "Sun, 25 Oct 2026 09:05:37 GMT"
HISTORY = FIXTURES / "history"
COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _gay_csv(folder: Path) -> Path:
    """The ALL file's header and units rows, then GAY 1989's rows: two range
    reads of the same file."""
    path = folder / "all.csv"
    path.write_bytes(
        (HISTORY / "ibtracs_ALL_range_header.csv").read_bytes()
        + (HISTORY / "ibtracs_ALL_range_GAY1989.csv").read_bytes()
    )
    return path


# ------------------------------------------------------------ same bytes

# sha256 of the index files 8111e5a's build_index wrote from these verbatim
# captures (numpy 2.5.4, Python 3.12). On the full 333 MB file of
# 2026-10-27 (Last-Modified 09:03:14) both versions wrote 778d2798...bce19,
# three runs each.
DIGEST_8111E5A = {
    "gay": "487fa8b4f4f48dcec407f6ceebfcde81d28c069867068256e62b86ce43988549",
    "active": "d10b76bfbf36ccbcfbaf9e62464a05804e659bca24f33d047ff86d54cd98b908",
}


def test_the_index_is_byte_identical_to_the_first_build(tmp_path: Path) -> None:
    cases = {
        "gay": (_gay_csv(tmp_path), "Sun, 25 Oct 2026 09:05:37 GMT"),
        "active": (HISTORY / "ibtracs_ACTIVE.list.v04r01.csv", "Sun, 25 Oct 2026 09:05:07 GMT"),
    }
    for name, (csv_path, modified) in cases.items():
        out = tmp_path / f"{name}.bin"
        cyclones.build_index(str(csv_path), str(out), modified)
        assert hashlib.sha256(out.read_bytes()).hexdigest() == DIGEST_8111E5A[name], name


# ------------------------------------------------------- what it costs

# The traced peak (Python objects + numpy buffers) grows by this much per
# extra row between the two verbatim captures: 294 B/row on 8111e5a (a
# Python float per latitude and longitude, an int per minute, Python lists
# for the grid), 86 B/row now (typed buffers, the grid in chunks). The
# constant part is not judged: the counting sort's four 64 800-cell tables
# cost the new build about 1 MB more on a 79-row file.
MAX_BYTES_PER_ROW = 160


def _traced_peak(csv_path: Path, out: Path, modified: str) -> tuple[int, int]:
    cyclones.build_index(str(csv_path), str(out), modified)  # imports and caches warm
    tracemalloc.start()
    try:
        head = cyclones.build_index(str(csv_path), str(out), modified)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return peak, int(head["rows"])


def test_the_build_holds_no_python_object_per_row(tmp_path: Path) -> None:
    small, small_rows = _traced_peak(_gay_csv(tmp_path), tmp_path / "gay.bin", LAST_MODIFIED)
    large, large_rows = _traced_peak(
        HISTORY / "ibtracs_ACTIVE.list.v04r01.csv", tmp_path / "active.bin", LAST_MODIFIED
    )
    assert (small_rows, large_rows) == (79, 309)
    per_row = (large - small) / (large_rows - small_rows)
    assert per_row < MAX_BYTES_PER_ROW, f"{per_row:.0f} B per row"


# --------------------------------------------------- a killed build process


def _children() -> set[int]:
    """This process's live children, without multiprocessing's resource
    tracker and without the `ps` that lists them."""
    ps = subprocess.Popen(
        ["ps", "-A", "-o", "pid=,ppid=,stat=,command="], stdout=subprocess.PIPE, text=True
    )
    out, _ = ps.communicate()
    me, found = os.getpid(), set()
    for line in out.splitlines():
        pid, ppid, stat, command = line.split(None, 3)
        if int(ppid) != me or int(pid) == ps.pid or stat.startswith("Z"):
            continue
        if "resource_tracker" not in command:
            found.add(int(pid))
    return found


async def _new_child(before: set[int]) -> int:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        new = _children() - before
        if new:
            assert len(new) == 1, new
            return new.pop()
        await asyncio.sleep(0.05)
    raise AssertionError("no build process was started")


async def _oom_score_adj(pid: int) -> str | None:
    """The child's oom_score_adj once it has set it (None without /proc)."""
    path = Path(f"/proc/{pid}/oom_score_adj")
    if not Path("/proc/self/oom_score_adj").exists():
        return None
    value = ""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = path.read_text().strip()
        if value == "1000":
            break
        await asyncio.sleep(0.05)
    return value


def _blocked_download(folder: Path) -> Path:
    """A kept download the build process can only block on: a FIFO nobody
    writes to, so it is reliably still running when the test kills it,
    whatever the machine's speed."""
    kept = folder / cyclones.CSV_NAME
    os.mkfifo(kept)
    (folder / (cyclones.CSV_NAME + ".last-modified")).write_text(LAST_MODIFIED)
    return kept


def _real_download(kept: Path) -> None:
    kept.unlink()
    _gay_csv(kept.parent).replace(kept)


async def _kill_the_build(ctx) -> None:  # type: ignore[no-untyped-def]
    """Starts the ingest, SIGKILLs its build process the way the kernel's
    OOM killer does, and waits for the ingest to settle."""
    before = _children()
    await cyclones.ensure(ctx)
    task = cyclones._state.task
    assert task is not None
    pid = await _new_child(before)
    adj = await _oom_score_adj(pid)
    assert adj in (None, "1000")  # Linux: the build is the OOM killer's first pick
    os.kill(pid, signal.SIGKILL)
    await asyncio.wait_for(task, 30)


async def test_a_killed_first_build_backs_off_says_why_and_the_api_keeps_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = install()
    cyclones.configure(tmp_path)
    kept = _blocked_download(tmp_path)
    gets: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        gets.append(request)
        return httpx.Response(304)  # unchanged upstream: build from the kept copy

    router.handler(cyclones.URL, answer)
    ctx = ctx_for(9.512, 100.013, NOW)
    await _kill_the_build(ctx)
    assert gets[0].headers["if-modified-since"] == LAST_MODIFIED

    error = cyclones._state.error
    assert error is not None and error.code == "build_failed"
    assert "SIGKILL (signal 9)" in error.reason and "out-of-memory killer" in error.reason
    # backing off, the kept download kept for the retry
    assert cyclones._state.failures == 1
    assert cyclones._state.retry_at - time.monotonic() > 9 * 60
    assert kept.exists()
    await cyclones.ensure(ctx)
    assert cyclones._state.task is None and len(gets) == 1

    block = (await cyclones.build(ctx)).public(NOW, "fr")
    assert block["status"] == "unavailable" and block["error"]["code"] == "build_failed"
    assert block["error"]["message"].startswith("Le serveur n’a pas pu finir")

    # the API process still answers, with that reason in the report
    monkeypatch.setattr(main, "place_budget", AddressBudget(burst=50, per_second=50))
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sos") as client:
        r = await client.get("/api/place", params={"lat": "9.512", "lon": "100.013"})
    assert r.status_code == 200
    blocks = r.json()["blocks"]
    assert len(blocks) == 22
    assert blocks["history.cyclones"]["error"]["code"] == "build_failed"
    assert "SIGKILL" in blocks["history.cyclones"]["error"]["reason"]
    assert len(gets) == 1  # the report started no ingest while backing off

    # once due, the retry runs a NEW build process, which succeeds
    _real_download(kept)
    cyclones._state.retry_at = 0.0
    await cyclones.ensure(ctx)
    assert cyclones._state.task is not None
    await asyncio.wait_for(cyclones._state.task, 60)
    assert cyclones._state.error is None and cyclones._state.index is not None
    assert cyclones._state.index.head["last_modified"] == LAST_MODIFIED
    assert not kept.exists()  # deleted once built


async def test_a_killed_rebuild_keeps_serving_the_old_index_and_says_why(
    tmp_path: Path,
) -> None:
    router = install()
    served = "Fri, 23 Oct 2026 09:00:00 GMT"
    cyclones.build_index(str(_gay_csv(tmp_path)), str(tmp_path / cyclones.INDEX_NAME), served)
    (tmp_path / "all.csv").unlink()
    cyclones.configure(tmp_path)
    router.handler(cyclones.URL, lambda request: httpx.Response(304))
    ctx = ctx_for(9.512, 100.013, NOW)
    await cyclones.ensure(ctx)  # the index on disk, loaded
    assert cyclones._state.index is not None and cyclones._state.task is None

    # a newer download kept by an earlier attempt, and the daily check due
    kept = _blocked_download(tmp_path)
    cyclones._state.checked -= cyclones.CHECK_EVERY + 1
    await _kill_the_build(ctx)

    block = (await cyclones.build(ctx)).public(NOW, "en")
    assert block["status"] == "ok" and block["data"]["total_storms"] == 1  # the old index
    assert block["error"]["code"] == "build_failed" and "SIGKILL" in block["error"]["reason"]
    assert kept.exists()
    assert cyclones._state.failures == 0  # an index is served: the 24 h check, no backoff

    # the next check runs a NEW build process and swaps the index
    _real_download(kept)
    cyclones._state.checked -= cyclones.CHECK_EVERY + 1
    await cyclones.ensure(ctx)
    assert cyclones._state.task is not None
    await asyncio.wait_for(cyclones._state.task, 60)
    assert cyclones._state.error is None and cyclones._state.index is not None
    assert cyclones._state.index.head["last_modified"] == LAST_MODIFIED


async def test_a_build_process_that_cannot_start_is_our_failure_not_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = install()
    cyclones.configure(tmp_path)
    _gay_csv(tmp_path).replace(tmp_path / cyclones.CSV_NAME)
    (tmp_path / (cyclones.CSV_NAME + ".last-modified")).write_text(LAST_MODIFIED)
    router.handler(cyclones.URL, lambda request: httpx.Response(304))
    monkeypatch.setattr(cyclones, "CHILD", (str(tmp_path / "no-such-python"),))
    await cyclones.ensure(ctx_for(9.512, 100.013, NOW))
    assert cyclones._state.task is not None
    await cyclones._state.task
    error = cyclones._state.error
    assert error is not None and error.code == "build_failed"
    assert error.reason == "the IBTrACS build process could not start (FileNotFoundError)"
    assert cyclones._state.failures == 1 and (tmp_path / cyclones.CSV_NAME).exists()


async def test_a_build_process_that_dies_before_reading_is_our_failure_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An import error in the child exits 1 with no JSON line: before, that
    read as upstream_parse, blaming IBTrACS for our own environment."""
    router = install()
    cyclones.configure(tmp_path)
    _gay_csv(tmp_path).replace(tmp_path / cyclones.CSV_NAME)
    (tmp_path / (cyclones.CSV_NAME + ".last-modified")).write_text(LAST_MODIFIED)
    router.handler(cyclones.URL, lambda request: httpx.Response(304))
    monkeypatch.setattr(cyclones, "CHILD", (sys.executable, "-c", "import no_such_module_here"))
    await cyclones.ensure(ctx_for(9.512, 100.013, NOW))
    assert cyclones._state.task is not None
    await cyclones._state.task
    error = cyclones._state.error
    assert error is not None and error.code == "build_failed"
    assert error.reason == "the IBTrACS build process exited with status 1 before reading the file"
    assert cyclones._state.failures == 1 and (tmp_path / cyclones.CSV_NAME).exists()


# --------------------------------------------------------- the container


def test_the_api_container_has_a_memory_ceiling_and_no_swap_beyond_it() -> None:
    """docker-compose.yml: an overrun is paid inside the SOSForge container,
    never in the neighbours' memory or swap; and the ceiling holds the
    worst case its comment adds up from measurements (556 MiB)."""
    api = yaml.safe_load(COMPOSE.read_text())["services"]["api"]
    limit = str(api["mem_limit"])
    assert limit.endswith("m") and str(api["memswap_limit"]) == limit
    assert int(limit[:-1]) > 556
