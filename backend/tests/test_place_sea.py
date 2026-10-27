"""sea.waves and sea.level (Open-Meteo bucket cassettes), sea.temperature
(CoastWatch), sea.buoy (NDBC), and the sea gate."""

from __future__ import annotations

import re

import pytest

from app.place import sea
from tests.place_helpers import FIXTURES, at, ctx_for, install, meta_of, read_json

WAVES = FIXTURES / "sea" / "openmeteo_s3_meteofrance_wave_wave_height_kohsamui"
LEVEL = FIXTURES / "sea" / "openmeteo_s3_meteofrance_currents_sea_level_lima"


async def test_waves_replay_at_ko_samui() -> None:
    router = install()
    cas = router.cassette(WAVES)
    ctx = ctx_for(9.512, 100.013, at(cas["exchanges"][0]["fetched_utc"]))
    block = await sea.build_waves(ctx)
    assert block.status == "ok", block.error
    out = block.public(ctx.now, "en")
    # the cassette's meta.json names the 12Z run of the 26th
    assert out["as_of"] == "2026-10-26T12:00:00Z" and out["as_of_kind"] == "model_run"
    d = out["data"]
    assert (d["cell"]["lat"], d["cell"]["lon"]) == (9.5417, 100.0417)
    heights = [h["height_m"] for h in d["hours"]]
    assert heights == cas["decoded_values_for_replay_check"][: len(heights)]
    assert d["hours"][0]["time"] == cas["first_step_utc"]
    assert d["max_48h"]["height_m"] == max(heights)
    assert "2026" in out["source"]["attribution"]  # the Copernicus year is the run's


async def test_sea_level_replay_at_lima_with_model_extremes() -> None:
    router = install()
    cas = router.cassette(LEVEL)
    ctx = ctx_for(-12.046, -77.043, at(cas["exchanges"][0]["fetched_utc"]))
    block = await sea.build_level(ctx)
    assert block.status == "ok", block.error
    d = block.data
    # SMOC is referenced to the GLOBAL mean sea level: never "the local mean"
    assert d is not None and d["datum"] == "global_msl"
    levels = [h["level_m"] for h in d["hours"]]
    assert levels[:24] == cas["decoded_values_for_replay_check"]
    known = [v for v in levels if v is not None]
    assert abs(d["window_mean_m"] - sum(known) / len(known)) < 0.01
    assert "window_mean_m" in d["derived"]
    assert d["cell"]["distance_km"] < 1.0
    # 0.59 0.48 0.33 0.15 0.0 -0.08 (low) -0.06 ... 0.89 (high) ... 0.19 0.19 (low)
    first = d["extremes"][:3]
    assert [e["type"] for e in first] == ["low", "high", "low"]
    assert first[0]["level_m"] == -0.08 and first[1]["level_m"] == 0.89


def test_the_datum_is_the_one_open_meteo_documents() -> None:
    page = (FIXTURES / "sea" / "openmeteo_marine_docs.html").read_text(encoding="utf-8")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    assert "sea_level_height_msl" in text
    assert "The reference (datum) height is the global mean sea level" in text
    assert "sea surface height, global mean steric variation" in text


def test_extremes_need_ten_centimetres_of_range() -> None:
    t = [at(f"2026-10-27T{h:02d}:00:00Z") for h in range(8)]
    small = [0.50, 0.55, 0.52, 0.56, 0.53, 0.40, 0.20, 0.30]
    out = sea.extremes(t, small)
    # the 3-4 cm wiggles (0.55 / 0.52) are dropped as a pair; 0.56 then 0.20
    # differ by 0.36 m and both stand
    assert [(e["level_m"], e["type"]) for e in out] == [(0.56, "high"), (0.2, "low")]


@pytest.mark.parametrize(
    ("lat", "lon", "applies", "km"),
    [
        (48.857, 2.352, False, 150),
        (27.717, 85.324, False, 648),
        (9.512, 100.013, True, None),
        (0.0, -160.0, True, None),
    ],
)
def test_sea_gate(lat: float, lon: float, applies: bool, km: int | None) -> None:
    ctx = ctx_for(lat, lon, at("2026-10-27T11:00:00Z"))
    na = sea.gate(sea.SPEC_WAVES, ctx)
    if applies:
        assert na is None
    else:
        assert na is not None and na.status == "not_applicable" and na.error is not None
        assert na.error.code == "inland" and na.error.params["km"] == km


def test_sst_reads_the_mask_as_bits_and_the_nearest_water_cell() -> None:
    cells = sea.parse_sst(read_json("sea", "coastwatch_blendedsst_box_kohsamui.json"))
    assert sea.water(1) and sea.water(5) and not sea.water(2) and not sea.water(0)
    ctx = ctx_for(9.512, 100.013, at("2026-10-27T11:18:03Z"))
    block = sea.sst_block(cells, ctx)
    out = block.public(ctx.now, "en")
    assert out["status"] == "ok" and out["as_of"] == "2026-10-25T12:00:00Z"
    d = out["data"]
    assert d["day"] == "2026-10-25" and d["ice"] is False
    assert d["cell"]["distance_km"] <= 30
    nearest = min(
        (c for c in cells if c.sst is not None and sea.water(c.mask)),
        key=lambda c: (c.lat - ctx.lat) ** 2 + ((c.lon - ctx.lon) * 0.986) ** 2,
    )
    assert d["sst_c"] == round(nearest.sst, 1)


