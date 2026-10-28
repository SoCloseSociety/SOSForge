"""weather_now (METAR), forecast_hourly / forecast_daily (MET Norway), wind
(NWS for the US, MET elsewhere), on verbatim captures."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta

import httpx
import pytest

from app.place import block as B
from app.place import fetch, metar, weather
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


# ------------------------------------------------------------ cache memory


@pytest.mark.parametrize(
    ("name", "lat", "lon"),
    [
        ("metno_complete_samui.json", 9.512, 100.013),
        ("metno_complete_oslo.json", 59.91, 10.75),
        ("metno_complete_anchorage.json", 61.218, -149.9),
        ("metno_complete_midpacific.json", 0.0, -160.0),
    ],
)
def test_slim_payload_builds_the_same_blocks(name: str, lat: float, lon: float) -> None:
    full = read_json("weather", name)
    slim, size = weather._parse_met(read_bytes("weather", name))
    now = at(meta_of("weather", name)["fetched_utc"])
    ctx = ctx_for(lat, lon, now)
    for build in (weather.hourly_block, weather.daily_block):
        assert build(slim, ctx).public(now, "en") == build(full, ctx).public(now, "en")
    assert weather.met_wind(slim, ctx) == weather.met_wind(full, ctx)
    # what the cache holds is what it counts: 197 KB where the full parse
    # takes 259 to 286 KB (61 to 90 KB on the wire, which it used to count)
    assert size == fetch.deep_size(slim) < fetch.deep_size(full) * 0.8
    assert size > 2 * len(read_bytes("weather", name))


def test_grid_cache_holds_the_wind_layers_only() -> None:
    raw = read_bytes("weather", "nws_grid_anchorage.json")
    props, size = weather._parse_grid(raw)
    assert set(props) == {"updateTime", "windSpeed", "windDirection", "windGust"}
    full = read_json("weather", "nws_grid_anchorage.json")["properties"]
    now = at(meta_of("weather", "nws_grid_anchorage.json")["fetched_utc"])
    assert weather.nws_hours(props, now) == weather.nws_hours(full, now)
    assert size < fetch.deep_size(full) / 4


def test_forecast_caches_are_bounded_in_bytes() -> None:
    assert weather._met_cache.max_bytes == weather.MET_CACHE_BYTES
    assert weather._grid_cache.max_bytes == weather.GRID_CACHE_BYTES
    cache = fetch.Cache("test bytes", max_entries=4096, max_bytes=1000)
    for key in range(5):
        cache.sized(cache.get(key), 300)
    # the oldest entries go first once the bytes are over the bound
    assert list(cache.entries) == [2, 3, 4] and cache.bytes == 900
    fetch.Cache.registry.remove(cache)


# ------------------------------------------------- METAR, merged across reads

PAIR_A = "awc_metars_cache_global_pair_a.csv.gz"  # read 17:24Z
PAIR_B = "awc_metars_cache_global_pair_b.csv.gz"  # read 17:39Z
# two reads 3 min apart, the background refresh's cadence (18:37Z, 18:40Z)
CHAIN_A = "awc_metars_cache_global_chain_a.csv.gz"
CHAIN_B = "awc_metars_cache_global_chain_b.csv.gz"
OCEAN = (0.0, -140.0)  # no METAR station within 50 km


def _headers(name: str) -> dict[str, str]:
    """The response headers recorded in a capture's sidecar."""
    lines = meta_of("weather", name)["raw_headers"].splitlines()[1:]
    return dict(line.split(": ", 1) for line in lines if ": " in line)


def _read(name: str) -> weather.MetarIndex:
    """A capture as the loader takes it: parsed, current at its Last-Modified."""
    headers = _headers(name)
    meta = meta_of("weather", name)
    read = at(meta["fetched_at_utc"]) if "fetched_at_utc" in meta else CACHE_FETCHED
    index = weather.parse_metar_cache(read_bytes("weather", name))
    return weather.stamped(index, weather.file_time(headers.get("last-modified"), read))


