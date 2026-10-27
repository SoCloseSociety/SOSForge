"""Area search: place name -> coordinates, via Nominatim (OpenStreetMap).

Why go through the backend instead of calling Nominatim from the browser:
their usage policy requires an identifying User-Agent and **at most one
request per second**. A direct call from every open tab would violate both,
and get us banned. Here there is a single exit point, rate limiting and
cache included.
"""

from __future__ import annotations

import asyncio
import ipaddress
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

# Injected by the tests so they can drive a real response shape without a
# network call. None means "the normal transport".
_transport: httpx.AsyncBaseTransport | None = None


def cached(query: str) -> bool:
    """Whether this search would be answered without reaching Nominatim."""
    return query.strip().lower() in _cache


class AddressBudget:
    """How many searches one address may send to Nominatim, as a token
    bucket: `burst` at once, then `per_second`.

    The 1 req/s queue above is shared by the whole product, which is the
    policy, but nothing said who it was shared WITH: measured with Nominatim
    mocked (tools/bench_geocode.py), one address firing 30 searches made
    every other visitor wait 29.98 s. The web debounces at 450 ms and
    aborts the previous search on a keystroke, so a person typing a name
    stays far inside a burst of 3; a script or a broken client does not, and
    is told to come back later instead of standing in front of everyone.
    """

    def __init__(self, burst: float = 3.0, per_second: float = 1.0, max_addresses: int = 10_000):
        self.burst = burst
        self.per_second = per_second
        self.max_addresses = max_addresses
        self._tokens: dict[str, tuple[float, float]] = {}  # address -> (tokens, at)

    def take(self, address: str | None, now: float | None = None) -> float:
        """0 if a search may go now (and spends a token), else how many
        seconds until one may. An unknown address is not limited here: behind
        a proxy that strips it, that would be every visitor in one bucket."""
        if not address:
            return 0.0
        now = time.monotonic() if now is None else now
        tokens, at = self._tokens.get(address, (self.burst, now))
        tokens = min(self.burst, tokens + (now - at) * self.per_second)
        if tokens < 1.0:
            self._tokens[address] = (tokens, now)
            return (1.0 - tokens) / self.per_second
        if len(self._tokens) >= self.max_addresses and address not in self._tokens:
            # bounded: forget every address whose bucket has refilled, which
            # is exactly the state a forgotten address comes back in
            self._tokens = {
                a: (t, s)
                for a, (t, s) in self._tokens.items()
                if t + (now - s) * self.per_second < self.burst
            }
            while len(self._tokens) >= self.max_addresses:
                self._tokens.pop(next(iter(self._tokens)))  # the longest-standing
        self._tokens[address] = (tokens - 1.0, now)
        return 0.0


budget = AddressBudget()


def address_key(address: str | None) -> str | None:
    """Who a budget is kept for: an IPv4 address as is, an IPv6 one by its
    /64 -- the prefix one subscriber is handed, so rotating source addresses
    inside it no longer mints a fresh bucket per address. An IPv4-mapped
    IPv6 address is its IPv4 one; anything unparsable is kept verbatim."""
    if not address:
        return address
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


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
            transport=_transport,
        ) as client:
            resp = await client.get(
                NOMINATIM,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": str(limit),
                    # Without this Nominatim omits the address block entirely,
                    # and the country code below would silently be None
                    # everywhere. The local agent needs it: an alert published
                    # with no coordinates carries its country and nothing else.
                    "addressdetails": "1",
                },
            )
            resp.raise_for_status()
            payload = resp.json() or []
    except Exception as exc:
        # a failed search must never break the page: the local text filter
        # keeps working regardless
        # Never `str(exc)`: an HTTPStatusError quotes the full request URL,
        # and that URL holds what the person searched for.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("geocoding unavailable: %s %s", type(exc).__name__, status or "")
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
                    # Uppercase ISO2, the form every source in this product
                    # uses. Absent for the open ocean and for anything
                    # straddling a border -- and absent must stay absent
                    # rather than become a guess (lesson 15).
                    "country_code": (
                        ((row.get("address") or {}).get("country_code") or "").upper() or None
                    ),
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
