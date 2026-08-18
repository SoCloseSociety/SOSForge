"""Area search: place name -> coordinates, via Nominatim (OpenStreetMap).

Why go through the backend instead of calling Nominatim from the browser:
their usage policy requires an identifying User-Agent and **at most one
request per second**. A direct call from every open tab would violate both,
and get us banned. Here there is a single exit point, rate limiting and
cache included.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SOSForge/1.0 (+https://github.com/SoCloseSociety/SOSForge)"

# the Nominatim policy: one request per second, no exceptions
_MIN_INTERVAL = 1.0
_last_call = 0.0
_lock = asyncio.Lock()

# area searches repeat a lot ("tokyo", "california"): a bounded cache avoids
# asking for them again
_cache: dict[str, list[dict]] = {}
_CACHE_MAX = 500


async def search(query: str, limit: int = 5) -> list[dict]:
    global _last_call

    key = query.strip().lower()
    if not key:
        return []
    if key in _cache:
        return _cache[key]

    # The lock protects ONLY the rate computation. Doing the HTTP request
    # under it froze the whole queue for the duration of the timeout (12 s):
    # ten users searching at the same time waited ten times that.
    async with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()

    try:
        async with httpx.AsyncClient(
            timeout=12.0,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        ) as client:
            resp = await client.get(
                NOMINATIM,
                params={"q": query, "format": "jsonv2", "limit": str(limit)},
            )
            resp.raise_for_status()
            payload = resp.json() or []
    except Exception as exc:
        # a failed search must never break the page: the local text filter
        # keeps working regardless
        log.warning("geocoding unavailable: %s", exc)
        return []

    results = []
    for row in payload:
        try:
            results.append(
                {
                    "name": row.get("display_name") or query,
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "type": row.get("type"),
                    # Nominatim bbox: [south, north, west, east], not GeoJSON order
                    "bbox": [float(v) for v in row.get("boundingbox", [])] or None,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = results
    return results