def _answer(name: str) -> httpx.Response:
    return httpx.Response(200, headers=_headers(name), content=read_bytes("weather", name))


@pytest.fixture(scope="module")
def metar_pair() -> tuple[weather.MetarIndex, weather.MetarIndex]:
    # two real reads of the cache file, 15 min apart (17:24Z and 17:39Z),
    # parsed bare (no read time): oldest rows 15:55 and 16:30
    a = weather.parse_metar_cache(read_bytes("weather", PAIR_A))
    b = weather.parse_metar_cache(read_bytes("weather", PAIR_B))
    return a, b


def _stations(index: weather.MetarIndex) -> dict[str, weather.Station]:
    return {s.icao: s for cell in index.cells.values() for s in cell}


def _oldest(index: weather.MetarIndex) -> datetime:
    return min(s.time for s in _stations(index).values())


def _times_named(block: B.Block, now: datetime) -> list[str]:
    """Every HH:MM the block says, to operators and to readers."""
    assert block.error is not None
    said = [block.error.reason, block.public(now, "en")["error"]["message"]]
    said += [str(v) for v in block.error.params.values()]
    return re.findall(r"\b\d\d:\d\d\b", " ".join(said))


def test_the_cache_file_holds_80_minutes_so_reads_are_merged(metar_pair) -> None:
    a, b = metar_pair
    assert (_oldest(a), a.newest) == (at("2026-10-27T15:55:00Z"), at("2026-10-27T17:23:00Z"))
    dropped = set(_stations(a)) - set(_stations(b))
    assert len(dropped) == 51  # reported at 15:55-17:23, gone from the next read
    merged = weather.merge_metar(a, b)
    assert dropped <= set(_stations(merged))
    assert merged.newest == b.newest
    assert merged.since is None and merged.read_at is None  # bare parses: no cover claimed
    # newest report per station: LFPB's 17:30Z, not its 17:00Z
    assert _stations(merged)["LFPB"].time == at("2026-10-27T17:30:00Z")


def test_a_station_the_next_read_dropped_is_still_the_nearest(metar_pair) -> None:
    a, b = metar_pair
    merged = weather.merge_metar(a, b)
    now = at("2026-10-27T17:40:00Z")
    for icao in sorted(set(_stations(a)) - set(_stations(b))):
        s = _stations(a)[icao]
        alone = weather.now_block(b, ctx_for(s.lat, s.lon, now))
        if alone.status == "unavailable":  # nobody else within 50 km
            break
    else:
        pytest.fail("every dropped station has a neighbour: no case to test")
    block = weather.now_block(merged, ctx_for(s.lat, s.lon, now))
    assert block.status == "ok" and block.data is not None
    assert block.data["station"]["icao"] == icao


def test_a_same_time_correction_replaces_the_report_it_corrects(metar_pair) -> None:
    # pair_a lists ETAR's 17:10Z SPECI as "AUTO ... 4000 BR"; pair_b re-issues
    # it at the SAME observation time as "COR ... 9999", and SAZG's 17:00Z
    # COR takes out the SCT100 layer. The original is retracted, not current.
    a, b = metar_pair
    assert _stations(a)["ETAR"].time == _stations(b)["ETAR"].time
    merged = weather.merge_metar(a, b)
    now = at("2026-10-27T17:40:00Z")
    etar = _stations(b)["ETAR"]
    d = weather.now_block(merged, ctx_for(etar.lat, etar.lon, now)).data
    assert d is not None and d["station"]["icao"] == "ETAR"
    assert " COR " in d["raw"] and "4000 BR" not in d["raw"]
    assert (d["visibility_km"], d["wx"], d["flight_category"]) == (10.0, None, "VFR")
    sazg = _stations(b)["SAZG"]
    d = weather.now_block(merged, ctx_for(sazg.lat, sazg.lon, now)).data
    assert d is not None and d["station"]["icao"] == "SAZG"
    assert d["raw"].startswith("METAR SAZG 271700Z COR ")
    assert d["clouds"] == [{"cover": "SCT", "base_ft": 2500}]