def test_sst_all_land_box_has_no_water_cell() -> None:
    cells = sea.parse_sst(read_json("sea", "coastwatch_blendedsst_box_kathmandu_all_land.json"))
    assert all(not sea.water(c.mask) for c in cells)
    block = sea.sst_block(cells, ctx_for(27.717, 85.324, at("2026-10-27T11:18:03Z")))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "coast_not_resolved"


def test_sst_box_covers_the_cell_and_30_km() -> None:
    south, north, west, east, key = sea.sst_box(64.15, -21.94)
    assert key == (64.175, -21.925)
    assert north - south > 2 * 30 / 111.32
    # 30 km of longitude at 64 N is ~0.62 deg, more than the old 0.25 deg box
    assert (east - west) / 2 > 0.6


@pytest.fixture(scope="module")
def ndbc() -> tuple[list[sea.Obs], dict[str, sea.StationInfo], dict[str, sea.Owner]]:
    latest = (FIXTURES / "sea" / "ndbc_latest_obs.txt").read_text()
    table = (FIXTURES / "sea" / "ndbc_station_table.txt").read_text()
    # served as ISO-8859-1 (its Content-Type says so)
    owners = (FIXTURES / "sea" / "ndbc_station_owners.txt").read_text(encoding="iso-8859-1")
    return sea.parse_latest(latest), sea.parse_station_table(table), sea.parse_owners(owners)


def test_buoy_joins_ids_case_insensitively_and_names_the_station_type(ndbc) -> None:
    obs, table, owners = ndbc
    now = at(meta_of("sea", "ndbc_latest_obs.txt")["fetched_utc"])
    block = sea.buoy_block(obs, table, owners, ctx_for(61.218, -149.900, now))
    out = block.public(now, "en")
    assert out["status"] == "ok" and out["as_of"] == "2026-10-27T10:52:00Z"
    d = out["data"]
    assert d["station"] == {
        "id": "APMA2",
        "name": "Anchorage Port, AK",
        "type": "Coastal Marine Station",
        "owner_code": "MA",
        # station_owners.txt: "MA |Marine Exchange of Alaska|US"
        "owner": "Marine Exchange of Alaska",
        "owner_country": "US",
        "distance_km": 2.1,
    }
    # a partner station's owner is credited where the panel shows the source
    assert "owned by Marine Exchange of Alaska" in out["source"]["attribution"]
    assert out["missing"] == []
    # MM is null, never 0
    assert d["wave_height_m"] is None and d["water_temp_c"] is None and d["pressure_hpa"] is None
    assert (d["wind_ms"], d["wind_from_deg"], d["gust_ms"], d["air_temp_c"]) == (1.5, 30, 3.1, 2.2)


def test_owner_unknown_without_the_owners_file_says_so(ndbc) -> None:
    obs, table, _ = ndbc
    now = at(meta_of("sea", "ndbc_latest_obs.txt")["fetched_utc"])
    block = sea.buoy_block(obs, table, None, ctx_for(61.218, -149.900, now))
    out = block.public(now, "en")
    assert out["data"]["station"]["owner"] is None
    assert "NDBC owner code MA" in out["source"]["attribution"]
    assert out["missing"] == [{"field": "station.owner", "reason": "not_in_source"}]


def test_every_owner_code_in_the_station_table_resolves_or_is_listed(ndbc) -> None:
    _, table, owners = ndbc
    codes = {info.owner_code for info in table.values() if info.owner_code}
    resolved = {code for code in codes if code in owners}
    # measured on the two captures of 2026-10-27: every code resolves
    assert resolved == codes, sorted(codes - resolved)
    assert owners["KO"] == sea.Owner("Korean Meteorological Administration", "KR")
    assert owners["N"] == sea.Owner("NDBC", "US")  # padded fields, stripped


def test_kma_period_is_dropped(ndbc) -> None:
    obs, table, owners = ndbc
    now = at("2026-10-27T11:17:45Z")
    block = sea.buoy_block(obs, table, owners, ctx_for(33.08, 126.03, now))
    d = block.data
    assert d is not None and d["station"]["id"] == "22107" and d["station"]["owner_code"] == "KO"
    assert d["station"]["owner"] == "Korean Meteorological Administration"
    assert d["wave_height_m"] == 2.5 and d["dominant_period_s"] is None


def test_tide_is_converted_from_feet(ndbc) -> None:
    obs, table, owners = ndbc
    block = sea.buoy_block(obs, table, owners, ctx_for(25.478, -80.989, at("2026-10-27T11:17:45Z")))
    d = block.data
    assert d is not None and d["station"]["id"] == "BDVF1"
    assert d["tide_m"] == round(0.52 * 0.3048, 2) and d["derived"] == ["tide_m"]


def test_no_buoy_reporting_within_100_km(ndbc) -> None:
    obs, table, owners = ndbc
    block = sea.buoy_block(obs, table, owners, ctx_for(9.512, 100.013, at("2026-10-27T11:17:45Z")))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_station"
