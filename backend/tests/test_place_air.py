"""air (CAMS global) and pollen (CAMS Europe) from the Open-Meteo bucket,
replayed from cassettes; values checked against Open-Meteo's own API body
for the same cell and hour (a rejected source, read here as an offline
oracle only); the AQI tables against the documents they were transcribed
from."""

from __future__ import annotations

import csv
import html
import io
import re
from datetime import timedelta

import httpx
import pytest

from app.place import air, compose, fetch, om
from tests.place_helpers import FIXTURES, at, ctx_for, install, read_json

KOSAMUI = FIXTURES / "air" / "openmeteo_s3_cams_global_kosamui"
PARIS = FIXTURES / "air" / "openmeteo_s3_cams_europe_paris"
RECORDED = at("2026-10-27T10:40:29Z")


def _oracle(name: str, hour: str) -> dict:
    payload = read_json("air", name)
    h = payload["hourly"]
    i = h["time"].index(hour)
    return {k: v[i] for k, v in h.items()}


async def test_ko_samui_values_equal_the_open_meteo_oracle() -> None:
    router = install()
    router.cassette(KOSAMUI)
    ctx = ctx_for(9.512, 100.013, RECORDED)
    block = await air.build_air(ctx)
    assert block.status == "ok", block.error
    out = block.public(ctx.now, "en")
    now = out["data"]["now"]
    oracle = _oracle("openmeteo_api_aq_kosamui.json", "2026-10-27T10:00")
    assert now["time"] == "2026-10-27T10:00:00Z"
    assert now["pm2_5_ugm3"] == oracle["pm2_5"] == 6.8
    assert now["pm10_ugm3"] == oracle["pm10"] == 10.6
    assert now["o3_ugm3"] == oracle["ozone"] == 79
    assert now["no2_ugm3"] == oracle["nitrogen_dioxide"] == 0.5
    assert now["so2_ugm3"] == oracle["sulphur_dioxide"] == 0.3
    assert now["uv_index"] == oracle["uv_index"] == 0.3
    assert now["dust_ugm3"] == oracle["dust"] == 0.0  # a real 0 stays 0
    assert out["data"]["cell"] == {"lat": 9.6, "lon": 100.0}
    assert out["as_of"] == "2026-10-27T00:00:00Z" and out["as_of_kind"] == "model_run"
    assert out["data"]["derived"] == ["aqi"]
    assert out["data"]["aqi"]["eu"]["band"] in air.EAQI_BANDS
    assert (
        "Copernicus Atmosphere Monitoring Service information 2026" in out["source"]["attribution"]
    )


async def test_paris_cams_europe_rows_run_north_to_south() -> None:
    router = install()
    router.cassette(PARIS)
    ctx = ctx_for(48.857, 2.352, RECORDED)
    hour = ctx.now.replace(minute=0, second=0, microsecond=0)
    series = await om.series(ctx, "cams_europe", ["pm10"], hour, hour)
    oracle = _oracle("openmeteo_api_aq_paris.json", "2026-10-27T10:00")
    assert round(series.values["pm10"][0] or -1, 1) == oracle["pm10"] == 5.7
    # a south-up reading of the same BBOX would land 4000 km away
    assert abs(series.cell[0] - 48.86) < 0.06


async def test_pollen_replay_at_paris() -> None:
    router = install()
    router.cassette(PARIS)
    block = await air.build_pollen(ctx_for(48.857, 2.352, RECORDED))
    assert block.status == "ok", block.error
    assert block.data is not None
    now = block.data["now"]
    assert set(now) == {"time", "birch_grains_m3", "grass_grains_m3", "ragweed_grains_m3"}
    oracle = read_json("air", "openmeteo_api_aq_paris.json")["current"]
    assert now["birch_grains_m3"] == oracle["birch_pollen"]
    assert now["grass_grains_m3"] == oracle["grass_pollen"]
    assert now["ragweed_grains_m3"] == oracle["ragweed_pollen"]


async def test_a_failed_refresh_serves_the_old_series_cut_to_the_hour_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = install()
    router.cassette(PARIS)
    first = await air.build_pollen(ctx_for(48.857, 2.352, RECORDED))
    assert first.status == "ok" and first.data is not None

    async def refresh_fails(*args: object, **kwargs: object) -> om.Series:
        raise fetch.FetchError("busy", "no slot for openmeteo.s3.amazonaws.com within 15 s")

    monkeypatch.setattr(om, "_series", refresh_fails)
    later = ctx_for(48.857, 2.352, RECORDED + timedelta(hours=20))
    block = await air.build_pollen(later)
    assert block.status == "ok" and block.data is not None and block.error is not None
    # "now" is the row of the hour now, never the old series' first hour
    assert block.data["now"]["time"] == "2026-10-28T06:00:00Z"
    assert block.data["now"] == first.data["hours"][20]
    assert block.data["hours"][0] == first.data["hours"][20]
    assert block.error.code == "busy"
    # past the old window's end: the refresh's own reason, not a ValueError
    gone = await compose._guard(
        air.SPEC_POLLEN, air.build_pollen, ctx_for(48.857, 2.352, RECORDED + timedelta(hours=50))
    )
    assert gone is not None and gone.status == "unavailable" and gone.error is not None
    assert gone.error.code == "busy"


