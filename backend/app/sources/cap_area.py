"""Give the WMO alerts the position they publish in their CAP document.

Measured on the live feed: **287 of 900 events had no position, 256 of them
from `wmo`**. The aggregated JSON (`wmo_all.json`, 2224 items on 2026-08-26)
carries no geometry at all -- not a polygon, not a coordinate, nothing. Its
only positional lead is the CAP document it links, and that link comes under
two different names: `url` for 1247 items and `capURL` for the other 977.

Fetching 771 of those documents says what is actually reachable. Of the 444
alerts ranked Severe or Extreme, **194 publish an `<area><polygon>`**
(Kazakhstan, Russia, Algeria, Saudi Arabia, Argentina, Spain, Germany, Canada,
New Zealand, part of the NWS relay), 191 publish only a `<geocode>` and 45
publish nothing. So roughly one severe alert in two can be placed exactly, and
the rest cannot be placed at all without a gazetteer we do not own -- see
`alerts_world.py` for why guessing one is refused.

A CAP document is immutable: its URL carries the minute it was issued and a
hash of its content. So this is a cache that fills once and is right forever,
exactly like `nws_zones.py`, and for the same reason -- an alert that misses
its turn this cycle is placed on the next, instead of a thousand requests
fired at an agency in one burst.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

WMO_CAP_BASE = "https://severeweather.wmo.int/v2/cap-alerts/"

# How many unknown CAP documents to fetch per polling cycle. The WMO aggregate
# republishes its whole list every cycle, so nothing is lost by waiting: from
# cold, a 2200-alert list fills in over a few cycles instead of in one burst.
CAP_DOCUMENTS_PER_CYCLE = 40

# Unlike an NWS zone, a CAP document is not a fixed set: there is a new one for
# every alert ever issued, roughly 2000 a day. So this cache is bounded and
# evicts the oldest entries, otherwise a process running for a month holds a
# dictionary of every alert the planet published. Eviction is free here: an
# alert that has left the aggregate is never asked for again.
CAP_CACHE_MAX = 20_000


def _tag(element: ET.Element) -> str:
    """Local name. Every one of the 771 documents read declares
    `urn:oasis:names:tc:emergency:cap:1.2`, but the version is part of the
    namespace, so pinning it would break the day a producer moves to 1.1."""
    return element.tag.rsplit("}", 1)[-1]


def parse_polygon(text: str | None) -> tuple[float, float] | None:
    """CAP `<polygon>`: whitespace-separated `lat,lon` pairs, **latitude
    first** -- the opposite order from GeoJSON, which is the convention used
    everywhere else in this backend.

    One unreadable or out-of-range pair rejects the whole ring rather than
    being skipped: a polygon read half-right is a point in the wrong place,
    and lesson 15 says that is worse than no point at all.
    """
    if not text:
        return None
    lats: list[float] = []
    lons: list[float] = []
    for pair in text.split():
        parts = pair.split(",")
        if len(parts) < 2:
            return None
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        lats.append(lat)
        lons.append(lon)
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def parse_cap_position(document: str | bytes) -> tuple[float, float] | None:
    """One point for one CAP alert, or nothing.

    Only `area/polygon` is read, and that restriction is not incidental: DWD
    publishes the area to SUBTRACT from the warning as a `geocode` named
    `EXCLUDE_POLYGON`, whose value is a coordinate list character for
    character identical to a polygon. Anything that scans the document for
    numbers averages that hole into the answer.

    Several areas are averaged together. A warning covering three valleys is
    one event here, and its centre is a fair answer to "where is this"; the
    alternative on offer is no answer at all.
    """
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return None

    lats: list[float] = []
    lons: list[float] = []
    for element in root.iter():
        if _tag(element) != "area":
            continue
        for child in element:
            if _tag(child) != "polygon":
                continue
            point = parse_polygon(child.text)
            if point:
                lats.append(point[0])
                lons.append(point[1])
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


class CapAreaCache:
    """CAP document path -> position, filled a few per cycle, kept on disk
    when the caller gives a path (in memory only otherwise).

    `None` is a real answer and is remembered as such: of the 443 Severe or
    Extreme alerts published today, 144 are Chinese county warnings and 34
    Indian bulletins that carry an administrative code, or nothing, and never
    a shape. Without that memory they would be re-fetched every five minutes
    forever, for an answer that cannot change.
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        base: str = WMO_CAP_BASE,
        per_cycle: int = CAP_DOCUMENTS_PER_CYCLE,
        max_entries: int = CAP_CACHE_MAX,
    ):
        self._positions: dict[str, tuple[float, float] | None] = {}
        self._cache_path = cache_path
        self.base = base
        self.per_cycle = per_cycle
        self.max_entries = max_entries
        self._load()

    def _load(self) -> None:
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for key, value in data.items():
                self._positions[key] = (float(value[0]), float(value[1])) if value else None
            log.info("wmo cap: %d documents restored from cache", len(self._positions))
        except (OSError, TypeError, ValueError) as exc:
            log.warning("wmo cap cache unreadable (%s), starting empty", exc)

    def _save(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({k: list(v) if v else None for k, v in self._positions.items()}),
                encoding="utf-8",
            )
        except OSError as exc:  # a cache is an optimisation, never a dependency
            log.warning("could not write the wmo cap cache: %s", exc)

    def known(self, path: str) -> tuple[float, float] | None:
        return self._positions.get(path)

    def has(self, path: str) -> bool:
        return path in self._positions

    async def resolve(self, client: httpx.AsyncClient, paths: list[str]) -> int:
        """Fetches up to `per_cycle` unknown documents. Returns how many it read."""
        unknown = [p for p in dict.fromkeys(paths) if not self.has(p)][: self.per_cycle]
        learned = 0
        for path in unknown:
            try:
                response = await client.get(self.base + path)
                response.raise_for_status()
                position = parse_cap_position(response.text)
            except Exception as exc:
                # A document that will not come back is remembered as
                # unplaceable, so we do not queue it again on every cycle.
                log.debug("wmo cap %s did not resolve (%s)", path, exc)
                position = None
            self._positions[path] = position
            learned += 1
        if learned:
            self._evict()
            self._save()
        return learned

    def _evict(self) -> None:
        """Oldest first. A dict preserves insertion order, and insertion order
        here is the order the alerts were met -- close enough to their age."""
        excess = len(self._positions) - self.max_entries
        for path in list(self._positions)[:excess]:
            del self._positions[path]
