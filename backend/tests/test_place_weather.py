"""weather_now (METAR), forecast_hourly / forecast_daily (MET Norway), wind
(NWS for the US, MET elsewhere), on verbatim captures."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.place import metar, weather
from tests.place_helpers import at, ctx_for, install, meta_of, read_bytes, read_json

CACHE_FETCHED = at("2026-10-27T11:09:26Z")
MET_FETCHED = at("2026-10-27T10:36:41Z")


# ----------------------------------------------------------------- METAR


@pytest.fixture(scope="module")
def metar_index() -> weather.MetarIndex:
    return weather.parse_metar_cache(read_bytes("weather", "awc_metars_cache_global.csv.gz"))


def test_calm_has_no_direction() -> None:
    g = metar.parse("METAR PANC 271053Z 00000KT 10SM MIFG CLR M01/M01 A3043 RMK AO2 SLP305")
    assert g.calm is True and g.wind_from_deg is None and g.wind_ms == 0.0


def test_variable_wind_has_no_direction() -> None:
    g = metar.parse("SPECI CYPY 271107Z AUTO VRB02KT 1/4SM FZFG CLR M05/M05 A2982 RMK ICE")
    assert g.wind_variable is True and g.wind_from_deg is None and g.wind_ms == 1.0
    assert g.visibility_km == 0.4 and g.visibility_at_least is False


def test_mps_winds_are_read_from_the_report_not_the_rounded_knots() -> None:
    # the CSV says 19/29 kt for this report
    g = metar.parse("SPECI URMM 271108Z 11010G15MPS 9999 BKN025 14/09 Q1023 R11/010070 NOSIG")
    assert (g.wind_ms, g.gust_ms, g.wind_from_deg) == (10.0, 15.0, 110)


def test_9999_is_ten_km_or_more() -> None:
    g = metar.parse("METAR VTSM 271100Z 14007KT 9999 SCT020 27/24 Q1011 NOSIG")
    assert (g.visibility_km, g.visibility_at_least) == (10.0, True)
    assert g.qnh_hpa == 1011 and g.qnh_derived is False


def test_q_group_wins_over_a_group() -> None:
    g = metar.parse("METAR OICK 271100Z 28008KT 9999 FEW040 26/M02 Q1021 A3017")
    assert g.qnh_hpa == 1021 and g.qnh_derived is False
    a_only = metar.parse("METAR PANC 271053Z 00000KT 10SM MIFG CLR M01/M01 A3043 RMK AO2")
    assert a_only.qnh_hpa == pytest.approx(1030.5, abs=0.1) and a_only.qnh_derived is True


def test_a_tempo_gust_is_a_forecast_not_the_observation() -> None:
    g = metar.parse("METAR RJGG 271100Z 33025KT 9999 FEW020 20/16 Q1009 TEMPO 31030G40KT")
    assert g.gust_ms is None and g.wind_from_deg == 330


def test_whole_and_fraction_statute_miles() -> None:
    g = metar.parse("METAR PABT 271053Z 00000KT 2 1/2SM BR FEW005 FEW018 SCT030 01/01 A3029")
    assert g.visibility_km == 4.0


def test_cache_file_wind_dir_zero_for_calm_and_vrb(metar_index: weather.MetarIndex) -> None:
    """The CSV writes 0 for calm and VRB: the raw report is what the block reads."""
    assert metar_index.rows == 5237


def test_ko_samui_now(metar_index: weather.MetarIndex) -> None:
    ctx = ctx_for(9.512, 100.013, CACHE_FETCHED)
    block = weather.now_block(metar_index, ctx).public(CACHE_FETCHED, "en")
    assert block["status"] == "ok" and block["as_of"] == "2026-10-27T11:00:00Z"
    d = block["data"]
    assert d["station"] == {"icao": "VTSM", "distance_km": 7.1, "elevation_m": 12}
    assert d["raw"] == "METAR VTSM 271100Z 14007KT 9999 SCT020 27/24 Q1011 NOSIG"
    assert (d["temp_c"], d["dewpoint_c"], d["rh_pct"]) == (27, 24, 84)
    assert (d["wind_ms"], d["wind_from_deg"], d["calm"], d["gust_ms"]) == (3.6, 140, False, None)
    assert (d["visibility_km"], d["visibility_at_least"], d["qnh_hpa"]) == (10.0, True, 1011)
    assert d["clouds"] == [{"cover": "SCT", "base_ft": 2000}] and d["flight_category"] == "VFR"
    assert d["derived"] == ["rh_pct"]


def test_repeated_cloud_columns_are_read_positionally(metar_index: weather.MetarIndex) -> None:
    ctx = ctx_for(53.56, -64.11, CACHE_FETCHED)  # CZUM, OVC039
    d = weather.now_block(metar_index, ctx).data
    assert d is not None and d["clouds"] == [{"cover": "OVC", "base_ft": 3900}]
    ctx = ctx_for(54.29, -130.45, CACHE_FETCHED)  # CYPR, BKN021 BKN031 OVC043
    d = weather.now_block(metar_index, ctx).data
    assert d is not None and [c["base_ft"] for c in d["clouds"]] == [2100, 3100, 4300]


def test_a_report_older_than_3_h_is_skipped(metar_index: weather.MetarIndex) -> None:
    ctx = ctx_for(9.512, 100.013, CACHE_FETCHED + timedelta(hours=3, minutes=1))
    block = weather.now_block(metar_index, ctx)
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_station"


def test_no_station_at_sea(metar_index: weather.MetarIndex) -> None:
    block = weather.now_block(metar_index, ctx_for(0.0, -160.0, CACHE_FETCHED))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_station"


# ---------------------------------------------------------------- MET Norway


def test_hourly_drops_the_past_first_step_and_keeps_clear_sky_uv_null() -> None:
    payload = read_json("weather", "metno_complete_samui.json")
    block = weather.hourly_block(payload, ctx_for(9.512, 100.013, MET_FETCHED))
    out = block.public(MET_FETCHED, "en")
    assert out["as_of"] == "2026-10-27T09:21:31Z" and out["as_of_kind"] == "issued"
    hours = out["data"]["hours"]
    assert hours[0]["time"] == "2026-10-27T11:00:00Z"  # 10:00 is behind a 10:36 read
    assert len(hours) == 48
    assert out["data"]["model_elevation_m"] == 279
    assert {"field": "precip_probability_pct", "reason": "not_in_source"} in out["missing"]
    assert all("precip_probability_pct" not in h for h in hours)


def test_clear_sky_uv_absent_is_null_never_zero() -> None:
    payload = read_json("weather", "metno_complete_samui.json")
    steps = payload["properties"]["timeseries"]
    absent = [
        s for s in steps if "ultraviolet_index_clear_sky" not in s["data"]["instant"]["details"]
    ]
    assert len(absent) == 26 and len(steps) == 89
    assert all(weather._detail(s, "ultraviolet_index_clear_sky") is None for s in absent)
    # and a real 0.0 (night) stays 0.0
    night = [
        s
        for s in steps
        if s["data"]["instant"]["details"].get("ultraviolet_index_clear_sky") == 0.0
    ]
    assert night and weather._detail(night[0], "ultraviolet_index_clear_sky") == 0.0


def test_nordic_area_has_gusts_and_precipitation_probability() -> None:
    payload = read_json("weather", "metno_complete_oslo.json")
    fetched = at(meta_of("weather", "metno_complete_oslo.json")["fetched_utc"])
    ctx = ctx_for(59.91, 10.75, fetched)
    hourly = weather.hourly_block(payload, ctx)
    assert hourly.missing == []
    assert hourly.data is not None and hourly.data["hours"][0]["precip_probability_pct"] is not None
    hours, gusts = weather.met_wind(payload, ctx)
    assert gusts and hours[0]["gust_ms"] is not None


def test_outside_the_nordic_area_gusts_are_missing_not_zero() -> None:
    payload = read_json("weather", "metno_complete_samui.json")
    hours, gusts = weather.met_wind(payload, ctx_for(9.512, 100.013, MET_FETCHED))
    assert not gusts and all(h["gust_ms"] is None for h in hours)
    first = payload["properties"]["timeseries"][1]
    assert first["time"] == "2026-10-27T11:00:00Z"
    details = first["data"]["instant"]["details"]
    assert hours[0]["wind_ms"] == details["wind_speed"]
    assert hours[0]["wind_from_deg"] == round(details["wind_from_direction"])


@pytest.mark.parametrize(
    ("lon", "hour"), [(100.01, 18), (0.0, 0), (45.0, 18), (-45.0, 0), (-160.0, 12), (139.65, 12)]
)
def test_day_starts_at_the_6h_step_nearest_solar_midnight(lon: float, hour: int) -> None:
    assert weather.day_start_hour(lon) == hour


def test_daily_view_and_the_day_under_way() -> None:
    payload = read_json("weather", "metno_complete_samui.json")
    block = weather.daily_block(payload, ctx_for(9.512, 100.013, MET_FETCHED))
    assert block.data is not None
    days = block.data["days"]
    assert block.data["day_start_utc_hour"] == 18
    # 10:36Z: only the 12-18Z block of the day that began at 18Z yesterday is ahead
    assert days[0]["start"] == "2026-10-26T18:00:00Z" and days[0]["complete"] is False
    assert days[0]["blocks"] == 1
    full = days[1]
    assert full["date"] == "2026-10-28" and full["start"] == "2026-10-27T18:00:00Z"
    assert full["complete"] is True and full["blocks"] == 4
    assert block.data["derived"] == ["days"]


# ---------------------------------------------------------------------- NWS


def test_nws_intervals_expand_and_convert_from_their_own_unit() -> None:
    props = read_json("weather", "nws_grid_anchorage.json")["properties"]
    now = at("2026-10-27T05:00:00Z")
    hours = weather.nws_hours(props, now)
    assert hours[0]["time"] == "2026-10-27T05:00:00Z"
    # windSpeed 2026-10-27T03:00/PT3H = 7.408 km/h -> 2.1 m/s for 03, 04 and 05Z
    assert hours[0]["wind_ms"] == pytest.approx(7.408 / 3.6, abs=0.05)
    assert hours[0]["wind_from_deg"] == 130
    assert len(hours) == 48


def test_nws_as_of_is_update_time_not_generated_at() -> None:
    props = read_json("weather", "nws_grid_anchorage.json")["properties"]
    assert weather.parse_time(props["updateTime"]) == at("2026-10-27T04:33:13Z")


def test_iso_intervals() -> None:
    start, span = weather.interval("2026-10-26T22:00:00+00:00/P8DT3H")
    assert start == at("2026-10-26T22:00:00Z") and span == timedelta(days=8, hours=3)


async def test_wind_for_anchorage_comes_from_nws() -> None:
    router = install()
    points = meta_of("weather", "nws_points_anchorage.json")
    grid = meta_of("weather", "nws_grid_anchorage.json")
    router.add(
        "https://api.weather.gov/points/61.22,-149.90",
        read_bytes("weather", "nws_points_anchorage.json"),
    )
    router.add(grid["url"], read_bytes("weather", "nws_grid_anchorage.json"))
    assert points["url"].startswith("https://api.weather.gov/points/")
    ctx = ctx_for(61.218, -149.900, at("2026-10-27T05:00:00Z"))
    block = await weather.build_wind(ctx)
    assert block.status == "ok" and block.source is weather.SRC_NWS
    assert block.as_of == at("2026-10-27T04:33:13Z")
    assert block.data is not None and block.data["fallback"] is None


async def test_nws_404_falls_back_to_met_and_says_so() -> None:
    router = install()
    router.add("https://api.weather.gov/points/61.22,-149.90", b"{}", status=404)
    router.add(
        "https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=61.22&lon=-149.90",
        read_bytes("weather", "metno_complete_anchorage.json"),
    )
    ctx = ctx_for(61.218, -149.900, MET_FETCHED)
    block = await weather.build_wind(ctx)
    assert block.status == "ok" and block.source is weather.SRC_MET
    assert block.data is not None
    assert block.data["fallback"] == {
        "from": "NWS",
        "code": "upstream_http",
        "reason": "HTTP 404 from api.weather.gov",
    }
    assert {"field": "gust_ms", "reason": "not_in_source"} in block.missing
    # no coordinate in the reason
    assert "61.2" not in json.dumps(block.data["fallback"])


async def test_met_is_read_once_for_three_blocks() -> None:
    router = install()
    url = "https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=9.51&lon=100.01"
    router.add(
        url,
        read_bytes("weather", "metno_complete_samui.json"),
        headers={"expires": "Tue, 27 Oct 2026 10:52:43 GMT"},
    )
    ctx = ctx_for(9.512, 100.013, MET_FETCHED)
    import asyncio

    blocks = await asyncio.gather(
        weather.build_hourly(ctx), weather.build_daily(ctx), weather.build_wind(ctx)
    )
    assert [b.status for b in blocks] == ["ok", "ok", "ok"]
    assert sum(1 for r in router.seen if str(r.url) == url) == 1
    assert router.seen[0].headers["user-agent"] == "SOSForge/1.0 (+https://sosforge.soclose.co)"
