#!/usr/bin/env python3
"""Core benchmark: what the hot paths of the backend cost, on real journals.

Network-free and in-process, so the numbers are the server's own work and
nothing else: no source is started, no lifespan runs, the HTTP routes are
driven through ASGI. What it measures:

  * replaying journals at startup (read + pipeline), per line;
  * the per-second work the heartbeat owes (`store.stats()`, the tick
    message, its JSON) with a FULL ring -- production runs at 4999/5000;
  * the snapshot a new socket costs, chunked and not;
  * one `upsert` of each kind (new with eviction, revision, repeat);
  * the REST routes, p50 / p95 through ASGI (serialization included);
  * the ring's memory, per event, from tracemalloc.

Real journals rarely hold 5000 distinct events, so `--fill` tops the ring up
with copies of the real events under new ids: real sizes, real shapes, the
production ring's occupancy.

Usage (from the repo root):
  backend/.venv/bin/python tools/bench_core.py JOURNAL.jsonl [...] --fill
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
# before importing the app: no journal written, nothing created in the repo
os.environ.setdefault("SOS_DATA_DIR", tempfile.mkdtemp(prefix="sos-bench-"))
os.environ.setdefault("SOS_PERSIST_JSONL", "false")


def _timeit(fn, repeat: int) -> tuple[float, float]:
    """(median, p95) wall time of `fn` in milliseconds."""
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return statistics.median(samples), samples[min(len(samples) - 1, int(0.95 * len(samples)))]


async def _bench(args: argparse.Namespace) -> dict:
    from app import main as m
    from app.scope import compose, split
    from app.store.ring import EventStore

    m.sources = m.build_sources()
    out: dict = {}

    # --- replay, as the lifespan does it
    tracemalloc.start()
    mem0 = tracemalloc.get_traced_memory()[0]
    lines = 0
    t0 = time.perf_counter()
    read_s = 0.0
    for journal in args.journals:
        r0 = time.perf_counter()
        entries = EventStore.read_journal(Path(journal))
        read_s += time.perf_counter() - r0
        lines += Path(journal).read_bytes().count(b"\n")
        out.setdefault("journal_entries_kept", 0)
        out["journal_entries_kept"] += len(entries)
        await m.pipeline.replay(Path(journal))
    out["replay_s"] = time.perf_counter() - t0
    out["replay_read_s"] = read_s
    out["journal_lines"] = lines
    out["journal_bytes"] = sum(Path(j).stat().st_size for j in args.journals)
    out["restored"] = m.store.stats()["total_buffered"]

    if args.fill:
        real = m.store.recent(limit=m.settings.ring_size)
        n = 0
        while len(m.store._ring) < m.settings.ring_size and real:
            src = real[n % len(real)]
            clone = src.model_copy(deep=True)
            clone.id = f"{src.id}#fill{n}"
            clone.cluster_id = None
            m.store.upsert(clone)
            n += 1
        out["filled_copies"] = n
    out["ring"] = len(m.store._ring)
    mem1 = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    out["ring_mem_mb"] = (mem1 - mem0) / 1e6
    out["ring_bytes_per_event"] = (mem1 - mem0) / max(1, out["ring"])

    r = args.repeat
    out["stats_ms"] = _timeit(m.store.stats, r)
    out["tick_build_ms"] = _timeit(m.tick_message, r)
    tick = m.tick_message()
    out["tick_json_ms"] = _timeit(lambda: json.dumps(tick, default=str), r)
    out["tick_bytes"] = len(json.dumps(tick, default=str))
    out["recent_all_ms"] = _timeit(
        lambda: m.store.recent(limit=m.settings.ring_size, primary_only=True), r
    )

    def snapshot(pin: bool) -> int:
        events, _ = compose(
            m.store.recent(limit=m.settings.ring_size, primary_only=True),
            m.settings.snapshot_size,
            None,
            pin=pin,
        )
        publics = [e.public() for e in events]
        parts = [publics]
        if pin:
            parts = split(publics, m.SNAPSHOT_CHUNK_EVENTS, m.SNAPSHOT_CHUNK_BYTES)
        return sum(len(json.dumps(c, default=str)) for c in parts)

    out["snapshot_legacy_ms"] = _timeit(lambda: snapshot(False), max(5, r // 4))
    out["snapshot_chunked_ms"] = _timeit(lambda: snapshot(True), max(5, r // 4))
    out["snapshot_chunked_bytes"] = snapshot(True)
    # the shared snapshot, when the code under test has one (round 2, lane P):
    # a miss is the old cost, a hit is what every other socket pays
    shared = getattr(m, "shared_snapshot", None)
    if shared is not None:

        def miss() -> None:
            m._shared_snapshots.clear()
            shared(True)

        out["snapshot_shared_miss_ms"] = _timeit(miss, max(5, r // 4))
        out["snapshot_shared_hit_ms"] = _timeit(lambda: shared(True), r)

    # --- one upsert of each kind, on the full ring
    victims = m.store.recent(limit=m.settings.ring_size)
    middle = victims[len(victims) // 2]

    def revise() -> None:
        changed = middle.model_copy(deep=True)
        changed.title = f"{middle.title} r{time.perf_counter_ns()}"
        m.store.upsert(changed)

    def repeat_() -> None:
        m.store.upsert(m.store.get(middle.id).model_copy())

    counter = iter(range(10**9))

    def insert() -> None:
        fresh = middle.model_copy(deep=True)
        fresh.id = f"bench:new{next(counter)}"
        fresh.cluster_id = fresh.id
        m.store.upsert(fresh)

    out["upsert_update_ms"] = _timeit(revise, r)
    out["upsert_noop_ms"] = _timeit(repeat_, r)
    out["upsert_new_ms"] = _timeit(insert, r)
    out["prune_stale_ms"] = _timeit(lambda: m.store.prune_stale(24 * 365), max(5, r // 4))

    # --- REST through ASGI: routing + validation + serialization
    transport = httpx.ASGITransport(app=m.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
        for name, url in (
            ("api_events", "/api/events"),
            ("api_events_2000", "/api/events?limit=2000"),
            ("api_events_v2_full", "/api/events?since="),
            ("api_sources", "/api/sources"),
            ("api_stats", "/api/stats"),
        ):
            samples = []
            size = 0
            for _ in range(args.http_repeat):
                t0 = time.perf_counter()
                resp = await client.get(url)
                samples.append((time.perf_counter() - t0) * 1000)
                size = len(resp.content)
            samples.sort()
            out[f"{name}_ms"] = (
                statistics.median(samples),
                samples[min(len(samples) - 1, int(0.95 * len(samples)))],
            )
            out[f"{name}_bytes"] = size
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("journals", nargs="+")
    ap.add_argument("--fill", action="store_true", help="top the ring up to ring_size")
    ap.add_argument("--repeat", type=int, default=50)
    ap.add_argument("--http-repeat", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(_bench(args))
    if args.json:
        print(json.dumps(report))
        return 0
    for key, value in report.items():
        if isinstance(value, tuple):
            print(f"{key:26} p50 {value[0]:9.3f}  p95 {value[1]:9.3f}")
        elif isinstance(value, float):
            print(f"{key:26} {value:.3f}")
        else:
            print(f"{key:26} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
