"""Audit 6, lane C -- the sources, measured against the live feeds of 2026-10-26.

Every payload comes from `fixtures/`, copied unmodified from the endpoint it
names (lesson 8). Where a test needs a value the live feed did not happen to
carry (an EEW drill flag set to true), it derives it from the real payload in
one visible line, never from a hand-written one.
"""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.models.event import Kind, Severity
from app.sources import tsunami as tsunami_module
from app.sources.alerts_world import MeteoalarmSource, classify_wmo, parse_meteoalarm, parse_wmo
from app.sources.base import SourceHealth
from app.sources.eew import CencSource, JmaEewSource
from app.sources.emsc_ws import parse_message
from app.sources.gdacs import GdacsSource
from app.sources.jma_tsunami import JmaTsunamiSource
from app.sources.nws import classify
from app.sources.regional import GeofonSource, JmaSource
from app.sources.space import SpaceWeatherSource
from app.sources.tsunami import TsunamiSource

FIXTURES = Path(__file__).parent / "fixtures"
REAL = json.loads((FIXTURES / "audit6_2026-10-26.json").read_text(encoding="utf-8"))
CAPTURED = datetime(2026, 10, 26, 19, 0, tzinfo=UTC)


# ------------------------------------------------------------- the depth rule
#
# One M6.5-7.0 at 470-501 km under the Bonin Islands, 2026-10-25, as four
# sources published it. Rated on magnitude alone it was SEVERE (EMSC, GEOFON,
# CENC) and EXTREME (JMA, with the major banner and the sound) -- for a quake
# whose strongest shaking anywhere was shindo 3. `severity_for_quake` caps a
# deep-focus quake at MODERATE whatever its magnitude.


def test_emsc_rates_a_deep_quake_by_its_depth():
    event = parse_message(REAL["emsc_fdsn_bonin_m65_490km"])
    assert event is not None
    assert event.depth_km == 490.3
    assert event.severity is Severity.MODERATE


def test_jma_rates_a_deep_quake_by_its_depth_and_shindo_still_promotes():
    (event,) = JmaSource().parse_payload([REAL["jma_quake_list_ogasawara_m70_470km"]])
    assert event.magnitude == 7.0
    assert event.depth_km == 470.0
    # shindo 3 does not promote (only 5- and above do), so the depth rule rules
    assert event.alert == "shindo 3"
    assert event.severity is Severity.MODERATE


def test_cenc_rates_a_deep_quake_by_its_depth():
    (event,) = CencSource().parse_payload(REAL["cenc_bonin_m65_480km"])
    assert event.depth_km == 480.0
    assert event.severity is Severity.MODERATE


def test_geofon_rates_a_deep_quake_by_its_depth():
    (event,) = GeofonSource().parse_payload({"features": [REAL["geofon_bonin_m65_501km"]]})
    assert event.depth_km == 501
    assert event.severity is Severity.MODERATE


# ------------------------------------------------------------------- JMA EEW


def test_eew_reads_its_depth():
    (event,) = JmaEewSource().parse_payload(REAL["jma_eew_latest"])
    assert event.depth_km == 50
    assert event.magnitude == 3.7


def test_an_eew_drill_is_never_published():
    drill = {**REAL["jma_eew_latest"], "isTraining": True}
    source = JmaEewSource()
    assert source.parse_payload(drill) == []
    assert source.retractions == []


def test_an_eew_cancelled_by_the_relay_flag_is_withdrawn():
    cancelled = {**REAL["jma_eew_latest"], "isCancel": True}
    source = JmaEewSource()
    assert source.parse_payload(cancelled) == []
    assert source.retractions == ["jma_eew:20261026131230"]


# ------------------------------------------------- alerts that ran their course


def test_the_real_jma_forecast_of_october_10_is_withdrawn_on_october_26():
    row = REAL["jma_tsunami_row_2026-10-10_forecast"]
    source = JmaTsunamiSource()
    assert source.parse_payload([row], now=CAPTURED) == []
    assert source.expirations == ["jma-tsunami:20261010025608"]