async def test_pollen_outside_the_europe_domain() -> None:
    install()
    block = await air.build_pollen(ctx_for(9.512, 100.013, RECORDED))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_coverage"


async def test_values_past_data_end_time_are_null() -> None:
    router = install()
    cas = router.cassette(KOSAMUI)
    meta = om.parse_meta(read_json("air", "openmeteo_s3_cams_global_meta.json"))
    ctx = ctx_for(9.512, 100.013, RECORDED)
    end = at("2026-11-02T13:00:00Z")  # the last hour of chunk 2295
    assert end.timestamp() > meta.data_end
    series = await om.series(ctx, "cams_global", ["pm2_5"], ctx.now, end)
    values = series.values["pm2_5"]
    tail = [v for t, v in zip(series.times, values, strict=True) if t.timestamp() > meta.data_end]
    assert tail and all(v is None for v in tail)
    assert cas["chunk"] == 2295


async def test_a_chunk_rewritten_mid_read_restarts_once() -> None:
    router = install()
    cas = router.cassette(KOSAMUI)
    data = next(
        e
        for e in cas["exchanges"]
        if e["method"] == "GET"
        and e["url"].endswith("pm2_5/chunk_2295.om")
        and int(e["headers"]["content-length"]) > 500
    )
    body = (KOSAMUI / data["body_file"]).read_bytes()
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["if-match"] == data["headers"]["etag"]
        if calls["n"] == 1:
            return httpx.Response(412)
        return httpx.Response(206, headers=dict(data["headers"]), content=body)

    router.handler(data["url"], flaky, range_=data["range"])
    ctx = ctx_for(9.512, 100.013, RECORDED)
    hour = ctx.now.replace(minute=0, second=0, microsecond=0)
    series = await om.series(ctx, "cams_global", ["pm2_5"], hour, hour)
    assert calls["n"] == 2
    assert round(series.values["pm2_5"][0] or -1, 1) == 6.8


async def test_meta_is_read_again_after_a_cold_read_and_not_after_a_warm_one() -> None:
    router = install()
    router.cassette(KOSAMUI)
    ctx = ctx_for(9.512, 100.013, RECORDED)
    hour = ctx.now.replace(minute=0, second=0, microsecond=0)
    meta_url = "https://openmeteo.s3.amazonaws.com/data/cams_global/static/meta.json"
    await om.series(ctx, "cams_global", ["pm2_5"], hour, hour)
    cold = sum(1 for r in router.seen if str(r.url) == meta_url)
    await om.series(ctx, "cams_global", ["pm2_5"], hour, hour + timedelta(hours=1))
    warm = sum(1 for r in router.seen if str(r.url) == meta_url) - cold
    assert cold == 2 and warm == 0


# ----------------------------------------------------------------- AQI tables


def test_epa_table_is_the_aqs_breakpoint_table() -> None:
    rows = list(
        csv.DictReader(io.StringIO((FIXTURES / "air" / "epa_aqi_breakpoints.csv").read_text()))
    )
    wanted = {
        "pm2_5": ("88101", "24 HOUR"),
        "pm10": ("81102", "24 HOUR"),
        "ozone": ("44201", "8-HR RUN AVG BEGIN HOUR"),
        "ozone_1h": ("44201", "1 HOUR"),
        "nitrogen_dioxide": ("42602", "1 HOUR"),
        "sulphur_dioxide": ("42401", "1 HOUR"),
    }
    names = {
        "GOOD": "good",
        "MODERATE": "moderate",
        "UNHEALTHY FOR SENSITIVE": "usg",
        "UNHEALTHY": "unhealthy",
        "VERY UNHEALTHY": "very_unhealthy",
        "HAZARDOUS": "hazardous",
    }
    for pollutant, (code, duration) in wanted.items():
        published = [
            (
                float(r["Low Breakpoint"]),
                float(r["High Breakpoint"]),
                int(r["Low AQI"]),
                int(r["High AQI"]),
                names[r["AQI Category"]],
            )
            for r in rows
            # EVERY row but "NONE" (this averaging defines nothing there):
            # the 501-999 rows and the 1-h SO2 cap included
            if r["Parameter Code"] == code
            and r["Duration Description"] == duration
            and r["AQI Category"] != "NONE"
        ]
        assert list(air.EPA[pollutant]) == published, pollutant


