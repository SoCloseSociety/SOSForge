"""history.*: USGS ComCat, NCEI (significant earthquakes, tsunamis,
eruptions), GVP volcanoes, IBTrACS cyclones -- on verbatim captures."""

from __future__ import annotations

import json
import re

import pytest

from app.place import cyclones, history
from tests.place_helpers import FIXTURES, at, ctx_for, install, read_json

NOW = at("2026-10-27T11:00:00Z")
THROUGH = at("2026-10-27T10:31:00Z")  # what a newest-event probe would have said


def _csv(name: str) -> str:
    return (FIXTURES / "history" / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------- USGS


def test_samui_strongest_with_distance_and_bearing_from_coordinates() -> None:
    q = history.parse_usgs(_csv("usgs_csv_all_samui.csv"), THROUGH)
    block = history.quake_block(q, ctx_for(9.512, 100.013, NOW))
    out = block.public(NOW, "en")
    d = out["data"]
    assert d["total"] == 4 and d["min_magnitude"] == 4.5 and d["since_year"] == 1900
    top = d["strongest"][0]
    assert top["id"] == "usp000eunq" and top["time"] == "2006-10-07T21:12:26.370Z"
    assert (top["mag"], top["mag_type"], top["depth_km"]) == (5.0, "mwc", 10.0)
    assert (top["distance_km"], top["bearing_deg"]) == (249.0, 4)
    assert top["url"] == "https://earthquake.usgs.gov/earthquakes/eventpage/usp000eunq"
    assert d["by_decade"] == [{"decade": 2000, "count": 4, "early": False, "partial": False}]
    # as_of is the probe value STORED with the cell, never a later probe
    assert out["as_of"] == "2026-10-27T10:31:00Z" and out["as_of_kind"] == "through"
    assert "also" not in out["source"]


def test_kathmandu_drops_the_landslide_row_and_shares_iscgem_rows_alike() -> None:
    text = _csv("usgs_csv_all_kathmandu.csv")
    assert ",landslide," in text
    q = history.parse_usgs(text, THROUGH)
    assert len(q.ids) == 380  # 381 rows, one landslide
    assert "us7000tbwb" not in q.ids
    block = history.quake_block(q, ctx_for(27.717, 85.324, NOW))
    out = block.public(NOW, "en")
    assert out["data"]["strongest"][0]["id"] == "iscgem904745"  # 1934 M8.0
    assert out["data"]["strongest"][0]["time"].startswith("1934-01-15")
    assert out["source"]["also"][0]["licence"] == "CC BY-SA 3.0"
    assert "CC BY-SA 3.0" in out["source"]["licence"]
    early = [d for d in out["data"]["by_decade"] if d["decade"] < 1980]
    assert early and all(d["early"] for d in early)
    current = [d for d in out["data"]["by_decade"] if d["decade"] == 2020]
    assert current and current[0]["partial"] is True


def test_header_only_answer_is_zero_events_not_an_error() -> None:
    text = _csv("usgs_csv_all_midpacific.csv")
    assert text.count("\n") == 1
    q = history.parse_usgs(text, THROUGH)
    d = history.quake_block(q, ctx_for(0.0, -160.0, NOW)).data
    assert d is not None and d["total"] == 0 and d["strongest"] == []


def test_pre_1970_times_are_kept() -> None:
    q = history.parse_usgs(_csv("usgs_csv_all_kathmandu.csv"), THROUGH)
    oldest = min(q.t)
    assert oldest < 0  # before the epoch
    assert q.times[list(q.t).index(oldest)].startswith("1934")


def test_radius_is_applied_locally() -> None:
    q = history.parse_usgs(_csv("usgs_csv_all_kathmandu.csv"), THROUGH)
    full = history.quake_block(q, ctx_for(27.717, 85.324, NOW, radius=300)).data
    near = history.quake_block(q, ctx_for(27.717, 85.324, NOW, radius=100)).data
    assert full is not None and near is not None and 0 < near["total"] < full["total"]


async def test_the_probe_value_travels_with_the_cell() -> None:
    router = install()
    probe = "newest"
    newest_csv = _csv("usgs_csv_all_samui.csv")  # its first row is the newest at Samui
    router.add(history.USGS_NEWEST, newest_csv)
    clat, clon = 9.55, 100.05
    router.add(
        history.USGS_QUERY.format(lat=clat, lon=clon, km=history.USGS_RADIUS_KM),
        _csv("usgs_csv_all_samui.csv"),
    )
    block = await history.build_earthquakes(ctx_for(9.512, 100.013, NOW))
    assert block.status == "ok" and probe
    assert block.as_of == at("2006-10-07T21:12:26.370Z")


# ---------------------------------------------------------------------- NCEI


def _rows(name: str) -> history.Rows:
    payload = read_json("history", name)
    return history.Rows(payload["items"], THROUGH, payload["totalItems"], True)


def test_tsunami_deaths_are_the_tsunamis_own() -> None:
    events = _rows("ncei_tsunamis_events_tokyo.json")
    runups = history.Rows([], THROUGH, 0, True)
    block = history.tsunami_block(events, runups, ctx_for(35.676, 139.650, NOW))
    d = block.data
    assert d is not None
    kanto = [
        e for e in d["sources"]["events"] if e["name"] == "SAGAMI BAY" and e["date"]["year"] == 1923
    ]
    assert kanto, "Kanto 1923 is among the deadliest tsunamis within 300 km of Tokyo"
    assert kanto[0]["deaths"] == 325 and kanto[0]["deaths_total"] == 105385
    assert kanto[0]["cause"] == "earthquake" and kanto[0]["validity"] == "definite"
    s = d["sources"]
    assert s["total"] == s["kept"] + s["excluded_doubtful"]
    assert all(e["validity"] in ("probable", "definite") for e in s["events"])


def test_tsunami_runups_are_scoped_to_100_km_at_most() -> None:
    events = _rows("ncei_tsunamis_events_midpacific.json")
    runups = _rows("ncei_tsunamis_runups_lima.json")
    wide = history.tsunami_block(events, runups, ctx_for(-12.046, -77.043, NOW, radius=300)).data
    narrow = history.tsunami_block(events, runups, ctx_for(-12.046, -77.043, NOW, radius=20)).data
    assert wide is not None and narrow is not None
    assert wide["runups"]["within_km"] == 100 and narrow["runups"]["within_km"] == 20
    assert 0 < narrow["runups"]["total"] < wide["runups"]["total"]
    top = wide["runups"]["top"]
    assert top == sorted(top, key=lambda r: r["height_m"] or -1, reverse=True)


def test_empty_tsunami_answer_at_sea() -> None:
    events = _rows("ncei_tsunamis_events_midpacific.json")
    d = history.tsunami_block(
        events, history.Rows([], THROUGH, 0, True), ctx_for(0.0, -160.0, NOW)
    ).data
    assert d is not None and d["sources"]["total"] == 0


def test_significant_earthquakes_with_null_month_and_day() -> None:
    block = history.significant_block(
        _rows("ncei_earthquakes_kathmandu.json"), ctx_for(27.717, 85.324, NOW)
    )
    d = block.data
    assert d is not None and d["total"] >= 10
    top = d["events"][0]
    assert top["date"] == {"year": 1934, "month": 1, "day": 15} and top["deaths"] == 10600
    dates = [e["date"] for e in d["events"]]
    assert all(set(x) == {"year", "month", "day"} for x in dates)
    gorkha = [e for e in d["events"] if e["date"]["year"] == 2015 and e["date"]["month"] == 4]
    assert gorkha and gorkha[0]["deaths"] == 8957


def test_eruptions_drop_eruption_false_and_ignore_publish() -> None:
    rows = _rows("ncei_volcanoes_reykjavik.json")
    assert sum(1 for i in rows.items if i.get("eruption") is False) == 1
    assert not any(i.get("publish") for i in rows.items)
    block = history.eruption_block(rows, ctx_for(64.147, -21.942, NOW))
    d = block.data
    assert d is not None
    within = [i for i in rows.items if i.get("eruption") is True]
    assert 0 < d["total"] <= len(within)


def test_newest_entry_probes() -> None:
    import httpx

    eq = httpx.Response(
        200, content=(FIXTURES / "history" / "ncei_earthquakes_newest_global.json").read_bytes()
    )
    assert history._ncei_newest(eq) == at("2026-10-22T14:33:00Z")
    ts = httpx.Response(
        200, content=(FIXTURES / "history" / "ncei_tsunamis_events_newest_global.json").read_bytes()
    )
    assert history._ncei_newest(ts) == at("2026-10-09T17:56:00Z")


def test_cause_and_validity_lists_are_ncei_own() -> None:
    spec = read_json("history", "ncei_openapi.json")
    params = spec["paths"]["/hazard-service/api/v1/tsunamis/events"]["get"]["parameters"]
    text = {p["name"]: re.sub(r"\s+", " ", p.get("description", "")) for p in params}
    causes = dict(re.findall(r"<li>(\d+) - ([^<]+)</li>", text["causeCode"]))
    assert sorted(int(k) for k in causes) == sorted(history.CAUSES)
    for code, label in causes.items():
        words = re.sub(r"[^a-z ]", "", label.lower()).replace(" and ", " ").split()
        assert all(w in history.CAUSES[int(code)].split("_") for w in words if w != "and"), label
    validity = dict(re.findall(r"<li>(-?\d+) - ([^<]+)</li>", text["minEventValidity"]))
    assert sorted(int(k) for k in validity) == sorted(history.VALIDITY)
    assert history.KEEP_VALIDITY == 3 and "probable" in validity["3"]


def test_boxes_widen_clamp_and_split() -> None:
    boxes = history.ncei_boxes(0.05, 179.95, 300 + history.HALF_DIAGONAL_KM)
    assert len(boxes) == 2
    polar = history.ncei_boxes(89.95, 0.05, 300)
    assert polar[0][1] == 90.0 and (polar[0][2], polar[0][3]) == (-180.0, 180.0)


# ----------------------------------------------------------------------- GVP


def test_volcanoes_nearest_first_with_bce_and_unknown_years() -> None:
    rows = [
        f["properties"]
        for f in read_json("history", "gvp_holocene_volcanoes_tokyo.json")["features"]
    ]
    cont = history.parse_continuing(read_json("history", "gvp_continuing_eruptions_global.json"))
    assert cont.through == at("2026-03-31T00:00:00Z") or cont.through.year == 2026
    block = history.volcano_block(history.Volcanoes(rows, cont), ctx_for(35.676, 139.650, NOW))
    out = block.public(NOW, "en")
    v = out["data"]["volcanoes"]
    assert v == sorted(v, key=lambda x: x["distance_km"])
    years = {x["name"]: x["last_eruption_year"] for x in v}
    assert years.get("Shiga", "absent") in (None, "absent")  # unknown stays null
    all_years = {r["Volcano_Name"]: r["Last_Eruption_Year"] for r in rows}
    assert all_years["Myokosan"] == -750  # BCE is negative
    assert not any(x["continuing"] for x in v)
    assert out["as_of"] == cont.through.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_continuing_needs_the_modifier() -> None:
    payload = read_json("history", "gvp_continuing_eruptions_global.json")
    cont = history.parse_continuing(payload)
    assert 352010 in cont.numbers  # Reventador, '>' 2026-03-25
    assert len(cont.numbers) == 41


def test_no_volcano_near_samui() -> None:
    cont = history.parse_continuing(read_json("history", "gvp_continuing_eruptions_global.json"))
    rows = read_json("history", "gvp_holocene_volcanoes_samui.json")["features"]
    d = history.volcano_block(history.Volcanoes(rows, cont), ctx_for(9.512, 100.013, NOW)).data
    assert d is not None and d["total"] == 0


# ------------------------------------------------------------------ IBTrACS


@pytest.fixture(scope="module")
def gay_index(tmp_path_factory: pytest.TempPathFactory) -> cyclones.Index:
    """The ALL file's header and units rows, then GAY 1989's rows, as served
    by two range reads of the same file."""
    folder = tmp_path_factory.mktemp("ibtracs")
    csv_path = folder / "all.csv"
    csv_path.write_bytes(
        (FIXTURES / "history" / "ibtracs_ALL_range_header.csv").read_bytes()
        + (FIXTURES / "history" / "ibtracs_ALL_range_GAY1989.csv").read_bytes()
    )
    out = folder / cyclones.INDEX_NAME
    head = cyclones.build_index(str(csv_path), str(out), "Sun, 25 Oct 2026 09:05:37 GMT")
    assert head["storms"] == 1 and head["since_season"] == 1989
    index = cyclones.load_index(str(out))
    assert index is not None
    return index


def test_units_row_is_not_a_storm_and_blank_is_a_space(gay_index: cyclones.Index) -> None:
    assert list(gay_index.a["sid"]) == ["1989305N07105"]
    assert int(gay_index.storm_max_usa[0]) == 140
    assert int(gay_index.a["wmo"].min()) == -1  # ' ' -> missing


def test_gay_near_ko_samui(gay_index: cyclones.Index) -> None:
    d = cyclones.query(gay_index, 9.51, 100.01, 300, 2026)
    assert d["total_storms"] == 1 and d["tropical"] == 1 and d["post_tropical_only"] == 0
    storm = d["strongest"][0]
    assert storm["name"] == "GAY" and storm["season"] == 1989
    assert storm["wind_source"] == "USA_WIND, 1-min"
    assert storm["lifetime_max_wind_kt"] == 140
    assert storm["max_wind_within_radius_kt"] is not None
    assert storm["max_wind_within_radius_kt"] <= storm["lifetime_max_wind_kt"]
    assert storm["provisional"] is False
    assert d["by_decade"] == [{"decade": 1980, "count": 1, "early": False, "partial": False}]
    assert d["file_modified"] == "2026-10-25T09:05:37Z"


def test_closest_approach_is_measured_to_segments(gay_index: cyclones.Index) -> None:
    from app.place import geo

    a = gay_index.a
    d = cyclones.query(gay_index, 9.51, 100.01, 300, 2026)
    by_points = min(
        geo.haversine_km(9.51, 100.01, float(la), float(lo))
        for la, lo in zip(a["lat"], a["lon"], strict=True)
    )
    assert d["strongest"][0]["closest_km"] <= round(by_points)


def test_outside_the_radius_nothing(gay_index: cyclones.Index) -> None:
    d = cyclones.query(gay_index, 35.68, 139.65, 300, 2026)
    assert d["total_storms"] == 0 and d["strongest"] == []


@pytest.fixture(scope="module")
def active_index(tmp_path_factory: pytest.TempPathFactory) -> cyclones.Index:
    folder = tmp_path_factory.mktemp("ibtracs_active")
    out = folder / cyclones.INDEX_NAME
    cyclones.build_index(
        str(FIXTURES / "history" / "ibtracs_ACTIVE.list.v04r01.csv"),
        str(out),
        "Sun, 25 Oct 2026 09:05:07 GMT",
    )
    index = cyclones.load_index(str(out))
    assert index is not None
    return index


def test_provisional_tracks_are_kept_with_their_nr_points(active_index: cyclones.Index) -> None:
    assert len(active_index.a["sid"]) == 7
    assert all(active_index.a["provisional"])
    assert cyclones.NATURES.index("NR") in set(active_index.a["nature"].tolist())


def test_an_extratropical_remnant_is_post_tropical(active_index: cyclones.Index) -> None:
    a = active_index.a
    et = cyclones.NATURES.index("ET")
    storm = a["storm"]
    # a point inside an extratropical stretch of a track: ET before and after
    n = a["nature"]
    inner = next(
        i
        for i in range(1, len(storm) - 1)
        if storm[i - 1] == storm[i] == storm[i + 1] and n[i - 1] == n[i] == n[i + 1] == et
    )
    d = cyclones.query(active_index, float(a["lat"][inner]), float(a["lon"][inner]), 10, 2026)
    assert d["total_storms"] == 1 and d["post_tropical_only"] == 1
    assert d["strongest"][0]["post_tropical"] is True


def test_index_header_version_mismatch_is_rebuilt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / cyclones.INDEX_NAME
    import numpy as np

    with open(path, "wb") as f:
        np.savez(f, head=np.asarray(json.dumps({"format": cyclones.FORMAT + 1})))
    assert cyclones.load_index(str(path)) is None
