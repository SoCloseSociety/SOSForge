"""Where a point is: the page's country rule ported, the coast distance, the
sea gate, the cell keys."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from app.place import geo
from tests.place_helpers import FIXTURES, at, ctx_for


def _points() -> list[dict]:
    return json.loads((FIXTURES / "country_points.json").read_text())["points"]


@pytest.mark.parametrize("point", _points(), ids=lambda p: p["name"])
def test_country_rule_on_the_shared_points(point: dict) -> None:
    codes, code, _ = geo.countries(point["lat"], point["lon"])
    assert codes == point["codes"]
    assert code == point["country_code"]
    assert geo.place_of(point["lat"], point["lon"]).at_sea is point["at_sea"]


def test_reykjavik_is_inside_iceland_at_1_10m() -> None:
    # 0.7 km outside every polygon of the old 1:50m table
    assert geo.countries(64.15, -21.94) == (["IS"], "IS", "containing")


def test_a_point_in_a_hole_is_not_in_the_polygon() -> None:
    # South Africa's exterior ring covers Lesotho; its hole takes it back
    codes, code, basis = geo.countries(-29.31, 27.48)
    assert (code, basis) == ("LS", "containing")
    assert "ZA" in codes  # the border is within 25 km


def test_an_island_the_table_omits_gets_the_nearest_country_and_stays_at_sea() -> None:
    codes, code, basis = geo.countries(10.09, 99.84)  # Ko Tao, 37 km from mapped land
    assert (codes, code, basis) == (["TH"], "TH", "nearest_within_100km")
    place = geo.place_of(10.09, 99.84)
    assert place.at_sea and place.sea_gate


def test_open_sea_has_no_country() -> None:
    assert geo.countries(0.0, -140.0) == ([], None, None)


def test_mid_pacific_is_41_km_from_jarvis_island() -> None:
    # 1:50m did not draw Jarvis (US Minor Outlying Islands); 1:10m does
    assert geo.countries(0.0, -160.0) == (["UM"], "UM", "nearest_within_100km")
    assert geo.place_of(0.0, -160.0).at_sea


def test_overseas_departments_have_their_own_alpha_3() -> None:
    # Natural Earth files them under FRA: INFORM would score Reunion as France
    assert geo.countries(-20.88, 55.45)[1] == "RE"
    assert [geo.iso3(c) for c in ("RE", "GP", "MQ", "GF", "YT", "BQ")] == [
        "REU",
        "GLP",
        "MTQ",
        "GUF",
        "MYT",
        "BES",
    ]


# measured at the ROUNDED point on the full-precision Natural Earth 1:10m
# coastline (ne_10m_coastline.geojson at natural-earth-vector ca96624, the
# local-plane segment distance of sea_scripts/coast_dist.py; the spherical
# cross-track distance agrees to 0.01 km under 50 km)
COAST = {
    "samui": (9.512, 100.013, 6.62),
    "tokyo": (35.676, 139.650, 10.82),
    "paris": (48.857, 2.352, 149.33),
    "lima": (-12.046, -77.043, 6.65),
    "reykjavik": (64.147, -21.942, 1.30),
    "anchorage": (61.218, -149.900, 0.68),
    "kathmandu": (27.717, 85.324, 641.50),
    "midpacific": (0.0, -160.0, 41.43),  # Jarvis: 342.4 on the 1:50m coastline
    "lampedusa": (35.50, 12.60, 0.65),  # 137.1 on 1:50m, which does not draw it
    "rodrigues": (-19.70, 63.42, 2.65),  # 592.1 on 1:50m
    "malta": (35.90, 14.51, 0.37),
    "male": (4.18, 73.51, 0.71),
}


@pytest.mark.parametrize("name", list(COAST))
def test_coast_distance_at_the_measured_places(name: str) -> None:
    lat, lon, measured = COAST[name]
    lat, lon = geo.round_point(lat, lon)
    km = geo.coast_km(lat, lon)
    assert km is not None
    # the shipped table is in hundredths of a degree (at most ~0.75 km off)
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
        ("malta", True),
        ("male", True),
        ("lampedusa", True),
        ("rodrigues", True),
        ("paris", False),
        ("kathmandu", False),
    ],
)
def test_sea_gate(name: str, gate: bool) -> None:
    lat, lon, _ = COAST[name]
    lat, lon = geo.round_point(lat, lon)
    assert geo.place_of(lat, lon).sea_gate is gate


# Islands the 1:10m country table draws and the 1:50m coastline did not
# (adversarial review of the place report): land, so `at_sea` is false, with
# the coast 71 to 957 km away -- the sea blocks said "inland" on every one.
# Tristan da Cunha had no coast within 1000 km at all.
ISLANDS = {
    "Lampedusa": (35.50, 12.60, "IT"),
    "San Andres": (12.58, -81.70, "CO"),
    "Rodrigues": (-19.70, 63.42, "MU"),
    "Kavaratti, Lakshadweep": (10.57, 72.64, "IN"),
    "Addu, Maldives": (-0.63, 73.16, "MV"),
    "Fernando de Noronha": (-3.85, -32.42, "BR"),
    "Aitutaki": (-18.86, -159.79, "CK"),
    "Lord Howe": (-31.55, 159.08, "AU"),
    "Rangiroa": (-14.95, -147.70, "PF"),
    "Pitcairn": (-25.07, -130.10, "PN"),
    "Wake": (19.29, 166.62, "UM"),
    "Aldabra": (-9.42, 46.35, "SC"),
    "Clipperton": (10.29, -109.22, "FR"),
    "Tristan da Cunha": (-37.07, -12.31, "SH"),
    "20 km east of Jarvis": (-0.37, -159.84, "UM"),
}


@pytest.mark.parametrize("name", list(ISLANDS))
def test_an_island_the_country_table_draws_is_on_the_coast(name: str) -> None:
    lat, lon, code = ISLANDS[name]
    place = geo.place_of(lat, lon)
    assert place.country_code == code and not place.at_sea
    assert place.coast_km is not None and place.coast_km <= geo.SEA_GATE_KM, place.coast_km
    assert place.sea_gate


def test_the_coastline_is_at_the_scale_of_the_country_table() -> None:
    """Every polygon of the country table under 1 deg2 has a coastline vertex
    within its box plus 0.05 deg, except the inland ones. On the 1:50m
    coastline 1515 failed; a finer country table needs a finer coastline."""
    segments = geo._segments()
    segments = segments[np.argsort(segments[:, 1], kind="stable")]  # whatever its order
    far: list[str] = []
    for shape in geo.shapes():
        if shape.area >= 1.0:
            continue
        lo, hi = np.searchsorted(segments[:, 1], [shape.south - 0.05, shape.north + 0.05])
        lon = segments[lo:hi, 0]
        if not ((lon >= shape.west - 0.05) & (lon <= shape.east + 0.05)).any():
            far.append(shape.code)
    assert sorted(far) == sorted(
        [
            # enclaves and exclaves (Andorra, Liechtenstein, San Marino, Llivia,
            # Nakhchivan, the Fergana valley, Baikonur, a UAE enclave in Oman, a
            # river island), Luxembourg, and Lake Malawi's Likoma and Chizumulu
            *["AD", "AE", "AM", "AZ", "AZ", "AZ", "BR", "ES", "KZ", "LI", "LU", "MW", "MW", "SM"],
            *["TJ", "TJ", "UZ", "UZ"],
            # Hans Island's Danish half: a 1:10m MINOR island, not in the 1:10m
            # coastline -- 16.8 km from the drawn coast, so its gate still holds
            "GL",
        ]
    ), sorted(far)


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


def test_cells_at_the_antimeridian_and_the_pole_stay_valid_coordinates() -> None:
    # Taveuni, Fiji, on the antimeridian: the click rounds to 180.0
    assert geo.round_point(-16.85, 179.996) == (-16.85, 180.0)  # the echo the client matches
    assert geo.cell_key(-16.85, 180.0, 0.1) == (-16.85, -179.95)
    assert geo.cell_key(89.995, 0.0, 0.1)[0] == 89.95
    assert geo.cell_key(90.0, 180.0, 0.1) == (89.95, -179.95)
    assert geo.cell_key(-90.0, -180.0, 0.1) == (-89.95, -179.95)
    for lat, lon in ((90.0, 180.0), (-90.0, -180.0), (0.0, 179.999)):
        clat, clon = geo.cell_key(lat, lon, 0.1)
        assert -90.0 < clat < 90.0 and -180.0 <= clon < 180.0


def test_the_usgs_query_at_the_antimeridian_is_inside_fdsn_ranges() -> None:
    from app.place import history

    ctx = ctx_for(-16.85, 179.996, at("2026-10-27T11:00:00Z"))
    clat, clon = history.cell_of(ctx)
    url = history.USGS_QUERY.format(lat=clat, lon=clon, km=history.USGS_RADIUS_KM)
    assert "longitude=-179.95&" in url
