"""Where a point is, computed locally: no reverse geocoding call.

- The country rule is the page's own (`frontend/src/countries.ts`), ported:
  a country counts when its polygon contains the point OR lies within
  NEAR_BORDER_KM. The table is Natural Earth 1:50m simplified at 0.02 deg,
  so many coastal cities fall inside NO polygon -- measured: Reykjavik
  0.7 km outside Iceland, Stockholm 0.66 km, New York 3.4 km, Lisbon 2.2 km,
  Venice 3.5 km. Without the margin Reykjavik's land blocks would all read
  "at sea". `app/data/countries.json` is a byte copy of the frontend table.
- `at_sea` is "no country within 25 km". Known limit, measured: an island
  1:50m omits reads as at sea (Ko Tao 36.5 km from mapped land).
- `coast_km` is the distance to the nearest 1:50m OCEAN coastline segment,
  searched only among segments in a box around the point (a segment
  straddling the ANTIPODE projects through the point on a local plane:
  Kathmandu once came out 191 km from Texas). Null beyond about 1000 km.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import numpy as np

NEAR_BORDER_KM = 25.0
SEA_GATE_KM = 50.0
COAST_SEARCH_KM = 1000.0
EARTH_KM = 6371.0088


def round_point(lat: float, lon: float) -> tuple[float, float]:
    """2 decimals (about 1.1 km) before any use, and never `-0.0`."""
    return round(lat, 2) + 0.0, round(lon, 2) + 0.0


def wrap_lon(lon: float) -> float:
    return (lon + 180.0) % 360.0 - 180.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, 0 = north, clockwise."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def box(lat: float, lon: float, km: float) -> tuple[float, float, float, float]:
    """(south, north, west, east) covering a circle of `km`, latitudes
    clamped at the poles, longitudes NOT wrapped (see `split_lon`)."""
    dlat = km / 111.32
    dlon = km / (111.32 * max(0.05, math.cos(math.radians(lat))))
    south, north = max(-90.0, lat - dlat), min(90.0, lat + dlat)
    if north >= 89.99 or south <= -89.99:
        # a box touching a pole covers every longitude
        return south, north, -180.0, 180.0
    return south, north, lon - dlon, lon + dlon


def split_lon(west: float, east: float) -> list[tuple[float, float]]:
    """A longitude span as one or two spans inside -180..180 (split at the
    antimeridian)."""
    if east - west >= 360.0:
        return [(-180.0, 180.0)]
    if west < -180.0:
        return [(west + 360.0, 180.0), (-180.0, east)]
    if east > 180.0:
        return [(west, 180.0), (-180.0, east - 360.0)]
    return [(west, east)]


def cell_centre(value: float, size: float) -> float:
    """The centre of the `size`-degree cell holding `value`."""
    return round((math.floor(value / size) + 0.5) * size, 6)


def cell_key(lat: float, lon: float, size: float) -> tuple[float, float]:
    return cell_centre(lat, size), cell_centre(lon, size)


# ----------------------------------------------------------------- countries


@dataclass(frozen=True)
class Shape:
    code: str
    ring: np.ndarray  # (n, 2): lon, lat
    west: float
    south: float
    east: float
    north: float
    area: float


def _data(name: str) -> dict:
    return json.loads((resources.files("app") / "data" / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def shapes() -> tuple[Shape, ...]:
    table = _data("countries.json")
    out: list[Shape] = []
    for code, rings in table["c"].items():
        for deltas in rings:
            pts = np.cumsum(np.asarray(deltas, dtype=np.float64).reshape(-1, 2), axis=0) / 100.0
            x, y = pts[:, 0], pts[:, 1]
            area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
            west, east = float(np.min(x)), float(np.max(x))
            south, north = float(np.min(y)), float(np.max(y))
            out.append(Shape(code, pts, west, south, east, north, area))
    return tuple(out)


@lru_cache(maxsize=1)
def iso3_table() -> dict[str, str]:
    return dict(_data("iso3.json")["a3"])


def iso3(code: str | None) -> str | None:
    return iso3_table().get(code) if code else None


def _inside(ring: np.ndarray, lon: float, lat: float) -> bool:
    x, y = ring[:, 0], ring[:, 1]
    xj, yj = np.roll(x, 1), np.roll(y, 1)
    crosses = (y > lat) != (yj > lat)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = (xj - x) * (lat - y) / (yj - y) + x
    return bool(np.count_nonzero(crosses & (lon < at)) % 2)


def _edge_km(ring: np.ndarray, lon: float, lat: float) -> float:
    """Shortest distance to the ring on a local flat projection: exact
    enough within the 25 km it is asked about (the frontend's `edgeKm`)."""
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    a = ring
    b = np.roll(ring, -1, axis=0)
    ax, ay = (a[:, 0] - lon) * kx, (a[:, 1] - lat) * ky
    bx, by = (b[:, 0] - lon) * kx, (b[:, 1] - lat) * ky
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length == 0, 0.0, np.clip(-(ax * dx + ay * dy) / length, 0.0, 1.0))
    return float(np.min(np.hypot(ax + t * dx, ay + t * dy)))


