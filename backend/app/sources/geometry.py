"""Centre and extent of an area alert, antimeridian included.

An area alert -- an NWS polygon, a CAP area, an ash cloud -- was reduced to
the average of its vertices, and that single point is all the zone matching
downstream could see. A warning drawn over 800 km of coast has its centre
400 km from places it covers: matched on the centre alone, those places are
never told. `extent()` keeps the bounding box so they can be.

Both helpers take `(lat, lon)` pairs and know about the antimeridian, which
is not a corner case here: the Aleutians, Fiji, Tonga and the Kermadecs are
all on it. Measured on the ash SIGMETs of 2026-10-25, AWC split a Whakaari
plume at 180 deg into two rings and wrote one of them with **lon 180.3** --
outside the range this product's model accepts. Averaged naively, a box
spanning 179 E and 179 W puts its centre on the Greenwich meridian, and its
extent covers the whole planet.

The extent follows RFC 7946 section 5.2: `(west, south, east, north)`, and a
box that crosses the antimeridian has `west > east`.
"""

from __future__ import annotations

from collections.abc import Iterable

Point = tuple[float, float]  # (lat, lon)
BBox = tuple[float, float, float, float]  # (west, south, east, north)


def wrap_lon(lon: float) -> float:
    """Any longitude, back into [-180, 180). 180.3 -> -179.7."""
    wrapped = ((lon + 180.0) % 360.0) - 180.0
    # keep a genuine +180 rather than flipping it to -180
    return 180.0 if wrapped == -180.0 and lon > 0 else wrapped


def _clean(points: Iterable[Point]) -> list[Point]:
    cleaned: list[Point] = []
    for lat, lon in points:
        if lat is None or lon is None or not -90 <= lat <= 90:
            # lesson 15: a vertex that cannot be right is dropped, not bent
            continue
        cleaned.append((float(lat), wrap_lon(float(lon))))
    return cleaned


def _crosses(lons: list[float]) -> bool:
    """True when the points sit closer together across the antimeridian than
    across Greenwich -- i.e. the shape straddles 180 deg."""
    west, east = min(lons), max(lons)
    if east - west <= 180:
        return False
    shifted = [lon + 360 if lon < 0 else lon for lon in lons]
    return max(shifted) - min(shifted) < east - west


def extent(points: Iterable[Point]) -> BBox | None:
    pts = _clean(points)
    if not pts:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    if _crosses(lons):
        shifted = [lon + 360 if lon < 0 else lon for lon in lons]
        west, east = wrap_lon(min(shifted)), wrap_lon(max(shifted))
    else:
        west, east = min(lons), max(lons)
    return (round(west, 4), round(min(lats), 4), round(east, 4), round(max(lats), 4))


def center(points: Iterable[Point]) -> Point | None:
    pts = _clean(points)
    if not pts:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    if _crosses(lons):
        lons = [lon + 360 if lon < 0 else lon for lon in lons]
    return sum(lats) / len(lats), wrap_lon(sum(lons) / len(lons))


def union(boxes: Iterable[BBox | None]) -> BBox | None:
    """The extent covering several boxes (an alert issued for many zones)."""
    corners: list[Point] = []
    for box in boxes:
        if box is None:
            continue
        west, south, east, north = box
        corners += [(south, west), (north, east)]
    return extent(corners)
