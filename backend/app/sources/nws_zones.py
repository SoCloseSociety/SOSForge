"""Give NWS alerts the position they publish somewhere else.

Measured on the live feed: **183 of 205 active alerts (89%) carry no geometry
at all**. They are issued for UGC zones -- counties, marine areas, fire zones --
and the alert names those zones instead of drawing them. Every one of those
zones has a real polygon, at a stable URL, and there were 511 distinct ones in
that whole snapshot.

Without this, nine US alerts out of ten cannot be put on the map, cannot be
ranked by distance, and cannot answer the only question that matters to the
person reading: is that near me. The local agent had to fall back to comparing
COUNTRIES, which for a country the size of the United States meant a marine
warning in South Carolina waking someone in Los Angeles -- measured, 79 times
in a day.

Zone boundaries do not change. So this is a cache that fills once and is right
forever, not a lookup on the hot path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# How many unknown zones to resolve per polling cycle. NWS re-publishes its
# active alerts every cycle, so an alert that misses out this time gets its
# coordinates on the next one -- a few minutes to fill in from cold, instead of
# five hundred requests in one burst against an agency that asks for restraint.
ZONES_PER_CYCLE = 25


class ZoneResolver:
    def __init__(self, cache_path: Path | None = None):
        self._centroids: dict[str, tuple[float, float] | None] = {}
        self._cache_path = cache_path
        self._load()

    def _load(self) -> None:
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for key, value in data.items():
                self._centroids[key] = tuple(value) if value else None
            log.info("nws zones: %d positions restored from cache", len(self._centroids))
        except (OSError, ValueError) as exc:
            log.warning("nws zone cache unreadable (%s), starting empty", exc)

    def _save(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({k: list(v) if v else None for k, v in self._centroids.items()}),
                encoding="utf-8",
            )
        except OSError as exc:  # a cache is an optimisation, never a dependency
            log.warning("could not write the nws zone cache: %s", exc)

    def known(self, zone_url: str) -> tuple[float, float] | None:
        return self._centroids.get(zone_url)

    def has(self, zone_url: str) -> bool:
        return zone_url in self._centroids

    async def resolve(self, client: httpx.AsyncClient, zone_urls: list[str]) -> int:
        """Fetches up to ZONES_PER_CYCLE unknown zones. Returns how many it learned."""
        unknown = [z for z in dict.fromkeys(zone_urls) if not self.has(z)][:ZONES_PER_CYCLE]
        learned = 0
        for url in unknown:
            try:
                response = await client.get(url)
                response.raise_for_status()
                centroid = _centroid_of(response.json().get("geometry"))
            except Exception as exc:
                # A zone that will not resolve is remembered as unresolvable,
                # so we do not ask again on every single cycle forever.
                log.debug("nws zone %s did not resolve (%s)", url, exc)
                centroid = None
            self._centroids[url] = centroid
            learned += 1
        if learned:
            self._save()
        return learned


def _centroid_of(geometry: dict | None) -> tuple[float, float] | None:
    """Average of the outer ring(s). A zone is a county or a stretch of coast:
    its centre is a fair answer to "where is this", and the alternative --
    keeping no position at all -- is what we are fixing."""
    if not geometry:
        return None
    coordinates = geometry.get("coordinates") or []
    kind = geometry.get("type")
    rings: list[list] = []
    if kind == "Polygon":
        rings = coordinates[:1]
    elif kind == "MultiPolygon":
        rings = [polygon[0] for polygon in coordinates if polygon]
    else:
        return None

    points = [point for ring in rings for point in ring if len(point) >= 2]
    if not points:
        return None
    lat = sum(p[1] for p in points) / len(points)
    lon = sum(p[0] for p in points) / len(points)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon
