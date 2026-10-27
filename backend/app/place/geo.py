"""Where a point is, computed locally: no reverse geocoding call.

- The country rule is the page's own (`frontend/src/countries.ts`), ported:
  a country counts when its polygon contains the point (and none of its
  holes: Maseru is LS, not ZA) OR lies within NEAR_BORDER_KM; with none,
  the one nearest country within OFFSHORE_KM (basis
  `nearest_within_100km`). The table is Natural Earth 1:10m simplified at
  0.02 deg (stored in hundredths), with the overseas departments under their
  own codes (Reunion is RE). `app/data/countries.json` is a byte copy of the
  frontend table.
- `at_sea` is "no country within 25 km", whatever OFFSHORE_KM found. Known
  limit, measured: an island the table omits reads as at sea (Ko Tao, 37 km
  from mapped land) -- with TH as its country, as the feed's Near me says.
- `coast_km` is the distance to the nearest 1:10m OCEAN coastline segment
  (`app/data/coastline_10m.json`), searched only among segments in a box
  around the point (a segment straddling the ANTIPODE projects through the
  point on a local plane: Kathmandu once came out 191 km from Texas). Null
  beyond about 1000 km. The coastline is at the SAME scale as the country
  table: with 1:50m beside the 1:10m countries, every island 1:50m does not
  draw was land (`at_sea` false) with its coast 71 to 957 km away, so the
  sea blocks told Lampedusa, Rodrigues and Wake they were inland. Known
  limit, measured: Hans Island's Danish half is a 1:10m MINOR island, in
  the country table but not in this coastline, so it reads 16.8 km from the
  coast (its gate still holds).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import numpy as np

NEAR_BORDER_KM = 25.0
OFFSHORE_KM = 100.0
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
    """The centre of the cell holding the point, always a valid coordinate:
    lat 90.0 is in the last cell below the pole, lon 180.0 is lon -180.0.
    The rounded point keeps its own 90.0 / 180.0 (the client matches its
    answer on it); only what goes into an upstream query is folded here --
    a click at 179.996 sent `longitude=180.05` to USGS (outside FDSN's
    -180..180, so `upstream_http`)."""
    return cell_centre(min(lat, 90.0 - 1e-9), size), cell_centre(wrap_lon(lon), size)


# ----------------------------------------------------------------- countries


@dataclass(frozen=True)
class Shape:
    code: str
    # (n, 2) arrays of lon, lat: the exterior ring, then its holes
    rings: tuple[np.ndarray, ...]
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
    for code, polygons in table["c"].items():
        for polygon in polygons:
            rings = tuple(
                np.cumsum(np.asarray(deltas, dtype=np.float64).reshape(-1, 2), axis=0) / 100.0
                for deltas in polygon
            )
            x, y = rings[0][:, 0], rings[0][:, 1]
            area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
            west, east = float(np.min(x)), float(np.max(x))
            south, north = float(np.min(y)), float(np.max(y))
            out.append(Shape(code, rings, west, south, east, north, area))
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


def _inside_shape(shape: Shape, lon: float, lat: float) -> bool:
    """Inside the exterior and in none of the holes (countries.ts): South
    Africa's ring no longer covers Lesotho."""
    if not _inside(shape.rings[0], lon, lat):
        return False
    return not any(_inside(hole, lon, lat) for hole in shape.rings[1:])


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
    # how `country_code` was found: "containing", "nearest_within_25km" or
    # "nearest_within_100km" (then at_sea is true too)
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
    """(every country at or within 25 km, sorted, else the nearest one within
    100 km; the country of the point; how it was found)."""
    margin_lat = OFFSHORE_KM / 110.57
    margin_lon = OFFSHORE_KM / (111.32 * max(0.05, math.cos(math.radians(lat))))
    found: set[str] = set()
    containing: list[Shape] = []
    nearest: tuple[float, str] | None = None
    offshore: tuple[float, str] | None = None
    for shape in shapes():
        if (
            lat < shape.south - margin_lat
            or lat > shape.north + margin_lat
            or lon < shape.west - margin_lon
            or lon > shape.east + margin_lon
        ):
            continue
        if _inside_shape(shape, lon, lat):
            containing.append(shape)
            found.add(shape.code)
            continue
        km = min(_edge_km(ring, lon, lat) for ring in shape.rings)
        if km <= NEAR_BORDER_KM:
            found.add(shape.code)
            if nearest is None or km < nearest[0]:
                nearest = (km, shape.code)
        elif km <= OFFSHORE_KM and (offshore is None or km < offshore[0]):
            offshore = (km, shape.code)
    if containing:
        # a point in a hole is outside its polygon; the smallest containing
        # polygon still settles an overlap the simplification leaves
        return sorted(found), min(containing, key=lambda s: s.area).code, "containing"
    if nearest is not None:
        return sorted(found), nearest[1], "nearest_within_25km"
    if offshore is not None:
        return [offshore[1]], offshore[1], "nearest_within_100km"
    return [], None, None


# ----------------------------------------------------------------- coastline


@lru_cache(maxsize=1)
def _segments() -> np.ndarray:
    """(n, 4): lon1, lat1, lon2, lat2 of every 1:10m coastline segment
    (383 163, 12.3 MB), sorted by lat1 so a query reads only its latitude
    band. Filled in place and sorted one column at a time: stacking per-line
    arrays and then sorting a copy held three of them at once."""
    lines = _data("coastline_10m.json")["l"]
    seg = np.empty((sum(len(d) // 2 - 1 for d in lines), 4), dtype=np.float64)
    at = 0
    for deltas in lines:
        pts = np.cumsum(np.asarray(deltas, dtype=np.float64).reshape(-1, 2), axis=0) / 100.0
        n = len(pts) - 1
        seg[at : at + n, :2] = pts[:-1]
        seg[at : at + n, 2:] = pts[1:]
        at += n
    del lines
    order = np.argsort(seg[:, 1], kind="stable")
    for column in range(4):
        seg[:, column] = seg[order, column]
    return seg


def coast_km(lat: float, lon: float) -> float | None:
    window_lat = COAST_SEARCH_KM / 111.32
    window_lon = window_lat / max(0.05, math.cos(math.radians(lat)))
    every = _segments()
    # the band holding every lat1 the box test below can accept (a hair
    # wider, so float rounding at its edges never drops one): the same answer
    # as a scan of the whole table, reading a median 11 % of it (28 % at most)
    lo, hi = np.searchsorted(every[:, 1], [lat - window_lat - 1e-6, lat + window_lat + 1e-6])
    seg = every[lo:hi]

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


def preload() -> None:
    """Read the country, ISO and coastline tables now (about 150 ms, 33 MB
    of RSS, 20 of them the 1:10m coastline)."""
    shapes()
    iso3_table()
    _segments()


def place_of(lat: float, lon: float) -> Place:
    codes, code, basis = countries(lat, lon)
    return Place(
        country_code=code,
        country_codes=tuple(codes),
        at_sea=basis not in ("containing", "nearest_within_25km"),
        coast_km=coast_km(lat, lon),
        country_basis=basis,
    )


def in_box(lat: float, lon: float, south: float, north: float, west: float, east: float) -> bool:
    return south <= lat <= north and west <= lon <= east
