"""drought (GDO), fire.hotspots (FIRMS), fire.danger (GWIS), risk.water
(Aqueduct), risk.country (INFORM), on verbatim captures."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
import pytest

from app.place import fetch, png, risk
from tests.place_helpers import FIXTURES, at, ctx_for, install, meta_of, read_bytes, read_json

NOW = at("2026-10-27T11:00:00Z")


# ------------------------------------------------------------------ drought


def test_paris_drought_takes_each_indicator_last_element() -> None:
    # the three series of the same point and window, each a capture of its own
    spi = read_json("risk", "gdo_hst_spi_paris.json")
    rdria = read_json("risk", "gdo_hst_rdria_paris.json")
    smang = read_json("risk", "gdo_hst_smang_paris.json")
    for name in ("spi", "rdria", "smang"):
        url = meta_of("risk", f"gdo_hst_{name}_paris.json")["url"]
        assert "lon=2.352&lat=48.857" in url and "date_ini=2026-07-01" in url
    block = risk.drought_block(spi, smang, rdria, ctx_for(48.857, 2.352, NOW))
    out = block.public(NOW, "en")
    d = out["data"]
    assert d["spi_3"] == {"value": -1.92, "period_start": "2026-10-11", "class": "severely_dry"}
    assert d["soil_moisture_anomaly"] == {"value": -2.74, "period_start": "2026-10-11"}
    assert d["agri_risk"] == {"class": 2, "label": "medium", "period_start": "2026-10-01"}
    # the block's as_of is the OLDEST of its indicators' periods (rdria's dekad)
    assert out["as_of"] == "2026-10-01T00:00:00Z" and out["as_of_kind"] == "period_start"
    assert out["source"]["attribution"].endswith(", 2026")


def test_a_null_last_value_stays_null_never_an_older_dekad() -> None:
    # Reykjavik, a window ending 2026-09-15: rdria [... 1, 1, null, null, null]
    spi = read_json("risk", "gdo_hst_spi_reykjavik_to20260915.json")
    smang = read_json("risk", "gdo_hst_smang_reykjavik_to20260915.json")
    rdria = read_json("risk", "gdo_hst_rdria_reykjavik_to20260915.json")
    assert rdria["rdria"][-4:] == [1, None, None, None]
    value, period, _ = risk.last_of(rdria, "rdria")
    assert (value, period) == (None, "2026-09-11")
    block = risk.drought_block(spi, smang, rdria, ctx_for(64.15, -21.95, NOW))
    assert block.status == "ok" and block.data is not None
    assert block.data["agri_risk"] == {"class": None, "label": None, "period_start": "2026-09-11"}
    assert block.data["spi_3"]["value"] == -0.43


def test_rdria_zero_is_no_risk_not_unknown() -> None:
    rdria = read_json("risk", "gdo_hst_rdria_samui.json")
    value, _, _ = risk.last_of(rdria, "rdria")
    assert value == 0.0
    assert risk.RDRIA_LABELS[0] == "none"


def test_mid_pacific_is_at_sea_only_on_all_three_signals() -> None:
    spi = read_json("risk", "gdo_hst_spi_midpacific.json")
    smang = read_json("risk", "gdo_hst_smang_midpacific.json")
    rdria = read_json("risk", "gdo_hst_rdria_midpacific.json")
    assert smang["smang"] == [] and all(v is None for v in rdria["rdria"])
    assert spi["spa03"][-1] == 3.03  # GDO still has precipitation there
    block = risk.drought_block(spi, smang, rdria, ctx_for(0.0, -160.0, NOW))
    assert block.status == "not_applicable" and block.error is not None
    assert block.error.code == "at_sea"


def test_land_with_no_soil_series_keeps_every_other_indicator() -> None:
    # Reykjavik's real soil series is null on every dekad, as at sea; our
    # table says land, and its rdria is not all null: each indicator stands alone
    spi = read_json("risk", "gdo_hst_spi_reykjavik_to20260915.json")
    smang = read_json("risk", "gdo_hst_smang_reykjavik_to20260915.json")
    rdria = read_json("risk", "gdo_hst_rdria_reykjavik_to20260915.json")
    assert all(v is None for v in smang["smang"])
    ctx = ctx_for(64.15, -21.95, NOW)
    assert ctx.place.at_sea is False
    land = risk.drought_block(spi, smang, rdria, ctx)
    assert land.status == "ok" and land.data is not None
    assert land.data["soil_moisture_anomaly"] == {"value": None, "period_start": "2026-09-11"}
    assert land.data["spi_3"]["value"] == -0.43


async def test_drought_is_asked_at_the_point_not_the_cell_centre() -> None:
    # the soil grid is finer than 0.1 deg: GDO's last dekad is -2.15 at
    # Anchorage's point and 0.25 at the centre of its 0.1 deg cell
    cell = read_json("risk", "gdo_hst_smang_anchorage_cell.json")
    assert cell["smang"][-1] == 0.25
    router = install()
    answers = {
        "spaST": read_bytes("risk", "gdo_hst_spi_anchorage_point.json"),
        "smang": read_bytes("risk", "gdo_hst_smang_anchorage_point.json"),
        "rdria": read_bytes("risk", "gdo_hst_rdria_anchorage_point.json"),
    }

    def gdo(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if (params["lat"], params["lon"]) != ("61.22", "-149.90"):
            return httpx.Response(599, content=b"not the point")
        return httpx.Response(200, content=answers[params["srvs"]])

    router.prefix("https://drought.emergency.copernicus.eu/", gdo)
    block = await risk.build_drought(ctx_for(61.22, -149.9, NOW))
    assert block.status == "ok" and block.data is not None
    assert block.data["point"] == {"lat": 61.22, "lon": -149.9}
    assert block.data["soil_moisture_anomaly"] == {"value": -2.15, "period_start": "2026-10-11"}
    assert len(router.seen) == 3


@pytest.mark.parametrize(
    ("value", "cls"),
    [
        (-2.0, "extremely_dry"),
        (-1.92, "severely_dry"),
        (-1.0, "moderately_dry"),
        (-0.99, "normal"),
        (1.0, "moderately_wet"),
        (1.5, "very_wet"),
        (2.0, "extremely_wet"),
        (None, None),
    ],
)
def test_spi_classes(value: float | None, cls: str | None) -> None:
    assert risk.spi_class(value) == cls


# ------------------------------------------------------------ fire hotspots


def test_hotspot_rings_window_and_as_of_from_the_rows() -> None:
    text = (FIXTURES / "risk" / "firms_viirs_noaa21_europe_24h.csv").read_text()
    spots = risk.parse_firms(text)
    # as_of from the newest acquisition, not from Last-Modified (10:43:48)
    assert spots.newest == at("2026-10-27T03:32:00Z")
    first = (float(spots.lat[0]), float(spots.lon[0]))  # 45.95287, 22.86749, 2026-10-26 00:30
    ctx = ctx_for(first[0], first[1], NOW)
    block = risk.hotspot_block({"N21": spots}, ctx)
    out = block.public(NOW, "en")
    assert out["as_of"] == "2026-10-27T03:32:00Z" and out["as_of_kind"] == "through"
    d = out["data"]
    assert d["window_start"] == "2026-10-26T03:32:00Z" and d["window_end"] == "2026-10-27T03:32:00Z"
    # the 00:30 row is 27 h before the newest one: outside the 24 h window
    assert d["nearest"] is None or d["nearest"]["time"] != "2026-10-26T00:30:00Z"
    assert set(d["within_km"]) == {"25", "50", "100"}


def test_header_only_file_states_no_as_of() -> None:
    text = (FIXTURES / "risk" / "firms_viirs_noaa21_alaska_24h_empty.csv").read_text()
    with pytest.raises(fetch.FetchError) as raised:
        risk.parse_firms(text)
    assert raised.value.code == "upstream_parse"


def test_rings_count_every_detection_in_the_window() -> None:
    spots = risk.parse_firms((FIXTURES / "risk" / "firms_viirs_noaa21_europe_24h.csv").read_text())
    end = spots.newest.timestamp()
    inside = [i for i in range(len(spots.t)) if end - 86400 < spots.t[i] <= end]
    i = inside[0]
    ctx = ctx_for(float(spots.lat[i]), float(spots.lon[i]), NOW)
    d = risk.hotspot_block({"N21": spots}, ctx).data
    assert d is not None and d["within_km"]["100"] >= 1 and d["nearest"] is not None
    assert d["nearest"]["distance_km"] <= 1.5  # the point was rounded to 0.01 deg


# -------------------------------------------------------------- fire danger


def test_legend_swatches_are_the_classes_table() -> None:
    rows = png.decode(read_bytes("risk", "gwis_fwi_legend.png"))
    swatches = []
    for y in range(len(rows)):
        rgb = rows[y][5][:3]
        if rgb != (255, 255, 255) and (not swatches or swatches[-1] != rgb):
            swatches.append(rgb)
    assert swatches == list(risk.FWI_LEGEND)
    assert [v[0] for v in risk.FWI_LEGEND.values()] == [
        "low",
        "moderate",
        "high",
        "very_high",
        "extreme",
        "very_extreme",
    ]


def test_the_tile_is_centred_on_the_point_itself() -> None:
    south, north, west, east, key = risk.tile_box(9.51, 100.01)
    assert key == (9.51, 100.01)
    # the centre pixel of the 9x9 tile is centred on the point: WMS samples it there
    assert round((south + north) / 2, 6) == 9.51 and round((west + east) / 2, 6) == 100.01
    assert round((north - south) / risk.TILE, 6) == risk.PIXEL_DEG
    url = risk.GWIS_URL.format(
        s=south, w=west, n=north, e=east, px=risk.TILE, py=risk.TILE, day="2026-10-27"
    )
    assert url == meta_of("risk", "gwis_fwi_ecmwf_samui_point_20261027_9x9.png")["url"]


def _centre_class(
    name: str, lat: float, lon: float
) -> tuple[str, float | None, float | None] | None:
    rows = png.decode(read_bytes("risk", name))
    south, north, west, east, _ = risk.tile_box(lat, lon)
    return risk.fwi_class(risk.pixel_at(rows, south, north, west, east, lat, lon))


def test_ko_samui_has_a_class_at_its_own_point() -> None:
    # the old 2 deg tile's pixel (5, 3) sampled 9.403, 99.903: open sea, 16.7 km away
    cell = png.decode(read_bytes("risk", "gwis_fwi_ecmwf_samui_cell_20261027_9x9.png"))
    assert (
        "bbox=8.625,99.125,10.625,101.125"
        in meta_of("risk", "gwis_fwi_ecmwf_samui_cell_20261027_9x9.png")["url"]
    )
    assert cell[5][3][3] == 0  # transparent: the island read as "masked"
    assert _centre_class("gwis_fwi_ecmwf_samui_point_20261027_9x9.png", 9.51, 100.01) == (
        "low",
        None,
        11.2,
    )


def test_lima_tomorrow_is_the_class_at_the_point() -> None:
    cell = png.decode(read_bytes("risk", "gwis_fwi_ecmwf_lima_cell_20261028_9x9.png"))
    old = risk.fwi_class(risk.pixel_at(cell, -13.125, -11.125, -78.125, -76.125, -12.05, -77.04))
    assert old is not None and old[0] == "moderate"  # the pixel 12.4 km away
    assert _centre_class("gwis_fwi_ecmwf_lima_point_20261028_9x9.png", -12.05, -77.04) == (
        "high",
        21.3,
        38.0,
    )


def test_paris_point_is_transparent() -> None:
    assert _centre_class("gwis_fwi_ecmwf_paris_point_20261027_9x9.png", 48.86, 2.35) is None


def test_lima_tile_pixel_classes_and_the_transparent_sea() -> None:
    meta = meta_of("risk", "gwis_fwi_ecmwf_lima_20261027_9x9.png")
    assert "bbox=-13.046,-78.043,-11.046,-76.043" in meta["url"]
    rows = png.decode(read_bytes("risk", "gwis_fwi_ecmwf_lima_20261027_9x9.png"))
    south, north, west, east = -13.046, -11.046, -78.043, -76.043
    lima = risk.fwi_class(risk.pixel_at(rows, south, north, west, east, -12.046, -77.043))
    assert lima == ("moderate", 11.2, 21.3)
    sea_pixel = risk.pixel_at(rows, south, north, west, east, -11.2, -77.9)
    assert sea_pixel[3] == 0 and risk.fwi_class(sea_pixel) is None
    assert risk.has_colour(read_bytes("risk", "gwis_fwi_ecmwf_lima_20261027_9x9.png"))


def test_world_probes_as_captured() -> None:
    assert risk.has_colour(read_bytes("risk", "gwis_fwi_world_probe_2026-10-27.png"))
    assert risk.has_colour(read_bytes("risk", "gwis_fwi_world_probe_2026-11-03.png"))
    # a date GWIS has not rendered: a real 82-byte answer with no colour at all
    assert not risk.has_colour(read_bytes("risk", "gwis_fwi_world_probe_2026-11-20.png"))


def _gwis(today_probe: str, horizon_probe: str, tile_name: str):  # type: ignore[no-untyped-def]
    import httpx

    tile = read_bytes("risk", tile_name)

    def answer(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        day = url.rsplit("time=", 1)[1]
        if "width=36" in url:
            probe = today_probe if day == "2026-10-27" else horizon_probe
            return httpx.Response(200, content=read_bytes("risk", probe))
        return httpx.Response(200, content=tile)

    return answer


async def test_danger_ok_with_the_horizon_rendered() -> None:
    router = install()
    router.prefix(
        "https://maps.effis.emergency.copernicus.eu/gwis",
        _gwis(
            "gwis_fwi_world_probe_2026-10-27.png",
            "gwis_fwi_world_probe_2026-11-03.png",
            "gwis_fwi_ecmwf_samui_point_20261027_9x9.png",
        ),
    )
    block = await risk.build_danger(ctx_for(9.512, 100.013, NOW))
    assert block.status == "ok" and block.stale_rule is False
    assert block.data is not None and block.data["horizon_end"] == "2026-11-03"
    assert [d["date"] for d in block.data["days"]] == ["2026-10-27", "2026-10-28", "2026-10-29"]
    assert block.data["days"][0]["class"] == "low"  # no longer "masked" at Ko Samui
    assert block.public(NOW, "en")["as_of"] == "2026-10-27T00:00:00Z"
    tiles = [str(r.url) for r in router.seen if "width=9" in str(r.url)]
    assert tiles and all("bbox=9.465,99.965,9.555,100.055" in u for u in tiles)


async def test_danger_is_stale_when_the_horizon_does_not_render() -> None:
    router = install()
    router.prefix(
        "https://maps.effis.emergency.copernicus.eu/gwis",
        _gwis(
            "gwis_fwi_world_probe_2026-10-27.png",
            "gwis_fwi_world_probe_2026-11-20.png",
            "gwis_fwi_ecmwf_lima_point_20261028_9x9.png",
        ),
    )
    block = await risk.build_danger(ctx_for(-12.046, -77.043, NOW))
    assert block.status == "ok" and block.stale_rule is True
    assert block.data is not None and block.data["horizon_end"] is None
    assert block.public(NOW, "en")["stale"] is True


async def test_danger_without_any_colour_today_is_stale_upstream() -> None:
    router = install()
    router.prefix(
        "https://maps.effis.emergency.copernicus.eu/gwis",
        _gwis(
            "gwis_fwi_world_probe_2026-11-20.png",
            "gwis_fwi_world_probe_2026-11-20.png",
            "gwis_fwi_ecmwf_lima_point_20261028_9x9.png",
        ),
    )
    block = await risk.build_danger(ctx_for(-12.046, -77.043, NOW))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "stale_upstream"


# --------------------------------------------------------------- risk.water


def test_aqueduct_labels_verbatim_with_their_own_scale() -> None:
    meta = meta_of("risk", "aqueduct40_annual_bws_samui.json")
    sql = risk.AQUEDUCT_SQL.format(lon="100.01", lat="9.51")
    assert meta["url"] == f"{risk.CARTO_URL}?q={quote(sql)}"  # the query this code sends
    payload = read_json("risk", "aqueduct40_annual_bws_samui.json")
    modified = parsedate_to_datetime(meta["headers"]["last-modified"])
    block = risk.water_block(
        payload["rows"], modified.astimezone(UTC), ctx_for(9.512, 100.013, NOW)
    )
    out = block.public(NOW, "en")
    assert out["as_of"] == "2023-08-10T14:44:21Z" and out["as_of_kind"] == "published"
    d = out["data"]
    assert d["basin_pfaf_id"] == 444035
    assert d["riverine_flood"] == {
        "raw": 0.013414567,
        "cat": 3,
        "label": "High (6 in 1,000 to 1 in 100)",
        "scale": "high",
    }
    assert d["drought_risk"] == {
        "raw": 0.683636859,
        "cat": 3,
        "label": "Medium - High (0.6-0.8)",
        "scale": "medium_high",
    }
    # bws_raw is requested: a value Aqueduct holds never reads as unknown
    assert d["water_stress"] == {
        "raw": 0.491120372,
        "cat": 3,
        "label": "High (40-80%)",
        "scale": "high",
    }
    assert "name_1" not in d  # double-encoded upstream: never returned
    assert out["missing"] == []


def test_aqueduct_water_stress_raw_above_one_at_lima() -> None:
    payload = read_json("risk", "aqueduct40_annual_bws_lima.json")
    block = risk.water_block(
        payload["rows"], datetime(2023, 8, 10, tzinfo=UTC), ctx_for(-12.05, -77.04, NOW)
    )
    assert block.data is not None
    # withdrawals over available supply: a ratio, above 1 in Lima's basin
    assert block.data["water_stress"] == {
        "raw": 1.678044515,
        "cat": 4,
        "label": "Extremely High (>80%)",
        "scale": "extremely_high",
    }


def test_every_wri_label_has_a_scale_key() -> None:
    rows = read_json("risk", "aqueduct40_distinct_labels.json")["rows"]
    assert len(rows) == 25
    for row in rows:
        scale = risk.water_scale(row["label"])
        if row["label"] == "No Data":
            assert row["cat"] == -9999 and scale is None
        else:
            assert scale in risk.SCALE_KEYS, row
    # the same cat means different words for different indicators
    by = {(r["ind"], r["cat"]): risk.water_scale(r["label"]) for r in rows}
    assert by[("drr", 3)] == "medium_high" and by[("rfr", 3)] == "high"
    assert by[("bws", -1)] == "arid_low_water_use"


def test_aqueduct_empty_answer() -> None:
    payload = read_json("risk", "aqueduct40_annual_midpacific_empty.json")
    when = datetime(2023, 8, 10, tzinfo=UTC)
    at_sea = risk.water_block(payload["rows"], when, ctx_for(0.0, -160.0, NOW))
    assert at_sea.status == "not_applicable" and at_sea.error is not None
    assert at_sea.error.code == "at_sea"
    land = risk.water_block(payload["rows"], when, ctx_for(64.147, -21.942, NOW))
    assert land.status == "unavailable" and land.error is not None and land.error.code == "masked"


# ------------------------------------------------------------- risk.country


def test_inform_scores_from_the_fixture() -> None:
    scores = read_json("risk", "inform_mid2026_tha.json")
    workflow = read_json("risk", "inform_workflow_2026mid.json")[0]
    block = risk.country_block(scores, workflow, "THA", ctx_for(9.512, 100.013, NOW))
    out = block.public(NOW, "en")
    assert out["as_of"] == "2026-09-02T00:00:00Z" and out["as_of_kind"] == "published"
    d = out["data"]
    assert (d["inform"], d["hazard_exposure"], d["natural_hazard"]) == (4.9, 7.4, 5.9)
    assert (d["vulnerability"], d["lack_of_coping_capacity"]) == (4.1, 3.9)
    assert d["hazards"] == {
        "earthquake": 1.7,
        "river_flood": 9.8,
        "coastal_flood": 5.6,
        "tropical_cyclone": 1.6,
        "drought": 5.2,
        "tsunami": 5.8,
        "epidemic": 5.8,
    }
    assert d["release"] == "INFORM Risk Mid 2026" and d["country_basis"] == "containing"


def test_inform_empty_list_is_no_coverage_not_zero() -> None:
    workflow = read_json("risk", "inform_workflow_2026mid.json")[0]
    empty = read_json("risk", "inform_mid2026_umi_empty.json")
    assert empty == []
    block = risk.country_block(empty, workflow, "UMI", ctx_for(19.28, 166.65, NOW))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_coverage"


def test_latest_global_group_comes_from_the_listing() -> None:
    names = read_json("risk", "inform_workflow_groups.json")
    ordered = risk.latest_groups(names)
    assert ordered[0] == "INFORM2026_Mid"
    assert "INFORM_SAHEL" not in ordered and "INFORM_LAC" not in ordered
    assert (
        ordered.index("INFORM2025_Mid")
        < ordered.index("INFORM2025_2nd")
        < ordered.index("INFORM2025")
    )


async def test_clipperton_has_no_alpha_3_and_is_not_scored_as_france() -> None:
    install()  # no route: a request to INFORM would answer 599
    block = await risk.build_country(ctx_for(10.29, -109.22, NOW))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_coverage"
    assert block.error.reason == "no ISO alpha-3 code for CP"


async def test_bouvet_is_bvt_which_inform_does_not_score_not_norway() -> None:
    router = install()
    for name in ("inform_workflow_groups.json", "inform_workflow_2026mid.json"):
        router.add(meta_of("risk", name)["url"], read_bytes("risk", name))
    capture = "inform_mid2026_bvt_empty.json"
    router.add(meta_of("risk", capture)["url"], read_bytes("risk", capture))
    block = await risk.build_country(ctx_for(-54.42, 3.36, NOW))
    assert [r.url.params.get("Iso3") for r in router.seen if "Scores" in r.url.path] == ["BVT"]
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "no_coverage"
    assert block.error.reason == "INFORM has no score for BVT"


async def test_no_country_at_sea() -> None:
    install()
    # open sea: (0, -160) is 41 km from Jarvis Island (UM) at 1:10m
    block = await risk.build_country(ctx_for(0.0, -140.0, NOW))
    assert block.status == "not_applicable" and block.error is not None
    assert block.error.code == "no_country"


def test_aqueduct_licence_is_wri_own() -> None:
    import html as html_lib
    import re

    def text(name: str) -> str:
        page = (FIXTURES / "risk" / name).read_text(encoding="utf-8", errors="replace")
        page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
        return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", page)))

    # the Aqueduct 4.0 data page says "Creative Commons"; WRI's open data
    # commitment names which one
    assert "License Creative Commons" in text("wri_aqueduct40_data_page.html")
    assert (
        "adopted the Creative Commons Attribution 4.0 International License as our standard "
        "license for publications and data" in text("wri_open_data_commitment.html")
    )
    assert risk.SRC_AQUEDUCT.licence == "CC BY 4.0"
