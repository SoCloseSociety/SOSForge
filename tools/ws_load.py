#!/usr/bin/env python3
"""Websocket load: what one tick costs the server with N clients attached.

The product's promise is a `tick` every second to every open tab, and a tab
that misses 15 s of them declares itself disconnected. So the questions are
numbers, not impressions:

  * does every client still get one tick per second at N clients, or does the
    period stretch (drift) as the fan-out grows;
  * how late does the LAST client get a tick, measured from the `server_time`
    the server stamped on it (same machine, same clock);
  * what that costs the server process, in CPU and memory, sampled from `ps`
    over the measurement window only (the connection storm is excluded).

Clients negotiate permessage-deflate, like a browser does, so the server pays
the per-client compression it pays in production.

Usage:
  backend/.venv/bin/python tools/ws_load.py --clients 100 --seconds 30 \
      --pid "$(lsof -ti tcp:8335 -sTCP:LISTEN)"
  --procs spreads the clients over several processes: at 1000 clients one
  Python process decoding every frame becomes the bottleneck, and the tool
  would then be measuring itself.

The server caps sockets per address (SOS_MAX_WS_PER_IP, 24 by default): start
the server under test with a higher cap or every client past the 24th is
refused with close code 1013.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import multiprocessing as mp
import statistics
import subprocess
import sys
import time
from datetime import datetime

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("pip install websockets (it is in the backend venv)")

TICK_PREFIX = '{"type": "tick", "server_time": "'


def _server_time(payload: str) -> float | None:
    """Epoch seconds of a tick, read without parsing the 8 KB body: at 1000
    clients a full `json.loads` per frame is what the tool would measure."""
    if not payload.startswith(TICK_PREFIX):
        return None
    start = len(TICK_PREFIX)
    end = payload.index('"', start)
    return datetime.fromisoformat(payload[start:end]).timestamp()


async def _client(url: str, start_at: float, end_at: float, out: dict) -> None:
    t0 = time.time()
    try:
        async with websockets.connect(url, max_size=None, open_timeout=60) as ws:
            first = await ws.recv()
            out["snapshot_s"] = time.time() - t0
            out["snapshot_bytes"] = len(first)
            ticks: list[tuple[float, float]] = []
            frames = 0
            received = 0
            while True:
                remaining = end_at - time.time()
                if remaining <= 0:
                    break
                try:
                    payload = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except TimeoutError:
                    break
                now = time.time()
                if now < start_at:
                    continue
                frames += 1
                received += len(payload)
                if isinstance(payload, str):
                    stamped = _server_time(payload)
                    if stamped is not None:
                        ticks.append((now, stamped))
            out["ticks"] = ticks
            out["frames"] = frames
            out["bytes"] = received
    except Exception as exc:  # noqa: BLE001 -- a refused client is a result
        out["error"] = f"{type(exc).__name__}: {exc}"


async def _worker_main(url: str, count: int, start_at: float, end_at: float, ramp: float) -> list:
    results: list[dict] = [{} for _ in range(count)]
    tasks = []
    for i in range(count):
        tasks.append(asyncio.create_task(_client(url, start_at, end_at, results[i])))
        if ramp:
            await asyncio.sleep(1 / ramp)
    await asyncio.gather(*tasks)
    return results


def _worker(args: tuple) -> list:
    url, count, start_at, end_at, ramp = args
    return asyncio.run(_worker_main(url, count, start_at, end_at, ramp))


def _cpu_seconds(pid: int) -> float:
    """Cumulative user+system CPU of a process, from `ps` (no psutil)."""
    raw = subprocess.check_output(["ps", "-o", "time=", "-p", str(pid)], text=True).strip()
    days = 0
    if "-" in raw:
        d, raw = raw.split("-", 1)
        days = int(d)
    parts = [float(p) for p in raw.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return days * 86400 + seconds


def _rss_mb(pid: int) -> float:
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True)) / 1024


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8335/ws")
    ap.add_argument("--clients", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=30.0, help="measurement window")
    ap.add_argument("--procs", type=int, default=1)
    ap.add_argument(
        "--ramp",
        type=float,
        default=200.0,
        help="new connections per second, all procs together",
    )
    ap.add_argument("--pid", type=int, help="server pid, for CPU and RSS")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    procs = max(1, min(args.procs, args.clients))
    per = [args.clients // procs + (1 if i < args.clients % procs else 0) for i in range(procs)]
    # connection storm first, measurement after: a snapshot costs far more
    # than a tick, and mixing the two would blame the tick for it
    settle = 3.0 + args.clients / max(1.0, args.ramp)
    start_at = time.time() + settle
    end_at = start_at + args.seconds

    cpu0 = rss0 = None
    with mp.Pool(procs) as pool:
        pending = pool.map_async(
            _worker, [(args.url, n, start_at, end_at, args.ramp / procs) for n in per]
        )
        if args.pid:
            while time.time() < start_at:
                time.sleep(0.05)
            cpu0, rss0 = _cpu_seconds(args.pid), _rss_mb(args.pid)
            while time.time() < end_at:
                time.sleep(0.05)
            cpu1, rss1 = _cpu_seconds(args.pid), _rss_mb(args.pid)
        results = [r for chunk in pending.get() for r in chunk]

    ok = [r for r in results if "error" not in r]
    errors: dict[str, int] = {}
    for r in results:
        if "error" in r:
            errors[r["error"]] = errors.get(r["error"], 0) + 1

    latencies = [now - stamped for r in ok for now, stamped in r.get("ticks", [])]
    per_client = [len(r.get("ticks", [])) for r in ok]
    gaps = []
    for r in ok:
        arrivals = [now for now, _ in r.get("ticks", [])]
        gaps.extend(b - a for a, b in itertools.pairwise(arrivals))
    # Drift: the server's own stamps on consecutive ticks. 1.000 s is the
    # promise; anything above is time the heartbeat spent working that it
    # then failed to subtract from its sleep.
    periods = []
    if ok and ok[0].get("ticks"):
        stamps = [stamped for _, stamped in ok[0]["ticks"]]
        periods = [b - a for a, b in itertools.pairwise(stamps)]

    report = {
        "clients": args.clients,
        "connected": len(ok),
        "errors": errors,
        "window_s": args.seconds,
        "snapshot_s_p50": _pct([r["snapshot_s"] for r in ok], 0.5),
        "snapshot_s_p95": _pct([r["snapshot_s"] for r in ok], 0.95),
        "ticks_per_client_min": min(per_client) if per_client else 0,
        "ticks_per_client_median": statistics.median(per_client) if per_client else 0,
        "tick_latency_ms_p50": 1000 * _pct(latencies, 0.5),
        "tick_latency_ms_p95": 1000 * _pct(latencies, 0.95),
        "tick_latency_ms_p99": 1000 * _pct(latencies, 0.99),
        "tick_latency_ms_max": 1000 * max(latencies) if latencies else float("nan"),
        "tick_gap_s_max": max(gaps) if gaps else float("nan"),
        "tick_period_s_mean": statistics.fmean(periods) if periods else float("nan"),
        "tick_period_s_p99": _pct(periods, 0.99),
        "tick_period_s_max": max(periods) if periods else float("nan"),
        # ticks the server owed over the window and did not stamp: the drift,
        # counted rather than averaged
        "ticks_short": (round(args.seconds - len(periods) - 1) if periods else 0),
        "bytes_per_client_per_s": (
            statistics.fmean(r.get("bytes", 0) for r in ok) / args.seconds if ok else 0
        ),
    }
    if args.pid and cpu0 is not None:
        report["server_cpu_pct"] = 100 * (cpu1 - cpu0) / args.seconds
        report["server_rss_mb_start"] = rss0
        report["server_rss_mb_end"] = rss1
    if args.json:
        print(json.dumps(report))
    else:
        for key, value in report.items():
            print(f"{key:28} {value:.3f}" if isinstance(value, float) else f"{key:28} {value}")
    return 0 if len(ok) == args.clients else 1


if __name__ == "__main__":
    sys.exit(main())
