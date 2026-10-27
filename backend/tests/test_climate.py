"""Live climate indicators. Fixtures in `fixtures/climate/` are verbatim line
excerpts of the real files captured on 2026-10-27 (headers kept; only the
rows each parser needs), plus the whole ENSO discussion page."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from app.climate import (
    ClimateMonitor,
    ClimateParseError,
    build_indicators,
    parse_co2_daily,
    parse_enso_discussion,
    parse_nino_weekly,
    parse_nsidc,
    parse_pulse,
)

FX = Path(__file__).parent / "fixtures" / "climate"


def read(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


def test_global_air_temperature_carries_its_own_date_and_status():
    r = parse_pulse(read("pulse_2t.csv"))
    assert r.as_of == date(2026, 10, 25)
    assert r.value == 15.054
    assert r.anomaly == 0.949
    assert r.baseline == {
        "kind": "day_1991_2020",
        "label": "1991-2020 average for this day",
        "value": 14.106,
    }
    assert r.previous == {"as_of": "2026-10-24", "value": 15.167, "anomaly": 1.024}
    # the last day is PRELIMINARY in the file, and says so here
    assert r.status == "preliminary"


def test_sea_surface_temperature():
    r = parse_pulse(read("pulse_sst.csv"))
    assert (r.as_of, r.value, r.anomaly, r.status) == (date(2026, 10, 25), 20.902, 0.733, "final")


def test_co2_compares_with_the_same_day_a_year_earlier_from_the_file_itself():
    r = parse_co2_daily(read("co2_daily_mlo.csv"))
    assert (r.as_of, r.value, r.unit) == (date(2026, 10, 25), 427.13, "ppm")
    assert r.baseline is not None and r.baseline["as_of"] == "2025-10-25"
    assert r.anomaly == round(427.13 - r.baseline["value"], 2)


def test_sea_ice_baseline_is_the_1991_2020_mean_of_the_same_day():
    north = parse_nsidc(read("nsidc_N.csv"))
    assert north.as_of == date(2026, 10, 17)
    assert north.value == 6.717
    assert north.baseline is not None and north.baseline["years"] == 30
    assert north.anomaly is not None and north.anomaly < 0
    south = parse_nsidc(read("nsidc_S.csv"))
    # missing coverage is carried, because the extent is then a lower bound
    assert south.extra["missing_million_km2"] == 0.382


def test_nino34_reads_the_glued_negative_columns():
    r = parse_nino_weekly(read("wksst9120.for"))
    assert (r.as_of, r.value, r.anomaly) == (date(2026, 10, 21), 30.1, 3.4)
    assert r.previous == {"as_of": "2026-10-14", "value": 30.0, "anomaly": 3.3}


def test_enso_status_and_issue_date_come_from_the_discussion():
    r = parse_enso_discussion(read("ensodisc.html"))
    assert r.value == "El Niño Advisory"
    assert r.as_of == date(2026, 10, 8)
    assert r.extra["next_issue"] == "2026-11-12"


def test_the_enso_synopsis_is_the_synopsis_not_the_whole_discussion():
    """PR #5 review: it ran 2545 characters, through every paragraph and the
    e-mail sign-up line, because the whitespace collapse erased the boundary
    the parser looked for. The page is now read block by block."""
    synopsis = parse_enso_discussion(read("ensodisc.html")).extra["synopsis"]
    assert synopsis == (
        "El Niño continues to strengthen, with a strong-to-very strong El Niño "
        "likely through January-March 2027 (remaining greater than an 83% chance)."
    )
    assert "ncep.list" not in synopsis


def test_missing_coverage_makes_the_extent_a_lower_bound_with_no_trend():
    """2026-10-17 south: 16.204 with 0.382 missing, after 16.225 with none. A
    0.021 drop inside 0.382 of missing area is no direction at all."""
    from app.climate import _lower_bound, _trend

    south = parse_nsidc(read("nsidc_S.csv"))
    assert _lower_bound(south) is True
    assert _trend(south) is None
    north = parse_nsidc(read("nsidc_N.csv"))  # nothing missing
    assert _lower_bound(north) is False
    assert _trend(north) == "down"


def test_every_baseline_carries_a_stable_kind_for_translation():
    kinds = {
        parse_pulse(read("pulse_2t.csv")).baseline["kind"],
        parse_co2_daily(read("co2_daily_mlo.csv")).baseline["kind"],
        parse_nsidc(read("nsidc_N.csv")).baseline["kind"],
        parse_nino_weekly(read("wksst9120.for")).baseline["kind"],
    }
    assert kinds == {"day_1991_2020", "day_year_earlier", "week_1991_2020"}


def test_a_file_that_does_not_say_what_it_should_is_an_error_not_a_value():
    with pytest.raises(ClimateParseError):
        parse_pulse("date,foo,bar\n2026-10-25,1,2\n")
    with pytest.raises(ClimateParseError):
        parse_enso_discussion("<html>maintenance</html>")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_no_value_before_the_first_success_and_the_real_error_is_shown():
    monitor = ClimateMonitor(build_indicators()[:1])

    def handler(request):
        return httpx.Response(503, text="Service Unavailable")

    async with _client(handler) as client:
        await monitor.poll(client)
    (item,) = monitor.snapshot()["indicators"]
    assert item["available"] is False
    assert item["value"] is None and item["as_of"] is None
    assert "503" in item["state"]["last_error"]


@pytest.mark.asyncio
async def test_a_failed_poll_keeps_the_real_value_with_its_dataset_date_and_says_so():
    monitor = ClimateMonitor(build_indicators()[:1])
    body = read("pulse_2t.csv")
    responses = [httpx.Response(200, text=body), httpx.Response(500, text="boom")]

    def handler(request):
        return responses.pop(0)

    async with _client(handler) as client:
        await monitor.poll(client, now=datetime(2026, 10, 27, 6, 0, tzinfo=UTC))
        await monitor.poll(client, now=datetime(2026, 10, 27, 9, 30, tzinfo=UTC))
    (item,) = monitor.snapshot(now=datetime(2026, 10, 27, 9, 31, tzinfo=UTC))["indicators"]
    assert item["available"] is False
    assert item["value"] == 15.054 and item["as_of"] == "2026-10-25"
    assert "500" in item["state"]["last_error"]
    assert item["state"]["last_ok"] is not None


@pytest.mark.asyncio
async def test_stale_when_the_dataset_is_late_by_its_own_standard():
    monitor = ClimateMonitor([i for i in build_indicators() if i.id == "arctic_sea_ice_extent"])

    def handler(request):
        return httpx.Response(200, text=read("nsidc_N.csv"))

    async with _client(handler) as client:
        await monitor.poll(client)
    fresh = monitor.snapshot(now=datetime(2026, 10, 18, 12, 0, tzinfo=UTC))["indicators"][0]
    late = monitor.snapshot(now=datetime(2026, 10, 27, 9, 0, tzinfo=UTC))["indicators"][0]
    assert fresh["stale"] is False
    assert late["stale"] is True  # 10 days behind a dataset that is normally 1 day behind


@pytest.mark.asyncio
async def test_each_indicator_is_polled_at_its_own_cadence():
    monitor = ClimateMonitor(build_indicators())
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(503)

    t0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
    async with _client(handler) as client:
        assert await monitor.poll(client, now=t0) == 7
        # nothing is due a minute later
        assert await monitor.poll(client, now=t0.replace(minute=1)) == 0


def test_the_route_and_the_feature_flag():
    from fastapi.testclient import TestClient

    from app.main import FEATURES, app

    assert "climate" in FEATURES
    body = TestClient(app).get("/api/climate").json()
    assert {i["id"] for i in body["indicators"]} == {
        "global_air_temperature",
        "sea_surface_temperature",
        "co2_mauna_loa",
        "arctic_sea_ice_extent",
        "antarctic_sea_ice_extent",
        "nino34_weekly",
        "enso_status",
    }