def test_the_same_forecast_was_published_while_it_stood():
    row = REAL["jma_tsunami_row_2026-10-10_forecast"]
    issued = datetime(2026, 10, 9, 19, 0, tzinfo=UTC)  # 04:00 JST on the 10th
    (event,) = JmaTsunamiSource().parse_payload([row], now=issued)
    assert event.severity is Severity.INFO
    assert event.expires is not None and event.expires > issued
    # a "slight sea-level change" forecast is not a tsunami alert
    assert event.kind is Kind.TSUNAMI
    assert event.tsunami is False


def test_a_g3_warning_valid_until_october_10_is_withdrawn_on_october_26():
    alert = REAL["swpc_alert_wark07_serial153"]
    source = SpaceWeatherSource()
    assert source.parse_alerts([alert], now=CAPTURED) == []
    assert source.expirations == ["space:alert:K07W:153"]


def test_the_g3_warning_carried_its_end_while_valid():
    alert = REAL["swpc_alert_wark07_serial153"]
    during = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)
    (event,) = SpaceWeatherSource().parse_alerts([alert], now=during)
    assert event.severity is Severity.SEVERE
    assert event.expires == datetime(2026, 10, 10, 23, 59, tzinfo=UTC)


def test_gdacs_withdraws_what_gdacs_itself_closed():
    root = ET.parse(FIXTURES / "gdacs_2026-10-26_closed_drought_and_live_cyclone.xml").getroot()
    source = GdacsSource(max_age_days=1.0)
    events = source.select(root.findall(".//item"))
    # the red cyclone ARNAB-26 is current and stays; the orange drought open
    # since December 2025 and closed on 2026-10-24 does not
    assert [e.id for e in events] == ["gdacs:TC1001345"]
    assert source.expirations == ["gdacs:DR1018332"]


def test_meteoalarm_carries_the_warning_end():
    warning = REAL["meteoalarm_germany_expired_orange"]
    event = parse_meteoalarm(warning, "germany")
    assert event is not None
    assert event.expires is not None
    assert event.expires < CAPTURED


def test_meteoalarm_withdraws_a_warning_that_has_ended():
    """Storm gusts of 2026-10-21/22, still listed by Meteoalarm on the 26th:
    241 of the 276 orange-and-red warnings it served that day were over."""
    warning = REAL["meteoalarm_germany_expired_orange"]
    source = MeteoalarmSource(min_level=3)
    assert source.select({"warnings": [warning]}, "germany", now=CAPTURED) == []
    assert source.expirations == [f"meteoalarm:{warning['alert']['identifier']}"]

    onset = parse_meteoalarm(warning, "germany").time
    (kept,) = MeteoalarmSource(min_level=3).select({"warnings": [warning]}, "germany", now=onset)
    assert kept.ongoing is True


def test_meteoalarm_entities_are_decoded_in_the_labels():
    event = parse_meteoalarm(REAL["meteoalarm_greece_amp"], "greece")
    assert event is not None
    assert event.place == "East Sterea & Evvoia"
    assert event.title == "Yellow Thunderstorm for East Sterea & Evvoia"
    assert "&amp;" not in event.title


# ------------------------------------------------------------- classification

# Every one of these is a real NWS event name (api.weather.gov/alerts/types).
NWS_REAL_NAMES = {
    "Storm Warning": Kind.STORM,
    "Storm Watch": Kind.STORM,
    "Dust Storm Warning": Kind.STORM,
    "Hurricane Force Wind Warning": Kind.STORM,
    "Hurricane Force Wind Watch": Kind.STORM,
    "Avalanche Warning": Kind.AVALANCHE,
    # unchanged, and must stay so
    "Hurricane Warning": Kind.CYCLONE,
    "Tropical Storm Warning": Kind.CYCLONE,
    "Storm Surge Warning": Kind.CYCLONE,
    "Winter Storm Warning": Kind.STORM,
    "Flash Flood Warning": Kind.FLOOD,
    "Small Craft Advisory": Kind.OTHER,
}


