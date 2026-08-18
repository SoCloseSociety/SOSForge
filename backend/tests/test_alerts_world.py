"""Non-US alerts: Meteoalarm (Europe) and the WMO CAP aggregate.

Fixtures: verbatim excerpts from the feeds captured on 2026-08-17.
"""

from __future__ import annotations

from app.models.event import Kind, Severity
from app.sources.alerts_world import classify_wmo, parse_meteoalarm, parse_wmo

# ------------------------------------------------------------------ Meteoalarm

# an active thunderstorm warning, with its two `info` blocks (French then English)
ALERT = {
    "alert": {
        "identifier": "2.49.0.0.250.0.FR.20260812160107.974023",
        "info": [
            {
                "language": "fr-FR",
                "event": "Vigilance jaune orages",
                "headline": "Vigilance jaune orages",
                "severity": "Minor",
                "responseType": ["Monitor"],
                "onset": "2026-08-12T16:01:00+02:00",
                "expires": "2026-08-13T06:00:00+02:00",
                "web": "https://vigilance.meteofrance.fr",
                "area": [
                    {"areaDesc": "Alpes-de-Haute-Provence", "geocode": [{"value": "FR821"}]},
                    {"areaDesc": "Hautes Alpes", "geocode": [{"value": "FR822"}]},
                    {"areaDesc": "Var", "geocode": [{"value": "FR825"}]},
                ],
                "parameter": [
                    {"value": "2; yellow; Moderate", "valueName": "awareness_level"},
                    {"value": "3; Thunderstorm", "valueName": "awareness_type"},
                ],
            },
            {
                "language": "en-GB",
                "event": "Yellow thunderstorm warning",
                "headline": "Yellow thunderstorm warning",
                "severity": "Minor",
                "responseType": ["Monitor"],
                "onset": "2026-08-12T16:01:00+02:00",
                "expires": "2026-08-13T06:00:00+02:00",
                "area": [{"areaDesc": "Alpes-de-Haute-Provence", "geocode": [{"value": "FR821"}]}],
                "parameter": [
                    {"value": "2; yellow; Moderate", "valueName": "awareness_level"},
                    {"value": "3; Thunderstorm", "valueName": "awareness_type"},
                ],
            },
        ],
    }
}

LIFTED = {
    "alert": {
        "identifier": "2.49.0.0.250.0.FR.20260812060108.090023",
        "info": [
            {
                "language": "en-GB",
                "event": "Yellow thunderstorm warning",
                "severity": "Minor",
                "responseType": ["AllClear"],
                "onset": "2026-08-12T06:01:00+02:00",
                "expires": "2026-08-12T06:00:00+02:00",
                "area": [{"areaDesc": "Hérault", "geocode": [{"value": "FR813"}]}],
                "parameter": [
                    {"value": "1; green; Minor", "valueName": "awareness_level"},
                    {"value": "3; Thunderstorm", "valueName": "awareness_type"},
                ],
            }
        ],
    }
}


def test_only_one_event_per_warning_despite_two_language_blocks():
    """Each warning carries its content twice (local language + English).
    Without an explicit choice, each one produced two events."""
    event = parse_meteoalarm(ALERT, "france")
    assert event is not None
    # the English block is preferred: the title must not be in French
    assert event.title == "Yellow thunderstorm warning"


def test_awareness_type_drives_the_kind_not_the_local_label():
    """`event` is written in the country's language ("Vigilance jaune orages"):
    only `awareness_type`, a standard code in English, is usable."""
    event = parse_meteoalarm(ALERT, "france")
    assert event is not None
    assert event.kind is Kind.STORM


def test_awareness_level_is_a_composite_string():
    """ "2; yellow; Moderate" -- the severity is the first field, not the string."""
    event = parse_meteoalarm(ALERT, "france")
    assert event is not None
    assert event.severity is Severity.MODERATE
    assert event.ongoing is True
    assert event.time.tzinfo is not None  # onset carries a local offset (+02:00)
    assert "Alpes-de-Haute-Provence" in event.place


def test_allclear_is_a_lifted_warning_not_an_alert():
    """Like the "no danger" tsunami bulletins: it is displayed, it does not alert."""
    event = parse_meteoalarm(LIFTED, "france")
    assert event is not None
    assert event.severity is Severity.INFO
    assert event.ongoing is False
    assert event.alert == "lifted"


def test_meteoalarm_garbage_is_ignored():
    assert parse_meteoalarm({}, "france") is None
    assert parse_meteoalarm({"alert": {"identifier": "x", "info": []}}, "france") is None


# ------------------------------------------------------------------------- WMO

WMO_ITEM = {
    "id": "IN-1786996079872015_69",
    "event": "Moderate Rain",
    "headline": "Moderate Rain is very likely to continue",
    "sent": "2026-08-17 20:17:19",
    "expires": "2026-08-17 23:15:00",
    "areaDesc": "Gomati, Sepahijala, South Tripura, West Tripura",
    "mid": "066",
    "s": 3,
    "u": 3,
    "c": 3,
    "url": "in-ndma-xx/2026/08/17/20/17/19-77728c723.xml",
    "effective": "2026-08-17 20:15:00",
}


def test_wmo_ranks_are_cap_positions_not_scores():
    """`s` is 1 for Extreme and 4 for Minor: it is a CAP rank, so the smaller
    it is, the more severe."""
    event = parse_wmo(WMO_ITEM)
    assert event is not None
    assert event.severity is Severity.MODERATE  # s = 3

    extreme = parse_wmo({**WMO_ITEM, "id": "X-1", "s": 1})
    assert extreme is not None and extreme.severity is Severity.EXTREME

    unknown = parse_wmo({**WMO_ITEM, "id": "X-2", "s": 0})
    assert unknown is not None and unknown.severity is Severity.INFO


def test_wmo_timestamps_have_no_timezone_and_are_utc():
    event = parse_wmo(WMO_ITEM)
    assert event is not None
    assert event.time.tzinfo is not None
    assert event.time.hour == 20


def test_wmo_links_back_to_the_source_cap():
    """The aggregate's timestamps have shown gaps against the source CAP: the
    link to the CAP must remain reachable."""
    event = parse_wmo(WMO_ITEM)
    assert event is not None
    assert event.url is not None
    assert event.url.startswith("https://severeweather.wmo.int/v2/cap-alerts/")


def test_wmo_country_comes_from_the_id_prefix():
    """It is the feed's only country indication, and it is enough for the flag."""
    assert parse_wmo(WMO_ITEM).country_code == "IN"
    assert parse_wmo({**WMO_ITEM, "id": "CN-42"}).country_code == "CN"
    # an identifier without a country prefix must not invent a flag
    assert parse_wmo({**WMO_ITEM, "id": "12345-x"}).country_code is None


def test_wmo_classification():
    assert classify_wmo("Tsunami Warning") is Kind.TSUNAMI
    assert classify_wmo("Tropical Cyclone Warning") is Kind.CYCLONE
    assert classify_wmo("Moderate Rain") is Kind.STORM
    assert classify_wmo("Flash Flood") is Kind.FLOOD
    assert classify_wmo("Heat Wave") is Kind.HEAT
    assert classify_wmo("Something Unheard Of") is Kind.OTHER


def test_wmo_garbage_is_ignored():
    assert parse_wmo({}) is None
    assert parse_wmo({"id": "x", "sent": "not a date"}) is None
