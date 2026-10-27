"""Lane H's findings on Meteoalarm (Greece, Croatia) and the WMO OID country.
Fixtures: `fixtures/meteoalarm_gr_hr_2026-10-27.json` and
`fixtures/wmo_issuer_country_2026-10-27.json`, copied unmodified."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.models.event import Severity
from app.sources.alerts_world import MeteoalarmSource, classify_wmo, issuer_country, oid_country

FX = Path(__file__).parent / "fixtures"
METEO = json.loads((FX / "meteoalarm_gr_hr_2026-10-27.json").read_text(encoding="utf-8"))
WMO = json.loads((FX / "wmo_issuer_country_2026-10-27.json").read_text(encoding="utf-8"))
DURING = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)


def test_two_greek_warnings_for_one_region_are_two_hazards_and_say_so():
    """Not a double: HNMS issues a thunderstorm AND a rain warning for
    Thessalia, under different ids, neither referencing the other."""
    pair = [METEO["greece_thessalia_thunderstorm"], METEO["greece_thessalia_rain"]]
    events = MeteoalarmSource(min_level=3).select({"warnings": pair}, "greece", now=DURING)
    assert len(events) == 2
    titles = sorted(e.title for e in events)
    assert titles == ["Orange Thunderstorm for Thessalia", "Orange rain warning for Thessalia"]


def test_an_official_orange_is_never_shown_below_its_colour():
    """Velebit, 2026-10-27: "3; orange; Severe", certainty Possible. The CAP
    rule (a forecast one rung down, `Possible` one more) published it MINOR --
    a yellow's rung. For Meteoalarm the colour is the floor: orange never
    below MODERATE."""
    warning = METEO["croatia_velebit_orange"]
    (event,) = MeteoalarmSource(min_level=3).select({"warnings": [warning]}, "croatia", now=DURING)
    assert event.raw["awareness_level"] == "3; orange; Severe"
    assert event.raw["certainty"] == "Possible"
    assert event.severity is Severity.MODERATE


def test_the_floor_is_meteoalarm_only():
    """The NWS / WMO "Severe, Possible" demotion is untouched."""
    from app.models.event import severity_for_cap

    assert severity_for_cap("severe", "future", "possible") is Severity.MINOR


def test_the_oid_names_the_issuing_country_and_must_agree():
    item = WMO["ideam_landslide_armenia"]
    assert item["id"].startswith("2.49.0.0.170.")
    assert oid_country(item) == "CO"
    assert issuer_country(item, classify_wmo(item["event"])) == "CO"
    # the NWS is 840; Saipan is its territory and keeps its own code
    saipan = WMO["nws_saipan_flood"]
    assert oid_country(saipan) == "US"
    assert issuer_country(saipan, classify_wmo(saipan["event"])) == "MP"
    # a directory that disagrees with the identifier gets no flag
    forged = {**item, "id": item["id"].replace("2.49.0.0.170.", "2.49.0.0.218.", 1)}
    assert issuer_country(forged, classify_wmo(item["event"])) is None