def test_an_older_copy_read_later_does_not_bring_the_original_back() -> None:
    # a CDN copy can be older than the file already held: taken in that
    # order, pair_a's original must not overturn pair_b's correction
    held, older = _read(PAIR_B), _read(PAIR_A)
    assert older.read_at is not None and held.read_at is not None
    assert older.read_at < held.read_at
    merged = weather.merge_metar(held, older)
    assert " COR " in _stations(merged)["ETAR"].raw
    assert (merged.read_at, merged.since) == (held.read_at, held.since)


def test_a_read_is_current_at_its_last_modified_never_later_than_the_read() -> None:
    read = at("2026-10-27T17:39:18Z")
    lm = _headers(PAIR_B)["last-modified"]
    assert weather.file_time(lm, read) == at("2026-10-27T17:38:09Z")
    assert weather.file_time(lm, at("2026-10-27T17:30:00Z")) == at("2026-10-27T17:30:00Z")
    assert weather.file_time(None, read) == read
    assert weather.file_time("yesterday", read) == read


async def test_after_a_restart_no_station_never_names_a_window_it_does_not_hold() -> None:
    # KOYE, an oil platform in the Gulf of Mexico, reported at 17:10Z: listed
    # at 17:24Z, gone at 17:39Z while 16:30Z rows stayed. The first read
    # after a restart is the 17:39Z file: its oldest row says nothing about
    # what it holds, and "no station since 16:30 UTC" was false there.
    koye = _stations(weather.parse_metar_cache(read_bytes("weather", PAIR_A)))["KOYE"]
    assert koye.time == at("2026-10-27T17:10:00Z")
    first = weather.parse_metar_cache(read_bytes("weather", PAIR_B))
    assert "KOYE" not in _stations(first) and _oldest(first) == at("2026-10-27T16:30:00Z")
    router = install()
    router.handler(weather.METAR_URL, lambda request: _answer(PAIR_B))
    loaded = await weather._load_metar(fetch.Entry())
    index = loaded.value
    now = at("2026-10-27T17:40:00Z")
    block = weather.now_block(index, ctx_for(28.87, -90.49, now))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_station"
    assert all(t >= "17:10" for t in _times_named(block, now))
    # the cover starts at the read itself (its Last-Modified), and two
    # minutes of it name no window at all
    assert index.since == index.read_at == at("2026-10-27T17:38:09Z")
    assert block.error.params == {"km": 50}
    message = block.public(now, "en")["error"]["message"]
    assert message == "No station within 50 km is in the latest reports."


def test_the_window_named_is_the_cover_held() -> None:
    a, b = _read(CHAIN_A), _read(CHAIN_B)
    merged = weather.merge_metar(a, b)
    start = at("2026-10-27T18:37:06Z")
    assert merged.since == a.since == start
    assert merged.read_at == b.read_at == at("2026-10-27T18:40:10Z")

    def error(now: datetime) -> B.Problem:
        block = weather.now_block(merged, ctx_for(*OCEAN, now))
        assert block.status == "unavailable" and block.error is not None
        return block.error

    assert error(start + timedelta(minutes=59)).params == {"km": 50}
    # rounded UP: a report observed at 18:37:00 is not vouched for
    later = error(start + timedelta(minutes=61))
    assert later.params == {"km": 50, "since": "18:38"}
    assert later.reason == "no METAR within 50 km reported since 18:38 UTC"
    assert error(start + timedelta(hours=3)).params == {"km": 50, "hours": 3}


