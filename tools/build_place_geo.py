"""Builds the place report's two geographic tables from Natural Earth (public
domain), next to the country table the page already ships:

- `backend/app/data/iso3.json`: ISO 3166-1 alpha-2 -> alpha-3, read from the
  `ISO_A2_EH` / `ISO_A3_EH` columns of the same 1:50m Admin 0 file that
  `frontend/src/data/countries.json` is built from. No hand-made table: the
  INFORM index is keyed by alpha-3, every SOSForge source by alpha-2.
- `backend/app/data/coastline_50m.json`: the 1:50m OCEAN coastline, for the
  sea gate ("is this place on a coast"). Vertices in hundredths of a degree
  (about 1 km), the first pair of a line absolute and every following pair a
  delta from the previous one -- the encoding of countries.json.

A dev tool: never imported at run time.

Usage:
  python3 tools/build_place_geo.py ne_50m_admin_0_countries.geojson ne_50m_coastline.geojson
Sources: https://github.com/nvkelso/natural-earth-vector (geojson/).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "backend" / "app" / "data"


def encode(line: list[list[float]]) -> list[int]:
    out: list[int] = []
    px = py = 0
    for lon, lat in line:
        x, y = round(lon * 100), round(lat * 100)
        if out and x == px and y == py:
            continue
        out += [x - px, y - py] if out else [x, y]
        px, py = x, y
    return out


def iso3(countries: dict) -> dict[str, str]:
    table: dict[str, str] = {}
    for feature in countries["features"]:
        props = feature["properties"]
        a2, a3 = props.get("ISO_A2_EH"), props.get("ISO_A3_EH")
        if not a2 or not a3 or "-99" in (a2, a3):
            continue
        if table.get(a2, a3) != a3:
            raise SystemExit(f"{a2} maps to both {table[a2]} and {a3}")
        table[a2] = a3
    return dict(sorted(table.items()))


def coastline(coast: dict) -> list[list[int]]:
    lines: list[list[int]] = []
    for feature in coast["features"]:
        geom = feature["geometry"]
        parts = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        for part in parts:
            encoded = encode(part)
            if len(encoded) >= 4:  # at least one segment
                lines.append(encoded)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("countries")
    parser.add_argument("coastline")
    args = parser.parse_args()
    countries = json.loads(Path(args.countries).read_text(encoding="utf-8"))
    coast = json.loads(Path(args.coastline).read_text(encoding="utf-8"))

    a3 = iso3(countries)
    out = {
        "src": "Natural Earth 1:50m Admin 0 Countries (ISO_A2_EH -> ISO_A3_EH), public domain; "
        "tools/build_place_geo.py",
        "a3": a3,
    }
    path = DATA / "iso3.json"
    path.write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{path}: {len(a3)} codes")

    lines = coastline(coast)
    out = {
        "src": "Natural Earth 1:50m coastline, public domain; tools/build_place_geo.py",
        "l": lines,
    }
    path = DATA / "coastline_50m.json"
    path.write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{path}: {len(lines)} lines, {sum(len(x) for x in lines) // 2} vertices")


if __name__ == "__main__":
    main()
