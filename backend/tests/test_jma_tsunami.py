"""JMA tsunami advisories.

Every fixture below is a VERBATIM excerpt of
`https://www.jma.go.jp/bosai/tsunami/data/list.json`, captured on 2026-08-26.
The three rows are the complete live list as served that day: the Kumamoto
M7.1 of 2026-07-28, its advisory, its arrival-time information bulletin, and
its lift.
"""

from __future__ import annotations

from datetime import UTC

from app.models.event import Kind, Severity
from app.sources.jma_tsunami import JmaTsunamiSource, classify

# The lift, published at 18:10 JST.
ROW_LIFTED = {
    "ctt": "20260728181010",
    "eid": "20260728162718",
    "rdt": "2026-07-28T18:10:00+09:00",
    "ttl": "津波予報",
    "ift": "発表",
    "ser": 0,
    "at": "2026-07-28T16:27:00+09:00",
    "anm": "熊本県熊本地方",
    "acd": "741",
    "cod": "+32.6+130.7-10000/",
    "mag": "7.1",
    "kind": [{"code": "712", "kind": "津波注意報解除"}],
    "json": "20260728181010_20260728162718_VTSE41_0.json",
    "en_ttl": "Tsunami Forecast",
    "en_anm": "Kumamoto Region, Kumamoto Prefecture",
}

# The arrival-time information: carries NO tsunami category at all, and note
# `ser` arriving as a string here where the other two rows send an int.
ROW_INFORMATION = {
    "ctt": "20260728162948",
    "eid": "20260728162718",
    "rdt": "2026-07-28T16:29:00+09:00",
    "ttl": "各地の満潮時刻・津波到達予想時刻に関する情報",
    "ift": "発表",
    "ser": "1",
    "at": "2026-07-28T16:27:00+09:00",
    "anm": "熊本県熊本地方",
    "acd": "741",
    "cod": "+32.6+130.7-10000/",
    "mag": "7.1",
    "kind": [],
    "json": "20260728162948_20260728162718_VTSE51_1.json",
    "en_ttl": (
        "Tsunami Information (High Tide Time and Estimated Tsunami Arrival Time at each place)"
    ),
    "en_anm": "Kumamoto Region, Kumamoto Prefecture",
}

# The advisory itself, published at 16:29 JST: one area under 津波注意報 and two
# under 津波予報.
ROW_ADVISORY = {
    "ctt": "20260728162913",
    "eid": "20260728162718",
    "rdt": "2026-07-28T16:29:00+09:00",
    "ttl": "津波注意報・津波予報",
    "ift": "発表",
    "ser": 0,
    "at": "2026-07-28T16:27:00+09:00",
    "anm": "熊本県熊本地方",
    "acd": "741",
    "cod": "+32.6+130.7-10000/",
    "mag": "7.1",
    "kind": [
        {"code": "712", "kind": "津波注意報"},
        {"code": "730", "kind": "津波予報（若干の海面変動）"},
        {"code": "740", "kind": "津波予報（若干の海面変動）"},
    ],
    "json": "20260728162913_20260728162718_VTSE41_0.json",
    "en_ttl": "Tsunami Advisory / Tsunami Forecast",
    "en_anm": "Kumamoto Region, Kumamoto Prefecture",
}

LIVE_LIST = [ROW_LIFTED, ROW_INFORMATION, ROW_ADVISORY]


def test_the_category_lives_in_the_text_not_in_the_code():
    """`kind[].code` is the AREA code (712 = Ariake Sea), never a severity.

    Cross-read against the detail bulletin for this very event, where area 712
    carries category code 62. Ranking on `code` would put a forecast for
    Nagasaki (730) above a major warning.
    """
    assert classify("津波注意報") == ("tsunami advisory", Severity.MODERATE)
    assert classify("津波予報（若干の海面変動）") == ("tsunami forecast", Severity.INFO)
    # every code in the live payload is an area, and none of them is a severity
    assert [a["code"] for a in ROW_ADVISORY["kind"]] == ["712", "730", "740"]


