"""Where a point is: the page's country rule ported, the coast distance, the
sea gate, the cell keys."""

from __future__ import annotations

import json
import math

import pytest

from app.place import geo
from tests.place_helpers import FIXTURES


def _points() -> list[dict]:
    return json.loads((FIXTURES / "country_points.json").read_text())["points"]


@pytest.mark.parametrize("point", _points(), ids=lambda p: p["name"])
def test_country_rule_on_the_shared_points(point: dict) -> None:
    codes, code, _ = geo.countries(point["lat"], point["lon"])
    assert codes == point["codes"]
    assert code == point["country_code"]
    assert geo.place_of(point["lat"], point["lon"]).at_sea is point["at_sea"]


def test_reykjavik_is_iceland_although_no_polygon_contains_it() -> None:
    codes, code, basis = geo.countries(64.15, -21.94)
    assert (codes, code, basis) == (["IS"], "IS", "nearest_within_25km")


# measured with the full Natural Earth 1:50m coastline (sea_scripts/coast_dist.py)
COAST = {
    "samui": (9.512, 100.013, 5.9),
    "tokyo": (35.676, 139.650, 14.2),
    "paris": (48.857, 2.352, 150.5),
    "lima": (-12.046, -77.043, 7.0),
    "reykjavik": (64.147, -21.942, 0.1),
    "anchorage": (61.218, -149.900, 0.8),
    "kathmandu": (27.717, 85.324, 647.5),
    "midpacific": (0.0, -160.0, 342.4),
}


@pytest.mark.parametrize("name", list(COAST))
def test_coast_distance_at_the_measured_places(name: str) -> None:
    lat, lon, measured = COAST[name]
    lat, lon = geo.round_point(lat, lon)
    km = geo.coast_km(lat, lon)
    assert km is not None
    # the shipped table is in hundredths of a degree and the point is rounded
    assert abs(km - measured) <= 1.0, km


def test_the_antipode_does_not_bring_texas_next_to_kathmandu() -> None:
    km = geo.coast_km(27.72, 85.32)
    assert km is not None and km > 600


def test_coast_distance_is_null_beyond_1000_km() -> None:
    assert geo.coast_km(45.0, 87.0) is None


@pytest.mark.parametrize(
    ("name", "gate"),
    [
        ("samui", True),
        ("tokyo", True),
        ("lima", True),
        ("reykjavik", True),
        ("anchorage", True),
        ("midpacific", True),
        ("paris", False),
        ("kathmandu", False),
    ],
)
def test_sea_gate(name: str, gate: bool) -> None:
    lat, lon, _ = COAST[name]
    lat, lon = geo.round_point(lat, lon)
    assert geo.place_of(lat, lon).sea_gate is gate


def test_rounding_is_two_decimals_and_never_negative_zero() -> None:
    lat, lon = geo.round_point(-0.001, -0.004)
    assert (lat, lon) == (0.0, 0.0)
    assert math.copysign(1.0, lat) == 1.0 and math.copysign(1.0, lon) == 1.0
    assert geo.round_point(9.512, 100.013) == (9.51, 100.01)


def test_cell_centres() -> None:
    assert geo.cell_key(9.51, 100.01, 0.1) == (9.55, 100.05)
    assert geo.cell_key(-12.05, -77.04, 0.25) == (-12.125, -77.125)
    assert geo.cell_centre(0.0, 0.05) == 0.025


def test_boxes_clamp_at_the_poles_and_split_at_the_antimeridian() -> None:
    south, north, west, east = geo.box(89.9, 0.0, 300)
    assert north == 90.0 and (west, east) == (-180.0, 180.0)
    _, _, west, east = geo.box(0.0, 179.0, 300)
    spans = geo.split_lon(west, east)
    assert len(spans) == 2 and spans[0][1] == 180.0 and spans[1][0] == -180.0


def test_iso3_comes_from_the_natural_earth_table() -> None:
    assert geo.iso3("TH") == "THA"
    assert geo.iso3("US") == "USA"
    assert geo.iso3(None) is None