def test_eea_bands_are_the_eea_page() -> None:
    page = (FIXTURES / "air" / "eea_aqi_index.html").read_text()
    page = re.sub(r"<style.*?</style>|<script.*?</script>", "", page, flags=re.S)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    labels = {
        "pm2_5": r"\(PM 2\.5 \)",
        "pm10": r"\(PM 10 \)",
        "ozone": r"\(O 3 \)",
        "nitrogen_dioxide": r"\(NO 2 \)",
        "sulphur_dioxide": r"\(SO 2 \)",
    }
    for pollutant, label in labels.items():
        m = re.search(label + r" ((?:\d+-\d+ ){5})>(\d+)", text)
        assert m, pollutant
        uppers = tuple(int(b.split("-")[1]) for b in m.group(1).split())
        assert air.EAQI[pollutant] == uppers, pollutant
        assert int(m.group(2)) == uppers[-1]


@pytest.mark.parametrize(
    ("value", "band"),
    [(5.4, "good"), (5.6, "fair"), (15.0, "fair"), (16.0, "moderate"), (141.0, "extremely_poor")],
)
def test_eaqi_at_its_breakpoints(value: float, band: str) -> None:
    assert air.eaqi({"pm2_5": value})["band"] == band


@pytest.mark.parametrize(
    ("pollutant", "value", "index"),
    [
        ("pm2_5", 9.0, 50),
        ("pm2_5", 9.1, 51),
        ("pm2_5", 35.4, 100),
        ("pm2_5", 35.49, 100),
        ("ozone", 0.054, 50),
        ("ozone", 0.055, 51),
        ("nitrogen_dioxide", 53.0, 50),
        ("sulphur_dioxide", 304.0, 200),
    ],
)
def test_epa_sub_index_at_its_breakpoints(pollutant: str, value: float, index: int) -> None:
    found = air.epa_sub_index(pollutant, value)
    assert found is not None and found[0] == index


def test_extreme_particles_are_hazardous_never_dropped() -> None:
    # concentrations at and past the AQS table's own 501-999 rows: a
    # pollutant above the 0-500 rows once fell out, and ozone made it "good"
    assert air.epa_sub_index("pm2_5", 325.5) == (501, "hazardous", False)
    assert air.epa_sub_index("pm10", 605.0) == (501, "hazardous", False)
    hours = 24
    past = {
        "pm2_5": [400.0] * hours,
        "pm10": [700.0] * hours,
        "ozone": [60.0] * hours,
        "nitrogen_dioxide": [20.0] * hours,
        "sulphur_dioxide": [5.0] * hours,
    }
    us = air.us_aqi(past, hours - 1)
    assert us["category"] == "hazardous" and us["value"] is not None and us["value"] > 500
    assert us["dominant"] in ("pm2_5", "pm10") and us["at_least"] is False
    eu = air.eaqi({k: v[-1] for k, v in past.items()})
    assert eu["band"] == "extremely_poor"  # the two indices now agree


def test_one_hour_so2_above_its_rows_is_a_floor_of_200() -> None:
    # "UNHEALTHY",200,200,305.0,99999.0: 1-h SO2 defines no AQI above 200
    assert air.epa_sub_index("sulphur_dioxide", 305.0) == (200, "unhealthy", True)
    ug = 400.0 * 64.066 / 24.45  # 400 ppb
    us = air.us_aqi({"sulphur_dioxide": [ug]}, 0)
    assert (us["value"], us["category"], us["at_least"], us["dominant"]) == (
        200,
        "unhealthy",
        True,
        "sulphur_dioxide",
    )


def test_ozone_takes_the_one_hour_index_where_it_is_defined() -> None:
    # 1-h ozone has no AQI below 0.125 ppm ("NONE"): normal hours are 8-h only
    assert air.epa_sub_index("ozone_1h", 0.124) is None
    # above 0.200 ppm the 8-h table stops: its 300 is only a floor
    assert air.epa_sub_index("ozone", 0.25) == (300, "very_unhealthy", True)
    ug = 0.45 * 1000 * 48.00 / 24.45  # 0.45 ppm every hour
    us = air.us_aqi({"ozone": [ug] * 8}, 7)
    assert us["dominant"] == "ozone" and us["category"] == "hazardous"
    assert us["value"] == air.epa_sub_index("ozone_1h", 0.45)[0]  # type: ignore[index]
    assert us["at_least"] is False


def test_unknown_is_never_the_lowest_class() -> None:
    assert air.eaqi({})["band"] is None
    assert air.us_aqi({}, 0)["value"] is None
