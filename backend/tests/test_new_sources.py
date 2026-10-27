"""IMO Iceland (quakes, volcanoes), JMA volcanic warnings, India NCS.

Fixtures are verbatim excerpts of the live endpoints captured on 2026-10-26
(`fixtures/new_sources_2026-10-26.json`, `fixtures/ncs_riseq_2026-10-26_excerpt.html`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.event import Kind, Severity
from app.sources.iceland import ImoQuakeSource, ImoVolcanoSource
from app.sources.jma_volcano import JmaVolcanoSource, classify
from app.sources.ncs import NcsSource, parse_time
from app.sources.registry import ExtraSourceSettings, extra_sources

FIXTURES = Path(__file__).parent / "fixtures"
REAL = json.loads((FIXTURES / "new_sources_2026-10-26.json").read_text(encoding="utf-8"))
NCS_HTML = (FIXTURES / "ncs_riseq_2026-10-26_excerpt.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------ IMO quakes


def _imo() -> tuple[ImoQuakeSource, dict[str, object]]:
    source = ImoQuakeSource()
    events = {e.source_id: e for e in source.parse_payload(REAL["imo_quakes"])}
    return source, events


def test_imo_reads_a_reviewed_quake():
    _, events = _imo()
    quake = events["IMO2026vashiz"]
    assert quake.kind is Kind.EARTHQUAKE
    assert quake.time == datetime(2026, 10, 25, 19, 55, 7, 152771, tzinfo=UTC)
    # GeoJSON order: [lon, lat]
    assert (quake.lat, quake.lon) == (63.600204, -23.550991)
    assert quake.depth_km == 10.0
    # six decimals in the payload, one here
    assert quake.magnitude == 1.9
    assert quake.preliminary is False
    # the Reykjanes RIDGE is offshore: no country stamped by the source
    assert quake.country is None
    assert quake.place == "Reykjaneshryggur"


def test_imo_labels_an_automatic_untyped_quake_preliminary():
    _, events = _imo()
    fresh = events["IMO2026vceayg"]
    assert fresh.kind is Kind.EARTHQUAKE  # type null: not typed yet
    assert fresh.preliminary is True
    assert fresh.magnitude == -0.3  # negative magnitudes are real


def test_imo_withdraws_what_an_analyst_deleted():
    source, events = _imo()
    assert "IMO2026thdfid" not in events
    assert source.retractions == ["imo:IMO2026thdfid"]


def test_imo_skips_an_unlocatable_event():
    _, events = _imo()
    assert "IMO2026uzeifz" not in events


def test_imo_never_calls_an_explosion_or_an_unknown_type_an_earthquake():
    _, events = _imo()
    kinds = {e.raw["type"]: e.kind for e in events.values()}
    assert kinds["explosion"] is Kind.OTHER
    assert kinds["anthropogenic event"] is Kind.OTHER


def test_imo_url_carries_an_explicit_utc_offset_and_the_floor():
    url = ImoQuakeSource(min_magnitude=1.0).build_url(datetime(2026, 10, 26, 19, 0, tzinfo=UTC))
    assert "start_time=2026-10-25T19%3A00%3A00%2B00%3A00" in url
    assert "size_min=1" in url


# --------------------------------------------------------------- IMO volcanoes


def test_imo_volcano_levels_above_green_only():
    events = {e.source_id: e for e in ImoVolcanoSource().parse_payload(REAL["imo_volcanoes"])}
    # Green (ASK) and no level at all (Beerenberg) are not published
    assert set(events) == {"REY", "BAR"}
    reykjanes = events["REY"]
    assert reykjanes.severity is Severity.SEVERE  # orange
    assert reykjanes.kind is Kind.VOLCANO
    assert reykjanes.ongoing is True
    assert reykjanes.country == "Iceland"
    assert (reykjanes.lat, reykjanes.lon) == (63.817, -22.717)
    assert reykjanes.time == datetime(2025, 10, 15, 16, 13, 52, 452639, tzinfo=UTC)
    assert "aviation YELLOW" in reykjanes.title
    assert events["BAR"].severity is Severity.MODERATE  # yellow


# ---------------------------------------------------------------- JMA volcano


def _jma() -> tuple[JmaVolcanoSource, dict[str, object]]:
    source = JmaVolcanoSource()
    source.load_list(REAL["jma_volcano_list"])
    events = {e.source_id: e for e in source.parse_payload(REAL["jma_volcano_warning"])}
    return source, events


def test_jma_level_3_is_severe_and_placed():
    _, events = _jma()
    sakurajima = events["506"]
    assert sakurajima.severity is Severity.SEVERE
    assert sakurajima.alert == "alert level 3 (do not approach the volcano)"
    assert (sakurajima.lat, sakurajima.lon) == (31.593, 130.657)
    assert sakurajima.place == "Sakurajima"
    assert sakurajima.time == datetime(2022, 7, 27, 11, 0, tzinfo=UTC)  # 20:00 JST


def test_jma_volcanoes_without_levels_are_ranked_by_their_words():
    _, events = _jma()
    assert events["329"].alert == "danger near the crater"  # Iwo Jima, 火口周辺危険
    assert events["331"].alert == "warning for the surrounding sea"  # submarine
    assert events["329"].severity is Severity.MODERATE


def test_jma_a_volcano_lowered_to_normal_is_lifted():
    source, events = _jma()
    assert "509" not in events  # Kuchinoerabujima, back to level 1
    assert "323" not in events  # Beyonesu Rocks, back to normal (submarine)
    assert sorted(source.lifts) == ["jma-volcano:323", "jma-volcano:509"]


def test_jma_full_width_digits_and_unknown_wordings():
    assert classify("レベル２（火口周辺規制）")[1] is Severity.MODERATE
    assert classify("レベル５（避難）")[1] is Severity.EXTREME
    assert classify("レベル１（活火山であることに留意）") is None
    with pytest.raises(LookupError):
        classify("見たことのない区分")


def test_jma_without_the_list_still_publishes_unplaced():
    source = JmaVolcanoSource()
    events = {e.source_id: e for e in source.parse_payload(REAL["jma_volcano_warning"])}
    assert events["506"].lat is None
    assert events["506"].place == "桜島"  # the Japanese name from the entry itself


# ------------------------------------------------------------------------ NCS


def test_ncs_reads_the_event_list_attribute():
    events = NcsSource().parse_payload(NCS_HTML)
    assert len(events) == 2
    tajik = next(e for e in events if e.place == "Tajikistan")
    # 23:37:01 IST is 18:07:01 UTC
    assert tajik.time == datetime(2026, 10, 26, 18, 7, 1, tzinfo=UTC)
    assert (tajik.lat, tajik.lon) == (38.626, 70.823)
    assert tajik.magnitude == 4.0
    assert tajik.depth_km == 10.0
    assert tajik.preliminary is False  # "Reviewed"
    # a national feed full of foreign events: no country stamped here
    assert tajik.country is None


def test_ncs_refuses_a_time_without_its_zone():
    assert parse_time("2026-10-26 23:37:01 IST") == datetime(2026, 10, 26, 18, 7, 1, tzinfo=UTC)
    assert parse_time("2026-10-26 23:37:01") is None
    assert parse_time("2026-10-26 23:37:01 UTC") is None


# ------------------------------------------------------------------- registry


def test_the_registry_builds_every_new_source_and_honours_its_switches():
    names = [s.name for s in extra_sources(ExtraSourceSettings())]
    assert names == ["imo", "imo-volcano", "jma-volcano", "ncs", "usdm"]
    # off by default: a 1:1 duplicate of Meteoalarm UK at the warning tier
    opted = ExtraSourceSettings(enable_ukea=True)
    assert "ukea" in [s.name for s in extra_sources(opted)]
    off = ExtraSourceSettings(enable_imo=False, enable_ncs=False)
    assert [s.name for s in extra_sources(off)] == ["imo-volcano", "jma-volcano", "usdm"]
