"""NOAA SWPC space weather tests, on real payloads captured 2026-08-18 from
the three live endpoints (`noaa-scales.json`, `noaa-planetary-k-index-forecast.json`,
`alerts.json`). All fixtures below are verbatim excerpts -- see CLAUDE.md:
a unit test on an invented payload proves nothing here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.models.event import Kind, Severity
from app.sources.space import (
    SpaceWeatherSource,
    _classify_alert,
    _implied_valid_until,
    _kp_to_g,
    _parse_valid_until,
    _severity_from_scale,
)

# --------------------------------------------------------------------- fixtures

# Verbatim `noaa-scales.json`, captured 2026-08-18 10:32 UTC: a quiet period,
# G1 forecast for the next two days, nothing beyond.
SCALES_PAYLOAD = {
    "0": {
        "DateStamp": "2026-08-18",
        "TimeStamp": "10:32:00",
        "R": {"Scale": "0", "Text": "none", "MinorProb": None, "MajorProb": None},
        "S": {"Scale": "0", "Text": "none", "Prob": None},
        "G": {"Scale": "0", "Text": "none"},
    },
    "1": {
        "DateStamp": "2026-08-18",
        "TimeStamp": "10:32:00",
        "R": {"Scale": None, "Text": None, "MinorProb": "35", "MajorProb": "5"},
        "S": {"Scale": None, "Text": None, "Prob": "5"},
        "G": {"Scale": "1", "Text": "minor"},
    },
    "2": {
        "DateStamp": "2026-08-19",
        "TimeStamp": "00:00:00",
        "R": {"Scale": None, "Text": None, "MinorProb": "35", "MajorProb": "5"},
        "S": {"Scale": None, "Text": None, "Prob": "5"},
        "G": {"Scale": "1", "Text": "minor"},
    },
    "3": {
        "DateStamp": "2026-08-20",
        "TimeStamp": "00:00:00",
        "R": {"Scale": None, "Text": None, "MinorProb": "35", "MajorProb": "5"},
        "S": {"Scale": None, "Text": None, "Prob": "5"},
        "G": {"Scale": "0", "Text": "none"},
    },
    "-1": {
        "DateStamp": "2026-08-17",
        "TimeStamp": "10:32:00",
        "R": {"Scale": "0", "Text": "none", "MinorProb": None, "MajorProb": None},
        "S": {"Scale": "0", "Text": "none", "Prob": None},
        "G": {"Scale": "1", "Text": "minor"},
    },
}

# Verbatim rows from `noaa-planetary-k-index-forecast.json` (81 rows total:
# 59 observed / 5 estimated / 17 predicted, matching CLAUDE.md's sample).
# Trimmed to one of each family plus the two highest predicted points.
KP_OBSERVED_ROW = {
    "time_tag": "2026-08-11T00:00:00",
    "kp": 1.0,
    "observed": "observed",
    "noaa_scale": None,
}
KP_ESTIMATED_ROW = {
    "time_tag": "2026-08-18T09:00:00",
    "kp": 4.67,
    "observed": "estimated",
    "noaa_scale": "G1",
}
KP_PREDICTED_PEAK = {
    "time_tag": "2026-08-19T03:00:00",
    "kp": 4.67,
    "observed": "predicted",
    "noaa_scale": "G1",
}
KP_PREDICTED_LOWER = {
    "time_tag": "2026-08-19T06:00:00",
    "kp": 4.0,
    "observed": "predicted",
    "noaa_scale": None,
}
KP_FORECAST_ROWS = [KP_OBSERVED_ROW, KP_ESTIMATED_ROW, KP_PREDICTED_PEAK, KP_PREDICTED_LOWER]

# Verbatim items from `alerts.json`, one per message family exercised below.
ALERT_ALTK05 = {
    "product_id": "K05A",
    "issue_datetime": "2026-08-18 05:06:25.137",
    "message": (
        "Space Weather Message Code: ALTK05\r\nSerial Number: 2045\r\n"
        "Issue Time: 2026 Aug 18 0506 UTC\r\n\r\nALERT: Geomagnetic K-index of 5 \n"
        "Threshold Reached: 2026 Aug 18 0505 UTC\nSynoptic Period: 0300-0600\n"
        "Active Warning: YES\nNoaa Scale: G1 - Minor\nComment: \r\n\r\n"
        "NOAA Scale: G1 - Minor\r\n\r\nNOAA Space Weather Scale descriptions can be found at\r\n"
        "www.swpc.noaa.gov/noaa-scales-explanation\r\n\r\n"
        "Potential Impacts: Area of impact primarily "
        "poleward of 60 degrees Geomagnetic Latitude.\r\n"
        "Induced Currents - Weak power grid fluctuations can occur.\r\n"
        "Spacecraft - Minor impact on satellite operations possible.\r\n"
        "Aurora - Aurora may be visible at high latitudes, i.e., northern tier of the U.S. "
        "such as northern Michigan and Maine."
    ),
}

ALERT_SUMSUD = {
    "product_id": "MSIS",
    "issue_datetime": "2026-08-11 11:41:52.250",
    "message": (
        "Space Weather Message Code: SUMSUD\r\nSerial Number: 302\r\n"
        "Issue Time: 2026 Aug 11 1141 UTC\r\n\r\nSUMMARY: Geomagnetic Sudden Impulse \n"
        "Observed: 2026 Aug 11 1133 UTC\nDeviation: 12 nT\nStation: BOU\nComment: "
    ),
}

ALERT_WARK05 = {
    "product_id": "K05W",
    "issue_datetime": "2026-08-18 02:27:00.810",
    "message": (
        "Space Weather Message Code: WARK05\r\nSerial Number: 2259\r\n"
        "Issue Time: 2026 Aug 18 0227 UTC\r\n\r\nWARNING: Geomagnetic K-index of 5 expected \n"
        "Valid From: 2026 Aug 18 0224 UTC\nValid To: 2026 Aug 18 1200 UTC\n"
        "Warning Conditions: Onset\nNoaa Scale: G1 - Minor\nComment: \r\n\r\n"
        "NOAA Scale: G1 - Minor\r\n\r\nNOAA Space Weather Scale descriptions can be found at\r\n"
        "www.swpc.noaa.gov/noaa-scales-explanation\r\n\r\n"
        "Potential Impacts: Area of impact primarily "
        "poleward of 60 degrees Geomagnetic Latitude.\r\n"
        "Induced Currents - Weak power grid fluctuations can occur.\r\n"
        "Spacecraft - Minor impact on satellite operations possible.\r\n"
        "Aurora - Aurora may be visible at high latitudes, i.e., northern tier of the U.S. "
        "such as northern Michigan and Maine."
    ),
}

ALERT_WARK04_EXTENDED = {
    "product_id": "K04W",
    "issue_datetime": "2026-08-18 02:27:43.313",
    "message": (
        "Space Weather Message Code: WARK04\r\nSerial Number: 5403\r\n"
        "Issue Time: 2026 Aug 18 0227 UTC\r\n\r\n"
        "EXTENDED WARNING: Geomagnetic K-index of 4 expected\nExtension to Serial Number: 5402\n"
        "Valid From: 2026 Aug 17 2101 UTC\nNow Valid Until: 2026 Aug 18 1500 UTC\n"
        "Warning Condition: Persistence\n\r\n\r\n"
        "NOAA Space Weather Scale descriptions can be found at\r\n"
        "www.swpc.noaa.gov/noaa-scales-explanation\r\n\r\n"
        "Potential Impacts: Area of impact primarily "
        "poleward of 65 degrees Geomagnetic Latitude.\r\n"
        "Induced Currents - Weak power grid fluctuations can occur.\r\n"
        "Aurora - Aurora may be visible at high latitudes such as Canada and Alaska."
    ),
}

ALERT_WATA20_WATCH = {
    "product_id": "A20F",
    "issue_datetime": "2026-08-17 22:46:24.740",
    "message": (
        "Space Weather Message Code: WATA20\r\nSerial Number: 1121\r\n"
        "Issue Time: 2026 Aug 17 2246 UTC\r\n\r\nWATCH: Geomagnetic Storm Category G1 Predicted \n"
        "Highest Storm Level Predicted by Day:\n"
        "Aug 18:  None (Below G1)   Aug 19:  G1 (Minor)   Aug 20:  None (Below G1)   \n"
        "THIS SUPERSEDES ANY/ALL PRIOR WATCHES IN EFFECT\nComment: \r\n\r\n"
        "NOAA Space Weather Scale descriptions can be found at\r\n"
        "www.swpc.noaa.gov/noaa-scales-explanation\r\n\r\n"
        "Potential Impacts: Area of impact primarily "
        "poleward of 60 degrees Geomagnetic Latitude.\r\n"
        "Induced Currents - Weak power grid fluctuations can occur.\r\n"
        "Spacecraft - Minor impact on satellite operations possible.\r\n"
        "Aurora - Aurora may be visible at high latitudes, i.e., northern tier of the U.S. "
        "such as northern Michigan and Maine."
    ),
}

ALERT_WATA20_CANCEL = {
    "product_id": "A20F",
    "issue_datetime": "2026-08-11 20:55:54.723",
    "message": (
        "Space Weather Message Code: WATA20\r\nSerial Number: 1120\r\n"
        "Issue Time: 2026 Aug 11 2055 UTC\r\n\r\n"
        "CANCEL WATCH: Geomagnetic Storm Category G1 Predicted \nCancel Serial Number: 1119\n"
        "Original Issue Time: 2026 Aug 09 1338 UTC\nCancelled Level Predicted:\n"
        "Aug 10  : None (Bellow G1)  Aug 11  : None (Bellow G1)  Aug 12  : None (Bellow G1)  \n"
        "CME arrival 11AUG\r\n\r\nNOAA Space Weather Scale descriptions can be found at\r\n"
        "www.swpc.noaa.gov/noaa-scales-explanation\r\n\r\n"
        "Potential Impacts: Area of impact primarily "
        "poleward of 60 degrees Geomagnetic Latitude.\r\n"
        "Induced Currents - Weak power grid fluctuations can occur.\r\n"
        "Spacecraft - Minor impact on satellite operations possible.\r\n"
        "Aurora - Aurora may be visible at high latitudes, i.e., northern tier of the U.S. "
        "such as northern Michigan and Maine."
    ),
}

ALERT_WARSUD = {
    "product_id": "SGIW",
    "issue_datetime": "2026-08-11 10:47:33.163",
    "message": (
        "Space Weather Message Code: WARSUD\r\nSerial Number: 258\r\n"
        "Issue Time: 2026 Aug 11 1047 UTC\r\n\r\nWARNING: Geomagnetic Sudden Impulse expected \n"
        "Valid From: 2026 Aug 11 1040 UTC\nValid To: 2026 Aug 11 1248 UTC\n"
        "Ip Shock: 2026-08-11 10:43\nComment: Weak shock detected at L1.\n"
    ),
}

ALERT_WARPX1 = {
    "product_id": "P11W",
    "issue_datetime": "2026-07-31 21:54:42.097",
    "message": (
        "Space Weather Message Code: WARPX1\r\nSerial Number: 630\r\n"
        "Issue Time: 2026 Jul 31 2154 UTC\r\n\r\n"
        "EXTENDED WARNING: Proton 10MeV Integral Flux above 10pfu expected\n"
        "Extension to Serial Number: 629\nValid From: 2026 Jul 31 1335 UTC\n"
        "Now Valid Until: 2026 Aug 01 1200 UTC\nWarning Condition: Persistence\n\r\n\r\n"
        "NOAA Scale: S1 - Minor"
    ),
}


# --------------------------------------------------------------------- _kp_to_g


def test_kp_to_g_below_storm_threshold_is_zero():
    assert _kp_to_g(4.9) == 0  # G-scale only starts at Kp 5


def test_kp_to_g_thresholds():
    assert _kp_to_g(5.0) == 1
    assert _kp_to_g(6.33) == 2
    assert _kp_to_g(7.0) == 3
    assert _kp_to_g(8.0) == 4
    assert _kp_to_g(9.0) == 5


def test_kp_to_g_never_exceeds_five():
    assert _kp_to_g(9.99) == 5


# ---------------------------------------------------------------- _severity_from_scale


def test_severity_from_scale_g1_g2_are_routine():
    assert _severity_from_scale(1) is Severity.MINOR
    assert _severity_from_scale(2) is Severity.MODERATE


def test_severity_from_scale_g4_g5_are_extreme():
    """G4 and G5 are the levels NOAA associates with grid voltage control
    problems and transformer damage: the top severity tier."""
    assert _severity_from_scale(4) is Severity.EXTREME
    assert _severity_from_scale(5) is Severity.EXTREME


def test_severity_from_scale_g3_is_severe_not_extreme():
    """Deliberately conservative: G3 ("strong") sits below the
    transformer-damaging tier."""
    assert _severity_from_scale(3) is Severity.SEVERE


def test_severity_from_scale_clamps_out_of_range():
    assert _severity_from_scale(-1) is Severity.INFO
    assert _severity_from_scale(9) is Severity.EXTREME


# -------------------------------------------------------------------- _classify_alert


def test_classify_alert_explicit_g_scale():
    result = _classify_alert(ALERT_WARK05["message"])
    assert result == (Severity.MINOR, "G1 (minor) geomagnetic storm")


def test_classify_alert_explicit_s_scale():
    result = _classify_alert(ALERT_WARPX1["message"])
    assert result == (Severity.MINOR, "S1 (minor) radiation storm")


def test_classify_alert_day_list_takes_the_peak_day():
    result = _classify_alert(ALERT_WATA20_WATCH["message"])
    assert result == (Severity.MINOR, "G1 (minor) geomagnetic storm")


def test_classify_alert_cancel_watch_with_no_level_is_none():
    """Every day in a CANCEL WATCH reads "None (Bellow G1)" [sic]: nothing
    is anticipated any more, so there is nothing to ingest."""
    assert _classify_alert(ALERT_WATA20_CANCEL["message"]) is None


def test_classify_alert_kindex_only_falls_back_to_kp_mapping():
    """WARK04 has no "Noaa Scale" line at all -- K-index 4 is below the G1
    storm threshold, so it maps to INFO via the Kp conversion."""
    result = _classify_alert(ALERT_WARK04_EXTENDED["message"])
    assert result == (Severity.INFO, "K-index 4 geomagnetic activity")


def test_classify_alert_ungraded_defaults_to_minor():
    """A Sudden Impulse warning reports a shock detected at L1 with no
    numeric scale at all -- kept, conservatively, at MINOR."""
    result = _classify_alert(ALERT_WARSUD["message"])
    assert result == (Severity.MINOR, "geomagnetic disturbance")


# ---------------------------------------------------------- _parse_valid_until


def test_parse_valid_until_reads_valid_to():
    valid_until = _parse_valid_until(ALERT_WARK05["message"])
    assert valid_until == datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def test_parse_valid_until_reads_now_valid_until():
    """An EXTENDED WARNING carries "Now Valid Until" instead of "Valid To"."""
    valid_until = _parse_valid_until(ALERT_WARK04_EXTENDED["message"])
    assert valid_until == datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


def test_parse_valid_until_absent_is_none():
    assert _parse_valid_until(ALERT_WATA20_WATCH["message"]) is None


# ------------------------------------------------------------ _implied_valid_until


def test_implied_valid_until_uses_last_day_in_the_list():
    issue_time = datetime(2026, 8, 17, 22, 46, 24, tzinfo=UTC)
    valid_until = _implied_valid_until(ALERT_WATA20_WATCH["message"], issue_time)
    assert valid_until == datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC)


def test_implied_valid_until_rolls_into_next_year():
    """A watch issued in December whose window reaches January must not be
    interpreted as a trip backward in time."""
    issue_time = datetime(2026, 12, 30, 6, 0, tzinfo=UTC)
    message = (
        "Highest Storm Level Predicted by Day:\nDec 30:  None (Below G1)   Jan 01:  G1 (Minor)   "
    )
    valid_until = _implied_valid_until(message, issue_time)
    assert valid_until == datetime(2027, 1, 1, 23, 59, 59, tzinfo=UTC)


def test_implied_valid_until_no_day_list_is_none():
    assert _implied_valid_until(ALERT_WARK05["message"], datetime(2026, 8, 18, tzinfo=UTC)) is None


# ---------------------------------------------------------------- parse_alerts


def test_parse_alerts_drops_alt_and_sum():
    """ALT (already-measured alert) and SUM (summary) describe something
    that already happened -- ingesting them as forecasts would be the
    freshness lie this product forbids."""
    events = SpaceWeatherSource().parse_alerts([ALERT_ALTK05, ALERT_SUMSUD])
    assert events == []


def test_parse_alerts_keeps_watch_and_warning():
    events = SpaceWeatherSource().parse_alerts(
        [ALERT_WARK05, ALERT_WATA20_WATCH], now=datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    )
    assert len(events) == 2
    assert {e.id for e in events} == {"space:alert:K05W:2259", "space:alert:A20F:1121"}


def test_parse_alerts_skips_cancel_watch():
    events = SpaceWeatherSource().parse_alerts([ALERT_WATA20_CANCEL])
    assert events == []


def test_parse_alert_event_shape():
    now = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    event = SpaceWeatherSource().parse_alerts([ALERT_WARK05], now=now)[0]
    assert event.id == "space:alert:K05W:2259"
    assert event.source == "space"
    assert event.kind is Kind.SPACE_WEATHER
    # a geomagnetic storm concerns a latitude band, not a point
    assert event.lat is None
    assert event.lon is None
    assert event.severity is Severity.MINOR
    assert event.alert == "WARK05"
    assert event.title == "Geomagnetic warning: G1 (minor) geomagnetic storm"
    assert event.time == datetime(2026, 8, 18, 2, 27, 0, 810000, tzinfo=UTC)


def test_parse_alert_ongoing_while_valid():
    before_expiry = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    event = SpaceWeatherSource().parse_alerts([ALERT_WARK05], now=before_expiry)[0]
    assert event.ongoing is True


def test_parse_alert_not_ongoing_once_expired():
    after_expiry = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)
    event = SpaceWeatherSource().parse_alerts([ALERT_WARK05], now=after_expiry)[0]
    assert event.ongoing is False


def test_parse_alert_watch_ongoing_uses_implied_window():
    within_window = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)
    past_window = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
    events_within = SpaceWeatherSource().parse_alerts([ALERT_WATA20_WATCH], now=within_window)
    events_past = SpaceWeatherSource().parse_alerts([ALERT_WATA20_WATCH], now=past_window)
    assert events_within[0].ongoing is True
    assert events_past[0].ongoing is False


def test_parse_alerts_skips_item_without_message_code():
    assert SpaceWeatherSource().parse_alerts([{"product_id": "X", "message": "garbage"}]) == []


def test_parse_alerts_skips_item_without_issue_datetime():
    broken = {**ALERT_WARK05, "issue_datetime": None}
    assert SpaceWeatherSource().parse_alerts([broken]) == []


def test_parse_alerts_empty_and_none_input():
    assert SpaceWeatherSource().parse_alerts([]) == []
    assert SpaceWeatherSource().parse_alerts(None) == []


def test_parse_alerts_serial_missing_falls_back_to_issue_time():
    message = ALERT_WARK05["message"].replace("Serial Number: 2259\r\n", "")
    no_serial = {**ALERT_WARK05, "message": message}
    event = SpaceWeatherSource().parse_alerts([no_serial])[0]
    assert event.id == "space:alert:K05W:2026-08-18T02:27:00.810000+00:00"


# ---------------------------------------------------------------- parse_scales


def _day(date: str, time: str, g_scale: str, g_text: str) -> dict:
    """Minimal `noaa-scales.json` day entry -- only the fields parse_scales reads."""
    return {"DateStamp": date, "TimeStamp": time, "G": {"Scale": g_scale, "Text": g_text}}


def test_parse_scales_real_fixture():
    events = SpaceWeatherSource().parse_scales(SCALES_PAYLOAD)
    assert len(events) == 1
    event = events[0]
    assert event.id == "space:outlook"
    assert event.kind is Kind.SPACE_WEATHER
    assert event.lat is None
    assert event.lon is None
    assert event.severity is Severity.MINOR  # peak forecast G1
    assert event.ongoing is True
    assert event.title == "Geomagnetic storm watch: G1 (minor) expected 2026-08-18"
    # day "0" (current) provides the event's timestamp
    assert event.time == datetime(2026, 8, 18, 10, 32, tzinfo=UTC)


def test_parse_scales_forecast_days_exclude_current_and_yesterday():
    """Only days "1".."3" feed the outlook; day "0" (current, already
    happening) and "-1" (yesterday, fully observed) are context only."""
    event = SpaceWeatherSource().parse_scales(SCALES_PAYLOAD)[0]
    dates = [d["date"] for d in event.raw["forecast_days"]]
    assert dates == ["2026-08-18", "2026-08-19", "2026-08-20"]
    assert event.raw["current"]["g_scale"] == 0
    assert event.raw["observed_yesterday"]["g_scale"] == 1


def test_parse_scales_no_storm_forecast_returns_nothing():
    """When the whole 3-day window sits at G0, nothing is anticipated: an
    event that only ever says "nothing expected" is noise, not a forecast."""
    quiet = {
        "0": _day("2026-08-18", "10:32:00", "0", "none"),
        "1": _day("2026-08-19", "00:00:00", "0", "none"),
        "2": _day("2026-08-20", "00:00:00", "0", "none"),
        "3": _day("2026-08-21", "00:00:00", "0", "none"),
    }
    assert SpaceWeatherSource().parse_scales(quiet) == []


def test_parse_scales_picks_the_highest_forecast_day():
    escalating = {
        "0": _day("2026-08-18", "10:32:00", "0", "none"),
        "1": _day("2026-08-19", "00:00:00", "1", "minor"),
        "2": _day("2026-08-20", "00:00:00", "4", "severe"),
        "3": _day("2026-08-21", "00:00:00", "2", "moderate"),
    }
    event = SpaceWeatherSource().parse_scales(escalating)[0]
    assert event.severity is Severity.EXTREME
    assert "2026-08-20" in event.title


def test_parse_scales_missing_days_is_defensive():
    assert SpaceWeatherSource().parse_scales({}) == []
    assert SpaceWeatherSource().parse_scales(None) == []


def test_parse_scales_falls_back_to_now_without_day_zero():
    fallback_now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    payload = {"1": _day("2026-08-19", "00:00:00", "1", "minor")}
    event = SpaceWeatherSource().parse_scales(payload, now=fallback_now)[0]
    assert event.time == fallback_now


# ----------------------------------------------------------- parse_kp_forecast


def test_parse_kp_forecast_only_counts_predicted():
    summary = SpaceWeatherSource().parse_kp_forecast(KP_FORECAST_ROWS)
    assert summary is not None
    assert summary["predicted_points"] == 2  # KP_PREDICTED_PEAK + KP_PREDICTED_LOWER


def test_parse_kp_forecast_excludes_estimated():
    """`estimated` is a same-day nowcast off real-time magnetometers, not a
    forecast of the future -- conservatively excluded like `observed`."""
    only_estimated = [KP_OBSERVED_ROW, KP_ESTIMATED_ROW]
    assert SpaceWeatherSource().parse_kp_forecast(only_estimated) is None


def test_parse_kp_forecast_picks_the_peak():
    summary = SpaceWeatherSource().parse_kp_forecast(KP_FORECAST_ROWS)
    assert summary["peak_kp"] == 4.67
    assert summary["peak_time"] == "2026-08-19T03:00:00"
    assert summary["implied_g"] == 0  # 4.67 is below the Kp-5 storm threshold


def test_parse_kp_forecast_no_predicted_rows_is_none():
    assert SpaceWeatherSource().parse_kp_forecast([KP_OBSERVED_ROW]) is None


def test_parse_kp_forecast_non_list_input_is_none():
    assert SpaceWeatherSource().parse_kp_forecast(None) is None
    assert SpaceWeatherSource().parse_kp_forecast({}) is None


# -------------------------------------------------------------------- _poll_once


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_poll_once_combines_all_three_products():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == SpaceWeatherSource.url:
            return httpx.Response(200, json=SCALES_PAYLOAD)
        if url == SpaceWeatherSource.kp_url:
            return httpx.Response(200, json=KP_FORECAST_ROWS)
        if url == SpaceWeatherSource.alerts_url:
            return httpx.Response(200, json=[ALERT_WARK05, ALERT_ALTK05])
        return httpx.Response(404, json={})

    source = SpaceWeatherSource()
    async with _mock_client(handler) as client:
        events = await source._poll_once(client)

    # one outlook event (from scales.json) + one kept alert (WARK05; ALTK05 dropped)
    assert len(events) == 2
    outlook = next(e for e in events if e.id == "space:outlook")
    assert "kp_forecast" in outlook.raw
    assert outlook.raw["kp_forecast"]["peak_kp"] == 4.67
    assert any(e.id == "space:alert:K05W:2259" for e in events)
    assert not any(e.raw.get("message_code") == "ALTK05" for e in events)


@pytest.mark.asyncio
async def test_poll_once_kp_failure_does_not_drop_the_outlook():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == SpaceWeatherSource.url:
            return httpx.Response(200, json=SCALES_PAYLOAD)
        if url == SpaceWeatherSource.kp_url:
            raise httpx.ConnectError("connection refused", request=request)
        if url == SpaceWeatherSource.alerts_url:
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={})

    source = SpaceWeatherSource()
    async with _mock_client(handler) as client:
        events = await source._poll_once(client)

    assert len(events) == 1
    assert events[0].id == "space:outlook"
    assert "kp_forecast" not in events[0].raw


@pytest.mark.asyncio
async def test_poll_once_alerts_failure_does_not_drop_the_outlook():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == SpaceWeatherSource.url:
            return httpx.Response(200, json=SCALES_PAYLOAD)
        if url == SpaceWeatherSource.kp_url:
            return httpx.Response(200, json=[])
        if url == SpaceWeatherSource.alerts_url:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(404, json={})

    source = SpaceWeatherSource()
    async with _mock_client(handler) as client:
        events = await source._poll_once(client)

    assert len(events) == 1
    assert events[0].id == "space:outlook"
