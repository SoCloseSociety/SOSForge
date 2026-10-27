"""drought (GDO), fire.hotspots (FIRMS), fire.danger (GWIS), risk.water
(Aqueduct), risk.country (INFORM), on verbatim captures."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import pytest

from app.place import fetch, png, risk
from tests.place_helpers import FIXTURES, at, ctx_for, install, meta_of, read_bytes, read_json

NOW = at("2026-10-27T11:00:00Z")


# ------------------------------------------------------------------ drought


def test_paris_drought_takes_each_indicator_last_element() -> None:
    spi = read_json("risk", "gdo_hst_spi_paris.json")
    rdria = read_json("risk", "gdo_hst_rdria_paris.json")
    smang = read_json("risk", "gdo_hst_smang_samui.json")  # no Paris soil capture
    block = risk.drought_block(spi, smang, rdria, ctx_for(48.857, 2.352, NOW))
    out = block.public(NOW, "en")
    d = out["data"]
    assert d["spi_3"] == {"value": -1.92, "period_start": "2026-10-11", "class": "severely_dry"}
    assert d["agri_risk"] == {"class": 2, "label": "medium", "period_start": "2026-10-01"}
    # the block's as_of is the OLDEST of its indicators' periods
    assert out["as_of"] == "2026-10-01T00:00:00Z" and out["as_of_kind"] == "period_start"
    assert out["source"]["attribution"].endswith(", 2026")


def test_a_null_last_value_stays_null_never_an_older_dekad() -> None:
    spi = read_json("risk", "gdo_hst_spi_samui.json")
    rdria = {"dates": ["2026-09-21", "2026-10-01"], "rdria": [2, None]}
    value, period, _ = risk.last_of(rdria, "rdria")
    assert (value, period) == (None, "2026-10-01")
    smang = read_json("risk", "gdo_hst_smang_samui.json")
    block = risk.drought_block(spi, smang, rdria, ctx_for(9.512, 100.013, NOW))
    assert block.data is not None and block.data["agri_risk"]["class"] is None


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
    # the same empty answers on a point our table calls land: each indicator stands alone
    land = risk.drought_block(spi, smang, rdria, ctx_for(9.512, 100.013, NOW))
    assert land.status == "ok" and land.data is not None
    assert (
        land.data["spi_3"]["value"] == 3.03 and land.data["soil_moisture_anomaly"]["value"] is None
    )


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


def _gwis(today_probe: str, horizon_probe: str):  # type: ignore[no-untyped-def]
    import httpx

    tile = read_bytes("risk", "gwis_fwi_ecmwf_lima_20261027_9x9.png")

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
        _gwis("gwis_fwi_world_probe_2026-10-27.png", "gwis_fwi_world_probe_2026-11-03.png"),
    )
    block = await risk.build_danger(ctx_for(-12.046, -77.043, NOW))
    assert block.status == "ok" and block.stale_rule is False
    assert block.data is not None and block.data["horizon_end"] == "2026-11-03"
    assert [d["date"] for d in block.data["days"]] == ["2026-10-27", "2026-10-28", "2026-10-29"]
    assert block.public(NOW, "en")["as_of"] == "2026-10-27T00:00:00Z"


async def test_danger_is_stale_when_the_horizon_does_not_render() -> None:
    router = install()
    router.prefix(
        "https://maps.effis.emergency.copernicus.eu/gwis",
        _gwis("gwis_fwi_world_probe_2026-10-27.png", "gwis_fwi_world_probe_2026-11-20.png"),
    )
    block = await risk.build_danger(ctx_for(-12.046, -77.043, NOW))
    assert block.status == "ok" and block.stale_rule is True
    assert block.data is not None and block.data["horizon_end"] is None
    assert block.public(NOW, "en")["stale"] is True


async def test_danger_without_any_colour_today_is_stale_upstream() -> None:
    router = install()
    router.prefix(
        "https://maps.effis.emergency.copernicus.eu/gwis",
        _gwis("gwis_fwi_world_probe_2026-11-20.png", "gwis_fwi_world_probe_2026-11-20.png"),
    )
    block = await risk.build_danger(ctx_for(-12.046, -77.043, NOW))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "stale_upstream"


# --------------------------------------------------------------- risk.water


def test_aqueduct_labels_verbatim_with_their_own_scale() -> None:
    payload = read_json("risk", "aqueduct40_annual_samui.json")
    modified = parsedate_to_datetime(
        meta_of("risk", "aqueduct40_annual_samui.json")["headers"]["last-modified"]
    )
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
    assert d["water_stress"] == {"raw": None, "cat": 3, "label": "High (40-80%)", "scale": "high"}
    assert "name_1" not in d  # double-encoded upstream: never returned


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


async def test_no_country_at_sea() -> None:
    install()
    block = await risk.build_country(ctx_for(0.0, -160.0, NOW))
    assert block.status == "not_applicable" and block.error is not None
    assert block.error.code == "no_country"
