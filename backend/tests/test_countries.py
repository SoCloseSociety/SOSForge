"""Resolution du pays, sur les libelles reellement emis par les flux."""

from __future__ import annotations

import pytest

from app.countries import flag_emoji, resolve


@pytest.mark.parametrize(
    ("country", "place", "expected"),
    [
        # USGS: le pays n'existe pas, il faut le tirer du libelle de lieu
        (None, "7 km WSW of Anza, CA", "US"),
        (None, "5 km NE of Orlando, Oklahoma", "US"),
        (None, "Island of Hawaii, Hawaii", "US"),
        (None, "84 km NE of Ruteng, Indonesia", "ID"),
        (None, "8 km W of Tecate, B.C., MX", "MX"),
        (None, "12 km S of Ponce, Puerto Rico", "PR"),
        # EMSC: region Flynn en capitales
        (None, "FLORES REGION, INDONESIA", "ID"),
        (None, "Fiji region", "FJ"),
        # champ country fourni par la source
        ("Russian Federation", None, "RU"),
        ("Türkiye", None, "TR"),
        ("New Zealand", "20 km north-west of Taihape", "NZ"),
        # GDACS liste plusieurs pays: le premier suffit a poser un drapeau
        ("Kenya, Somalia, Ethiopia", None, "KE"),
        ("The Democratic Republic of Congo", None, "CD"),
        # regions Flynn de l'EMSC: pas de virgule, le pays est en suffixe
        (None, "WESTERN TEXAS", "US"),
        (None, "OFF COAST OF NORTHERN CALIFORNIA", "US"),
        (None, "NEAR COAST OF CENTRAL PERU", "PE"),
    ],
)
def test_resolve(country, place, expected):
    assert resolve(country, place) == expected


@pytest.mark.parametrize(
    "place",
    [
        "Banda Sea",
        "South Sandwich Islands region",
        "Pacific-Antarctic Ridge",
        "South Atlantic Ocean",
        "west of Macquarie Island",
    ],
)
def test_the_open_sea_gets_no_flag(place):
    """Un seisme en pleine mer n'appartient a aucun pays. Sur un produit
    d'urgence, un drapeau approximatif est une information fausse: on prefere
    ne rien afficher."""
    assert resolve(None, place) is None


def test_no_input_no_guess():
    assert resolve(None, None) is None
    assert resolve("", "") is None


def test_flag_emoji():
    assert flag_emoji("FR") == "🇫🇷"
    assert flag_emoji("id") == "🇮🇩"  # insensible a la casse
    assert flag_emoji(None) is None
    assert flag_emoji("XYZ") is None
    assert flag_emoji("1A") is None