@dataclass(frozen=True)
class Place:
    country_code: str | None
    country_codes: tuple[str, ...]
    at_sea: bool
    coast_km: float | None
    # how `country_code` was found: "containing" or "nearest_within_25km"
    country_basis: str | None

    @property
    def sea_gate(self) -> bool:
        return self.at_sea or (self.coast_km is not None and self.coast_km <= SEA_GATE_KM)

    def public(self) -> dict:
        return {
            "country_code": self.country_code,
            "country_codes": list(self.country_codes),
            "at_sea": self.at_sea,
            "coast_km": self.coast_km,
        }


def countries(lat: float, lon: float) -> tuple[list[str], str | None, str | None]:
    """(every country at or within 25 km, sorted; the country of the point;
    how it was found)."""
    margin_lat = NEAR_BORDER_KM / 110.57
    margin_lon = NEAR_BORDER_KM / (111.32 * max(0.05, math.cos(math.radians(lat))))
    found: set[str] = set()
    containing: list[Shape] = []
    nearest: tuple[float, str] | None = None
    for shape in shapes():
        if (
            lat < shape.south - margin_lat
            or lat > shape.north + margin_lat
            or lon < shape.west - margin_lon
            or lon > shape.east + margin_lon
        ):
            continue
        if _inside(shape.ring, lon, lat):
            containing.append(shape)
            found.add(shape.code)
            continue
        km = _edge_km(shape.ring, lon, lat)
        if km <= NEAR_BORDER_KM:
            found.add(shape.code)
            if nearest is None or km < nearest[0]:
                nearest = (km, shape.code)
    if containing:
        # holes are not in the table: an enclave is its own country and
        # the smallest containing polygon is the right one (countries.ts)
        return sorted(found), min(containing, key=lambda s: s.area).code, "containing"
    if nearest is not None:
        return sorted(found), nearest[1], "nearest_within_25km"
    return sorted(found), None, None


# ----------------------------------------------------------------- coastline


@lru_cache(maxsize=1)
def _segments() -> np.ndarray:
    """(n, 4): lon1, lat1, lon2, lat2 of every coastline segment."""
    rows = []
    for deltas in _data("coastline_50m.json")["l"]:
        pts = np.cumsum(np.asarray(deltas, dtype=np.float64).reshape(-1, 2), axis=0) / 100.0
        rows.append(np.hstack([pts[:-1], pts[1:]]))
    return np.vstack(rows)


def coast_km(lat: float, lon: float) -> float | None:
    seg = _segments()
    window_lat = COAST_SEARCH_KM / 111.32
    window_lon = window_lat / max(0.05, math.cos(math.radians(lat)))

    def dlon(values: np.ndarray) -> np.ndarray:
        return (values - lon + 540.0) % 360.0 - 180.0

    d1, d2 = dlon(seg[:, 0]), dlon(seg[:, 2])
    near = (
        (np.abs(seg[:, 1] - lat) <= window_lat)
        & (np.abs(seg[:, 3] - lat) <= window_lat)
        & (np.abs(d1) <= window_lon)
        & (np.abs(d2) <= window_lon)
    )
    if not near.any():
        return None
    k = math.cos(math.radians(lat))
    rad = math.pi / 180.0 * EARTH_KM
    x1, y1 = d1[near] * rad * k, (seg[near, 1] - lat) * rad
    x2, y2 = d2[near] * rad * k, (seg[near, 3] - lat) * rad
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length == 0, 0.0, np.clip(-(x1 * dx + y1 * dy) / length, 0.0, 1.0))
    best = float(np.min(np.hypot(x1 + t * dx, y1 + t * dy)))
    return round(best, 1) if best <= COAST_SEARCH_KM else None


def place_of(lat: float, lon: float) -> Place:
    codes, code, basis = countries(lat, lon)
    return Place(
        country_code=code,
        country_codes=tuple(codes),
        at_sea=not codes,
        coast_km=coast_km(lat, lon),
        country_basis=basis,
    )


def in_box(lat: float, lon: float, south: float, north: float, west: float, east: float) -> bool:
    return south <= lat <= north and west <= lon <= east
