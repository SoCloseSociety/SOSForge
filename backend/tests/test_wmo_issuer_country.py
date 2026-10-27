"""WMO: the country from the issuing service, for land hazards only. Every
item is copied unmodified from the aggregate of 2026-10-27 11:17 UTC
(`fixtures/wmo_issuer_country_2026-10-27.json`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sources.alerts_world import classify_wmo, issuer_country, parse_wmo

REAL = json.loads(
    (Path(__file__).parent / "fixtures" / "wmo_issuer_country_2026-10-27.json").read_text(
        encoding="utf-8"
    )
)


def flag(key: str) -> str | None:
    item = REAL[key]
    return issuer_country(item, classify_wmo(item.get("event") or ""))


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        # land hazards from a national service: its own country
        ("ideam_landslide_armenia", "CO"),  # "Armenia" is a Colombian town here
        ("cma_gale_land", "CN"),  # CMA's "gale" is a county land-wind warning
        ("dwd_gale_force_gusts", "DE"),  # gusts over Wernigerode
        ("metoffice_coastal_flood", "GB"),  # `uk` prefix; a coastal flood is on land
        ("kma_strong_wind_land", "KR"),
        # a US territory has its own code
        ("nws_saipan_flood", "MP"),
        # marine products: no flag
        ("metno_gale_sea", None),
        ("smhi_near_gale", None),
        ("dwd_near_gale_coast", None),
        ("kma_wind_wave", None),
        ("nws_marine", None),
        # not a land hazard kind
        ("cma_dense_fog", None),
    ],
)
def test_issuer_country(key: str, expected: str | None):
    assert flag(key) == expected


def test_the_flag_reaches_the_event():
    event = parse_wmo(REAL["ideam_landslide_armenia"])
    assert event is not None and event.country_code == "CO"
    event = parse_wmo(REAL["metno_gale_sea"])
    assert event is not None and event.country_code is None


# Recheck of 0cc11f6: "wave" was a substring of the marine phrases, so every
# heatwave was a sea product and lost its country. Items copied unmodified
# from the aggregate of 2026-10-27 13:47 UTC (`wmo_wave_2026-10-27.json`).
WAVE = json.loads(
    (Path(__file__).parent / "fixtures" / "wmo_wave_2026-10-27.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("bom_heatwave_0", "AU"),  # "Severe heatwave warning for Queensland"
        ("bom_heatwave_1", "AU"),  # "... for Northern Territory"
        ("wind_wave_advisory", None),  # KMA, nearshore waters: the sea
    ],
)
def test_a_heatwave_is_not_a_sea_product(key: str, expected: str | None):
    item = WAVE[key]
    assert issuer_country(item, classify_wmo(item.get("event") or "")) == expected


def test_wave_is_read_as_a_word_not_after_heat_or_cold():
    from app.sources.alerts_world import _is_marine, wmo_issuer

    def marine(item: dict) -> bool:
        return _is_marine(item, wmo_issuer(item), classify_wmo(item.get("event") or ""))

    assert marine(WAVE["wave_height_warning"])  # UA, Danube Delta
    assert marine(WAVE["wind_wave_advisory"])
    assert not marine(WAVE["bom_heatwave_0"])
    assert not marine(WAVE["cold_wave"])  # CMA, Gaoyi County
    assert not marine(WAVE["coldwave_advisory"])  # KMA, Yangpyeong
    # the two-word heat wave has no item in any capture: the same BOM item,
    # with the name spelled the way other services spell it
    heat_wave = dict(WAVE["bom_heatwave_0"], headline="Heat Wave warning for Queensland")
    assert not marine(heat_wave)