@pytest.mark.parametrize(("name", "kind"), NWS_REAL_NAMES.items())
def test_nws_names(name: str, kind: Kind):
    assert classify(name) is kind


# Real `event` values of the WMO aggregate.
WMO_REAL_NAMES = {
    "Landslide warning": Kind.LANDSLIDE,
    "Meteorological risk of geological disaster": Kind.LANDSLIDE,
    "Inundación urbana": Kind.FLOOD,
    "lluvia tormenta eléctrica": Kind.STORM,
    "Hujan Lebat dan Petir": Kind.STORM,
    "ORAGE": Kind.STORM,
    "Tempête de poussière/tempête de sable": Kind.STORM,
    "Altas Temperaturas": Kind.HEAT,
    "Hurricane Force Wind Warning": Kind.STORM,
    # must not move
    "Orange Warning": Kind.OTHER,
    "Low temperature": Kind.OTHER,
    "Forestfire": Kind.WILDFIRE,
}


@pytest.mark.parametrize(("name", "kind"), WMO_REAL_NAMES.items())
def test_wmo_names(name: str, kind: Kind):
    assert classify_wmo(name) is kind


def test_a_placeholder_headline_gives_way_to_the_event_name():
    event = parse_wmo(REAL["wmo_co_landslide_cap_alert"])
    assert event is not None
    assert event.title == "Landslide warning"
    assert event.kind is Kind.LANDSLIDE


def test_a_real_headline_is_kept():
    event = parse_wmo(REAL["wmo_us_hurricane_force_wind"])
    assert event is not None
    assert event.title.startswith("Hurricane Force Wind Warning issued October 26")
    assert event.kind is Kind.STORM


# --------------------------------------------------------------------- health


def test_a_partial_success_does_not_erase_a_partial_failure():
    health = SourceHealth("tsunami")
    health.feed_failed("PHEB", ConnectionError("down"))
    health.feed_ok("PAAQ")
    health.ok(3)
    snap = health.snapshot()
    assert snap["connected"] is True
    assert snap["degraded"] is True
    assert snap["failed_feeds"] == [{"feed": "PHEB", "error": "ConnectionError: down"}]
    assert "PHEB" in snap["last_error"]

    health.feed_ok("PHEB")
    health.ok(1)
    snap = health.snapshot()
    assert snap["degraded"] is False
    assert snap["last_error"] is None


def test_connected_is_not_delivering():
    health = SourceHealth("emsc")
    health.ok()  # the websocket opened
    assert health.snapshot()["connected"] is True
    assert health.snapshot()["last_event_at"] is None
    health.observe("emsc:1", datetime(2026, 10, 26, 12, 0, tzinfo=UTC))  # the first quake
    health.ok(1)
    assert health.snapshot()["last_event_at"] is not None


def test_a_feed_no_longer_polled_stops_failing():
    health = SourceHealth("phivolcs")
    health.feed_failed("September", TimeoutError("slow"))
    health.keep_feeds({"latest", "October"})
    assert health.failed_feeds == {}


@pytest.mark.asyncio
async def test_one_dead_tsunami_centre_is_degraded_not_green(monkeypatch):
    atom = (FIXTURES / "tsunami_PAAQAtom_2026-10-26.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("PAAQAtom.xml"):
            return httpx.Response(200, content=atom)
        if request.url.path.endswith("PHEBAtom.xml"):
            return httpx.Response(503)
        return httpx.Response(404)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(tsunami_module.httpx, "AsyncClient", client)
    monkeypatch.setattr(tsunami_module.asyncio, "sleep", stop)

    emitted = []

    async def emit(event):
        emitted.append(event)

    source = TsunamiSource()
    with pytest.raises(asyncio.CancelledError):
        await source.run(emit)

    snap = source.health.snapshot()
    assert emitted, "the live centre's bulletin still goes out"
    assert snap["connected"] is True
    assert snap["degraded"] is True
    assert [f["feed"] for f in snap["failed_feeds"]] == ["PHEB"]
    assert "PHEB" in snap["last_error"]
