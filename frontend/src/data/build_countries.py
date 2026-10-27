"""Builds countries.json (next to this file): which country a point is in,
answered in the page, offline.

Why: Near me must show the warnings that carry no position but name the
reader's country (most Meteoalarm warnings, part of the WMO aggregate). That
needs the reader's country, and the page promises "your location never
leaves your device" -- so no reverse geocoding call. This table answers it
locally.

Source: Natural Earth 1:10m Admin 0 Countries (public domain),
https://github.com/nvkelso/natural-earth-vector, `ISO_A2_EH` codes. 1:10m,
not 1:50m: the 1:50m table had no polygon for Patmos, Lipari, Lampedusa,
Helgoland, Scilly or Porto Santo, and put Borkum in the Netherlands,
Kastellorizo in Turkey and Melilla in Morocco (final review of PR #5).

Units without a code there: Somaliland is filed under SO, Northern Cyprus,
the UN buffer zone and the two UK bases under CY, Guantanamo under CU (the
codes the alert feeds use for those places). Claimed ground (Siachen, Bir
Tawil, the Patagonian ice field, the Spratly reefs) is left out: no country
is better than a wrong one.

Natural Earth files the French overseas departments under FR and the
Caribbean Netherlands under NL. Meteoalarm's "France" is the metropolitan
departments only, so an orange storm in Brittany read as "France" for a
reader in Reunion, 9,000 km away. Those polygons get their own ISO codes
(GF, GP, MQ, RE, YT, BQ).

Holes are KEPT: without them South Africa's ring covered all of Lesotho.

Output: {"src": ..., "c": {"FR": [polygon, ...]}}, a polygon a list of
rings (the exterior first, then its holes), a ring a flat list of integers:
lon and lat in hundredths of a degree (about 1 km), the first pair absolute
and every following pair a delta from the previous one. Simplified at 0.01
degrees.

Usage: python3 frontend/src/data/build_countries.py ne_10m_admin_0_countries.geojson
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape

OUT = Path(__file__).resolve().parent / "countries.json"
TOLERANCE = 0.02
# ADM0_A3 -> the code the feeds use, for units Natural Earth leaves at -99
NO_CODE = {"SOL": "SO", "CYN": "CY", "CNM": "CY", "ESB": "CY", "WSB": "CY", "USG": "CU"}
# Overseas parts filed under their state: (state, own code, west, south, east, north)
OVERSEAS = [
    ("FR", "GF", -54.7, 2.0, -51.5, 6.0),
    ("FR", "GP", -61.9, 15.8, -60.9, 16.6),
    ("FR", "MQ", -61.3, 14.3, -60.7, 15.0),
    ("FR", "RE", 55.1, -21.5, 55.9, -20.8),
    ("FR", "YT", 44.9, -13.1, 45.4, -12.5),
    ("NL", "BQ", -68.5, 11.9, -62.9, 17.7),
]


def encode(ring: list[tuple[float, float]]) -> list[int]:
    out: list[int] = []
    px = py = 0
    for lon, lat in ring:
        x, y = round(lon * 100), round(lat * 100)
        if out and x == px and y == py:
            continue
        out += [x - px, y - py] if out else [x, y]
        px, py = x, y
    return out


def overseas(code: str, poly: Polygon) -> str:
    point = poly.representative_point()
    for state, own, west, south, east, north in OVERSEAS:
        if code == state and west <= point.x <= east and south <= point.y <= north:
            return own
    return code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("geojson")
    args = parser.parse_args()
    data = json.loads(Path(args.geojson).read_text(encoding="utf-8"))
    table: dict[str, list[list[list[int]]]] = {}
    for feature in data["features"]:
        props = feature["properties"]
        code = props.get("ISO_A2_EH")
        if not code or code == "-99":
            code = NO_CODE.get(props.get("ADM0_A3", ""))
        if not code:
            continue
        geom = shape(feature["geometry"]).simplify(TOLERANCE, preserve_topology=True)
        polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        for poly in polys:
            if not isinstance(poly, Polygon) or poly.is_empty:
                continue
            exterior = encode(list(poly.exterior.coords))
            if len(exterior) < 6:
                continue
            holes = [encode(list(hole.coords)) for hole in poly.interiors]
            rings = [exterior] + [hole for hole in holes if len(hole) >= 6]
            table.setdefault(overseas(code, poly), []).append(rings)
    out = {
        "src": "Natural Earth 1:10m Admin 0 Countries, public domain; frontend/src/data/build_countries.py",
        "c": dict(sorted(table.items())),
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(table)} countries, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
