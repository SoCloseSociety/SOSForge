"""Japanese early warning and Chinese coverage (Wolfx relay).

Fixtures: verbatim excerpts from the feeds captured on 2026-08-17.
"""

from __future__ import annotations

from app.models.event import Kind, Severity
from app.sources.eew import CencSource, JmaEewSource

EEW = {
    "Title": "緊急地震速報（予報）",
    "Issue": {"Source": "東京", "Status": "通常"},
    "EventID": "20260817223317",
    "Serial": 4,
    "AnnouncedTime": "2026/08/17 22:34:00",
    "OriginTime": "2026/08/17 22:33:12",
    "Hypocenter": "日向灘",
    "Latitude": 32.0,
    "Longitude": 131.9,
    "Magunitude": 3.6,
    "Depth": 20,
    "MaxIntensity": "2",
    "isFinal": False,
}

CENC = {
    "No1": {
        "type": "reviewed",
        "EventID": "CD.20260817142722.7",
        "time": "2026-08-17 14:15:53",
        "ReportTime": "2026-08-17 14:27:34",
        "location": "甘肃陇南市礼县",
        "placeName": "甘肃陇南市礼县",
        "magnitude": "2.5",
        "depth": "10",
        "latitude": "33.92",
        "longitude": "104.95",
        "intensity": "4",
    }
}


def test_jma_time_is_tokyo_time_without_offset():
    """The main trap of this module: JMA timestamps in Japan time WITHOUT
    stating the offset. A naive parse would date the alert with the server's
    clock, a nine-hour error on information measured in seconds."""
    event = JmaEewSource().parse_payload(EEW)[0]
    assert event.time.hour == 13 and event.time.minute == 33  # 22:33 JST = 13:33 UTC
    assert event.time.tzinfo is not None


def test_jma_magnitude_key_is_misspelled_in_the_api():
    event = JmaEewSource().parse_payload(EEW)[0]
    assert event.magnitude == 3.6  # field "Magunitude", not "Magnitude"
    assert event.lat == 32.0 and event.depth_km == 20
    assert event.country_code == "JP"


def test_a_cancelled_early_warning_disappears():
    """An early detection is often a false positive, and the source cancels it.
    A cancelled alert left on display would be worse than no alert at all."""
    cancelled = {**EEW, "Issue": {"Source": "東京", "Status": "キャンセル"}}
    assert JmaEewSource().parse_payload(cancelled) == []


def test_expected_shaking_outranks_a_modest_magnitude():
    """An early warning is for taking cover: what matters is the expected
    shaking on the ground, not the magnitude estimate of the first seconds."""
    strong = {**EEW, "MaxIntensity": "6+"}
    assert JmaEewSource().parse_payload(strong)[0].severity is Severity.EXTREME

    # a WARNING bulletin (警報) rates at least "severe", even at a low magnitude
    warning = {**EEW, "Title": "緊急地震速報（警報）", "Magunitude": 4.0}
    assert JmaEewSource().parse_payload(warning)[0].severity is Severity.SEVERE


def test_jma_garbage_is_ignored():
    assert JmaEewSource().parse_payload({}) == []
    assert JmaEewSource().parse_payload([]) == []
    assert JmaEewSource().parse_payload({"EventID": "x", "OriginTime": "not a date"}) == []


def test_cenc_payload_is_a_dict_not_a_list():
    """Indexed No1, No2... Iterating over the keys would yield the strings "No1"."""
    events = CencSource().parse_payload(CENC)
    assert len(events) == 1

    event = events[0]
    assert event.kind is Kind.EARTHQUAKE
    assert event.magnitude == 2.5  # every number arrives as a string
    assert event.lat == 33.92
    assert event.country_code == "CN"
    # 14:15 Beijing time = 06:15 UTC
    assert event.time.hour == 6 and event.time.minute == 15


def test_cenc_garbage_is_ignored():
    assert CencSource().parse_payload({}) == []
    assert CencSource().parse_payload({"No1": "not an object"}) == []
    assert CencSource().parse_payload({"No1": {"EventID": "x", "time": "never"}}) == []