def test_a_report_dropped_between_two_close_reads_is_still_served() -> None:
    # KOYE's 18:30Z report again, an hour later: listed 18:31Z to 18:38Z,
    # gone from the 18:40Z file with no newer one, and no other station
    # within 50 km (KPBM is 61 km away). Read 3 min apart, it stays served.
    a, b = _read(CHAIN_A), _read(CHAIN_B)
    assert _stations(a)["KOYE"].time == at("2026-10-27T18:30:00Z")
    assert "KOYE" not in _stations(b)
    dropped = set(_stations(a)) - set(_stations(b))
    merged = weather.merge_metar(a, b)
    assert dropped <= set(_stations(merged))
    now = at("2026-10-27T18:41:00Z")
    alone = weather.now_block(b, ctx_for(28.87, -90.49, now))
    assert alone.status == "unavailable"
    block = weather.now_block(merged, ctx_for(28.87, -90.49, now))
    assert block.status == "ok" and block.data is not None
    assert block.data["station"]["icao"] == "KOYE"
    assert block.public(now, "en")["as_of"] == "2026-10-27T18:30:00Z"


def test_reads_further_apart_than_a_report_stays_listed_start_the_cover_over() -> None:
    # 17:24:08 then 17:38:09: KOYE's 18:30Z report was listed for 7 to 10
    # minutes only, so one can come and go in 14, and a station whose newer
    # report did would be served its superseded one
    a, b = _read(PAIR_A), _read(PAIR_B)
    assert a.read_at is not None and b.read_at is not None
    assert b.read_at - a.read_at > weather.METAR_MAX_GAP
    assert weather.merge_metar(a, b) is b
    # 11:09Z, then 17:24Z: the reads failed for six hours in between
    assert weather.merge_metar(_read("awc_metars_cache_global.csv.gz"), a) is a


async def test_the_loader_merges_into_the_copy_it_had() -> None:
    held = _read(CHAIN_A)
    router = install()
    router.handler(weather.METAR_URL, lambda request: _answer(CHAIN_B))
    loaded = await weather._load_metar(fetch.Entry(value=held))
    index = loaded.value
    assert (index.since, index.read_at) == (held.since, at("2026-10-27T18:40:10Z"))
    both = set(_stations(held)) | set(_stations(_read(CHAIN_B)))
    assert set(_stations(index)) == both


async def test_the_cache_file_is_read_every_3_minutes_without_a_visitor(monkeypatch) -> None:
    from app import place

    assert place.start(enabled=False) == []  # the report switched off: no read at all
    router = install()
    answers = [lambda: httpx.Response(503), lambda: _answer(CHAIN_A), lambda: _answer(CHAIN_B)]
    agents: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["user-agent"])
        return answers[min(len(agents), len(answers)) - 1]()

    router.handler(weather.METAR_URL, answer)
    monkeypatch.setattr(weather, "METAR_TTL_S", 0.05)
    monkeypatch.setattr(fetch, "FAILURE_MEMORY_S", 0.05)
    tasks = place.start(enabled=True)
    assert [t.get_name() for t in tasks] == ["place:metar-refresh", "place:inform-release"]
    try:
        async with asyncio.timeout(10):
            while len(agents) < 3:
                await asyncio.sleep(0.05)
            while (entry := weather._metar_cache.peek("global")) is None or entry.task:
                await asyncio.sleep(0.05)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    # the failure was retried, then the expired copy read again and merged
    assert agents == [fetch.USER_AGENT] * 3
    index = entry.value
    assert (index.since, index.read_at) == (at("2026-10-27T18:37:06Z"), at("2026-10-27T18:40:10Z"))
    seen = len(agents)
    await asyncio.sleep(1.2)
    assert len(agents) == seen  # cancelled with the lifespan: no read after


def test_a_message_variant_is_the_one_naming_the_most_parameters() -> None:
    from app.place import messages

    def say(**params: object) -> str:
        return messages.message("no_station", "en", params)

    assert say(km=50) == "No station within 50 km is in the latest reports."
    assert say(km=50, since="18:38") == "No station within 50 km has reported since 18:38 UTC."
    assert say(km=50, hours=3) == "No station within 50 km has reported in the last 3 hours."
    assert say() == "No station near this place has reported recently."
