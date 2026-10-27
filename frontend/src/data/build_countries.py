"""Builds countries.json (next to this file): which country a point is in,
answered in the page, offline.

Why: Near me must show the warnings that carry no position but name the
reader's country (most Meteoalarm warnings, part of the WMO aggregate). That
needs the reader's country, and the page promises "your location never
leaves your device" -- so no reverse geocoding call. This table answers it
locally.

Source: Natural Earth 1:50m Admin 0 Countries (public domain),
https://github.com/nvkelso/natural-earth-vector, `ISO_A2_EH` codes. Three
units carry no code there: Somaliland is filed under SO and Northern Cyprus
under CY (the codes the alert feeds use for them); the Siachen glacier, claimed
by two countries, is left out -- no country is better than a wrong one.

Output: {"src": ..., "c": {"FR": [ring, ...]}}. A ring is a flat list of
integers, lon and lat in hundredths of a degree (about 1 km), the first pair
absolute and every following pair a delta from the previous one. Simplified
at 0.02 degrees, which keeps every border within a couple of kilometres.

Usage: python3 frontend/src/data/build_countries.py ne_50m_admin_0_countries.geojson
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape

OUT = Path(__file__).resolve().parent / "countries.json"
TOLERANCE = 0.02
NO_CODE = {"SOL": "SO", "CYN": "CY"}  # ADM0_A3 -> the code the feeds use


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("geojson")
    args = parser.parse_args()
    data = json.loads(Path(args.geojson).read_text(encoding="utf-8"))
    rings: dict[str, list[list[int]]] = {}
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
            # holes are ignored: an enclave (Lesotho, San Marino) is its own
            # country in the table, and the lookup takes the SMALLEST match
            ring = encode(list(poly.exterior.coords))
            if len(ring) >= 6:
                rings.setdefault(code, []).append(ring)
    out = {
        "src": "Natural Earth 1:50m Admin 0 Countries, public domain; frontend/src/data/build_countries.py",
        "c": dict(sorted(rings.items())),
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(rings)} countries, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