def test_a_major_warning_is_not_demoted_to_a_warning():
    """大津波警報 CONTAINS 津波警報: order in the table is what keeps them apart."""
    assert classify("大津波警報") == ("major tsunami warning", Severity.EXTREME)
    assert classify("津波警報") == ("tsunami warning", Severity.SEVERE)
    # the suffixed variants JMA also publishes must not fall through
    assert classify("津波警報（継続）") == ("tsunami warning", Severity.SEVERE)


def test_a_lift_is_not_an_alert():
    """津波注意報解除 contains 津波注意報: the lift has to be tested first."""
    assert classify("津波注意報解除") is None
    assert classify("大津波警報解除") is None
    assert classify("") is None
    assert classify(None) is None


def test_one_event_not_one_marker_per_bulletin():
    """Three bulletins, one `eid`: the key is the EVENT.

    The advisory row is the one read, because the lift is dropped in the
    retraction test below -- here we prove the grouping by feeding the two
    rows that precede the lift.
    """
    source = JmaTsunamiSource()
    events = source.parse_payload([ROW_INFORMATION, ROW_ADVISORY])
    assert len(events) == 1
    assert events[0].id == "jma-tsunami:20260728162718"


def test_the_advisory_parses_with_the_severity_of_its_worst_area():
    source = JmaTsunamiSource()
    (event,) = source.parse_payload([ROW_ADVISORY])

    assert event.kind is Kind.TSUNAMI
    assert event.tsunami is True
    # one area at 注意報 (moderate), two at 予報 (info): the worst one wins
    assert event.severity is Severity.MODERATE
    assert event.alert == "tsunami advisory"
    # 16:29 Japan time (+09:00) = 07:29 UTC. The event is dated when JMA
    # ISSUED the advisory, not when the quake happened.
    assert event.time.astimezone(UTC).hour == 7
    assert event.time.astimezone(UTC).minute == 29
    assert event.raw["origin_time"] == "2026-07-28T16:27:00+09:00"
    # ISO 6709, JMA conventions: depth in metres and negative
    assert (event.lat, event.lon) == (32.6, 130.7)
    assert event.depth_km == 10.0
    assert event.magnitude == 7.1
    # the ALERT is Japan's; the epicentre is named, never confused with it
    assert event.place == "Japan"
    assert event.country == "Japan"
    assert "Kumamoto Region, Kumamoto Prefecture" in event.title
    assert event.raw["area_codes"] == ["712", "730", "740"]


def test_an_advisory_is_ongoing_and_can_expire():
    """An alert that can never expire is a bug: JMA quotes no expiry, so the
    ceiling is ours and it is explicit."""
    source = JmaTsunamiSource(max_advisory_hours=12.0)
    (event,) = source.parse_payload([ROW_ADVISORY])
    assert event.ongoing is True
    assert event.expires is not None
    assert (event.expires - event.time).total_seconds() == 12 * 3600


def test_a_lifted_advisory_is_withdrawn_not_published():
    """The live list still carried this event a month after it was lifted:
    silence is never what removes a JMA tsunami advisory."""
    source = JmaTsunamiSource()
    events = source.parse_payload(LIVE_LIST)
    assert events == []
    assert source.retractions == ["jma-tsunami:20260728162718"]


def test_a_bulletin_with_no_category_never_decides_the_level():
    """The arrival-time information says nothing about the advisory level, so
    it must not be read as the latest word -- even though it is newer than the
    advisory it accompanies."""
    source = JmaTsunamiSource()
    (event,) = source.parse_payload([ROW_ADVISORY, ROW_INFORMATION])
    assert event.severity is Severity.MODERATE
    assert source.retractions == []


def test_a_partially_lifted_bulletin_keeps_the_areas_still_at_risk():
    """One area lifted, one still under warning: the event stays, at the
    severity of what is still in force."""
    row = {
        **ROW_ADVISORY,
        "kind": [
            {"code": "712", "kind": "津波注意報解除"},
            {"code": "730", "kind": "津波警報"},
        ],
    }
    source = JmaTsunamiSource()
    (event,) = source.parse_payload([row])
    assert event.severity is Severity.SEVERE
    assert source.retractions == []


def test_junk_never_takes_the_batch_down():
    source = JmaTsunamiSource()
    assert source.parse_payload([]) == []
    assert source.parse_payload(None) == []
    # no eid, no key; no category, no level
    assert source.parse_payload([{"kind": [{"code": "1", "kind": "津波警報"}]}]) == []
