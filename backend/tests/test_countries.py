"""Country resolution, on the labels actually emitted by the feeds."""

from __future__ import annotations

import pytest

from app.countries import resolve


@pytest.mark.parametrize(
    ("country", "place", "expected"),
    [
        # USGS: there is no country field, it must be pulled from the place label
        (None, "7 km WSW of Anza, CA", "US"),
        (None, "5 km NE of Orlando, Oklahoma", "US"),
        (None, "Island of Hawaii, Hawaii", "US"),
        (None, "84 km NE of Ruteng, Indonesia", "ID"),
        (None, "8 km W of Tecate, B.C., MX", "MX"),
        (None, "12 km S of Ponce, Puerto Rico", "PR"),
        # EMSC: Flynn region in capitals
        (None, "FLORES REGION, INDONESIA", "ID"),
        (None, "Fiji region", "FJ"),
        # country field provided by the source
        ("Russian Federation", None, "RU"),
        ("Türkiye", None, "TR"),
        ("New Zealand", "20 km north-west of Taihape", "NZ"),
        # GDACS lists several countries: the first is enough to set a flag
        ("Kenya, Somalia, Ethiopia", None, "KE"),
        ("The Democratic Republic of Congo", None, "CD"),
        # EMSC Flynn regions: no comma, the country is the suffix
        (None, "WESTERN TEXAS", "US"),
        (None, "OFF COAST OF NORTHERN CALIFORNIA", "US"),
        (None, "NEAR COAST OF CENTRAL PERU", "PE"),
    ],
)
def test_resolve(country, place, expected):
    assert resolve(country, place) == expected


@pytest.mark.parametrize(
    ("place", "expected"),
    [
        # all observed in real feeds, all used to produce a WRONG flag
        ("GULF OF CALIFORNIA", "MX"),  # "california" -> United States: Mexican waters
        ("21 km NNW of T'q'ibuli, Georgia", "GE"),  # Caucasus Georgia, not the US state
        ("LAC KIVU REGION, CONGO", "CD"),  # Kivu is in the DRC, not Congo-Brazzaville
        ("EQUATORIAL GUINEA REGION", "GQ"),
    ],
)
def test_ambiguous_labels_no_longer_produce_a_false_flag(place, expected):
    assert resolve(None, place) == expected


@pytest.mark.parametrize(
    "place",
    [
        "Banda Sea",
        # "guinea" alone cannot decide between Papua and Guinea
        "NEAR EAST COAST OF NEW GUINEA",
        # South Georgia is a Southern Ocean island, not a country
        "SOUTH GEORGIA RISE",
        "SOUTH GEORGIA ISLAND REGION",
        "South Sandwich Islands region",
        "Pacific-Antarctic Ridge",
        "South Atlantic Ocean",
        "west of Macquarie Island",
    ],
)
def test_the_open_sea_gets_no_flag(place):
    """An open-sea earthquake belongs to no country. On an emergency product,
    an approximate flag is false information: better to display nothing at
    all."""
    assert resolve(None, place) is None


def test_no_input_no_guess():
    assert resolve(None, None) is None
    assert resolve("", "") is None
