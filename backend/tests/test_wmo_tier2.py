"""WMO targeted second tier and IDEAM re-issues. Fixture: real items of the
aggregate of 2026-10-27 (`fixtures/wmo_tier2_2026-10-27.json`), unmodified."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.models.event import Kind
from app.sources.alerts_world import WmoCapSource, classify_wmo, tier2_candidate

REAL = json.loads(
    (Path(__file__).parent / "fixtures" / "wmo_tier2_2026-10-27.json").read_text(encoding="utf-8")
)
DURING = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)


def _ids(items):
    return {f"wmo:{i['id']}" for i in items}


def test_a_heat_or_flood_alert_from_an_issuer_we_do_not_read_is_kept():
    for key in ("chad_heat_tier2", "syria_flood_tier2"):
        (item,) = REAL[key]
        assert str(item["s"]) == "3"
        assert tier2_candidate(item), key


def test_the_french_and_arabic_names_classify():
    assert classify_wmo(REAL["chad_heat_tier2"][0]["event"]) is Kind.HEAT  # "Chaleur"
    assert classify_wmo(REAL["syria_flood_tier2"][0]["event"]) is Kind.FLOOD  # flash flood


def test_issuers_we_read_directly_and_bom_stay_at_the_top_tier():
    for key in ("nws_flood_tier2", "metoffice_flood_tier2", "bom_flood_tier2"):
        (item,) = REAL[key]
        assert not tier2_candidate(item), key


def test_a_kind_we_already_have_stays_at_the_top_tier():
    (item,) = REAL["kazakh_wildfire_tier2"]  # "Forestfire"
    assert not tier2_candidate(item)


def test_the_targeted_tier_is_off_when_asked_or_outside_top_tier_mode():
    data = {"items": REAL["chad_heat_tier2"]}
    assert WmoCapSource(targeted_tier2=False).select(data) == []
    assert WmoCapSource(max_severity_rank=2).targeted_tier2 is False


def test_an_ideam_reissue_is_one_alert_and_the_older_copies_are_withdrawn():
    copies = REAL["ideam_landslide_tier2_copies"]
    assert len(copies) == 3
    assert len({c["areaDesc"] for c in copies}) == 1  # identical content
    source = WmoCapSource()
    (kept,) = source.select({"items": copies})
    newest = max(copies, key=lambda c: c["sent"])
    assert kept["id"] == newest["id"]
    assert set(source.expirations) == _ids(copies) - {f"wmo:{newest['id']}"}


def test_the_published_tier2_events_are_never_minor():
    data = {"items": REAL["chad_heat_tier2"] + REAL["syria_flood_tier2"]}
    events = WmoCapSource().parse_payload(data, now=DURING)
    assert {e.kind for e in events} == {Kind.HEAT, Kind.FLOOD}
    assert all(e.severity.value not in ("minor", "info") for e in events)
