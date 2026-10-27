"""Round 2 sources: Environment Agency floods (England), US Drought Monitor.

Fixtures, copied unmodified from the live endpoints on 2026-10-27:
`ukea_floods_2026-10-27.json` (three items of /id/floods, levels 2, 3, 4),
`ukea_floodArea_2026-10-27.json` (the area record of the level-2 item),
`usdm_current_2026-10-22_D4.json` (the D4 feature of usdm_current.json).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.models.event import Kind, Severity
from app.sources.base import SourceHealth
from app.sources.ukea import UkEaFloodSource, area_position, bng_to_wgs84
from app.sources.usdm import parse_map

FX = Path(__file__).parent / "fixtures"
FLOODS = json.loads((FX / "ukea_floods_2026-10-27.json").read_text(encoding="utf-8"))
AREA = json.loads((FX / "ukea_floodArea_2026-10-27.json").read_text(encoding="utf-8"))["items"]
USDM = json.loads((FX / "usdm_current_2026-10-22_D4.json").read_text(encoding="utf-8"))


def test_bng_matches_the_ordnance_survey_worked_example():
    # OS "A guide to coordinate systems in Great Britain", Caister water tower:
    # E 651409.903 N 313177.270 -> 52 39' 27.2531" N, 1 43' 4.5177" E (OSGB36)
    lat, lon = bng_to_wgs84(651409.903, 313177.270)
    assert abs(lat - (52 + 39 / 60 + 27.2531 / 3600)) < 1e-6
    assert abs(lon - (1 + 43 / 60 + 4.5177 / 3600)) < 1e-6


def test_the_flood_area_box_contains_its_own_centroid():
    centre, box = area_position(AREA)
    assert centre == (50.7142, -1.98582)
    assert box is not None
    west, south, east, north = box
    assert west <= centre[1] <= east and south <= centre[0] <= north


def test_a_flood_warning_is_published_and_placed_on_its_area():
    source = UkEaFloodSource()
    area_id = AREA["notation"]
    source.areas[area_id] = area_position(AREA)
    events = {e.source_id: e for e in source.parse_payload(FLOODS)}
    warning = events[area_id]
    assert warning.kind is Kind.FLOOD
    assert warning.severity is Severity.MODERATE  # level 2, "immediate action required"
    assert (warning.lat, warning.lon) == (50.7142, -1.98582)
    assert warning.bbox is not None
    assert warning.time.tzinfo is not None
    assert warning.country_code == "GB"


def test_the_yellow_tier_and_ended_warnings_are_not_published():
    source = UkEaFloodSource()
    events = {e.source_id for e in source.parse_payload(FLOODS)}
    levels = {item["floodAreaID"]: item["severityLevel"] for item in FLOODS["items"]}
    alert = next(a for a, lv in levels.items() if lv == 3)
    ended = next(a for a, lv in levels.items() if lv == 4)
    assert alert not in events and ended not in events
    # level 4 = "warning no longer in force": a stored one is lifted
    assert source.lifts == [f"ukea:{ended}"]
    # a Flood Alert below the threshold is withdrawn, in case it was a downgrade
    assert source.expirations == [f"ukea:{alert}"]


def test_flood_alerts_can_be_opted_in():
    events = UkEaFloodSource(max_level=3).parse_payload(FLOODS)
    assert {e.severity for e in events} == {Severity.MODERATE, Severity.MINOR}


def test_drought_regions_from_the_weekly_map():
    released = datetime(2026, 10, 22, 10, 15, 57, tzinfo=UTC)
    events = parse_map(USDM, released)
    # 15 polygons of D4 on that map, 7 of them at least 0.25 square degrees
    assert len(events) == 7
    assert {e.kind for e in events} == {Kind.DROUGHT}
    assert {e.severity for e in events} == {Severity.SEVERE}
    assert all(e.time == released for e in events)
    assert all(e.bbox is not None for e in events)
    assert events[0].title == "Exceptional drought (D4) -- United States"
    assert len({e.id for e in events}) == len(events)


def test_every_source_has_a_display_name():
    assert SourceHealth("ncs").snapshot()["label"] == "NCS (India)"
    assert SourceHealth("ukea").snapshot()["label"] == "Environment Agency (England)"
    assert SourceHealth("unknown-source").snapshot()["label"] == "unknown-source"
