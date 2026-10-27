#!/usr/bin/env python3
"""Geocode fairness: how long one visitor waits behind another's burst.

Nominatim allows one request per second for the whole product, so
`/api/geocode` holds a single queue. The question this measures is who pays
for a burst: one address fires N distinct searches at once (a script, a
search box without debounce), and a second address searches once just
after. Nominatim itself is replaced by an instant mock -- nothing leaves the
machine -- so every millisecond reported is the product's own queueing.

Usage (from the repo root):
  backend/.venv/bin/python tools/bench_geocode.py --burst 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("SOS_DATA_DIR", tempfile.mkdtemp(prefix="sos-bench-"))
os.environ.setdefault("SOS_PERSIST_JSONL", "false")


async def _run(burst: int) -> dict:
    from app import geocode
    from app import main as m

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        return httpx.Response(200, json=[{"display_name": q, "lat": "1", "lon": "2"}])

    geocode._transport = httpx.MockTransport(handler)
    geocode._cache.clear()

    def client(address: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=m.app, client=(address, 40000))
        return httpx.AsyncClient(transport=transport, base_url="http://bench")

    async with client("203.0.113.5") as noisy, client("198.51.100.7") as quiet:

        async def timed(c: httpx.AsyncClient, q: str) -> tuple[float, int]:
            t0 = time.perf_counter()
            resp = await c.get("/api/geocode", params={"q": q})
            return time.perf_counter() - t0, resp.status_code

        storm = [asyncio.create_task(timed(noisy, f"place {i}")) for i in range(burst)]
        await asyncio.sleep(0.05)
        other = await timed(quiet, "tokyo")
        burst_results = await asyncio.gather(*storm)
    statuses: dict[int, int] = {}
    for _, status in burst_results:
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "burst": burst,
        "other_visitor_wait_s": round(other[0], 3),
        "other_visitor_status": other[1],
        "burst_statuses": statuses,
        "burst_slowest_s": round(max(t for t, _ in burst_results), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--burst", type=int, default=10)
    args = ap.parse_args()
    print(json.dumps(asyncio.run(_run(args.burst))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
