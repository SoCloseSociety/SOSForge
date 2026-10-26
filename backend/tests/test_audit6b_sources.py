"""Audit 6, lane C, second batch: BMKG, ash SIGMETs, WMO expiry, area extents.

Fixtures: `fixtures/audit6b_2026-10-26.json` and
`fixtures/wmo_cap_no-met_2026-10-26.xml`, copied unmodified from the live
endpoints (the two SIGMETs from AWC's archive for 2026-10-25 18:00 UTC, the
window in which production's ash source was down).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.event import Event, Kind, Severity
from app.sources import geometry
from app.sources.alerts_world import WmoCapSource, parse_meteoalarm, parse_wmo
from app.sources.cap_area import CapAreaCache, parse_cap_area
from app.sources.hazards import AshSource, ash_points, ash_volcano_name
from app.sources.nws import parse_feature
from app.sources.nws_zones import ZoneResolver
from app.sources.regional import BmkgSource, bmkg_felt, bmkg_tsunami

FIXTURES = Path(__file__).parent / "fixtures"
REAL = json.loads((FIXTURES / "audit6b_2026-10-26.json").read_text(encoding="utf-8"))
CAP_NO_MET = (FIXTURES / "wmo_cap_no-met_2026-10-26.xml").read_text(encoding="utf-8")
# inside the validity of both archived SIGMETs
ASH_DURING = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)


# ------------------------------------------------------------------- BMKG


def _bmkg() -> list[Event]:
    return BmkgSource().parse_payload(
        {f: REAL[f"bmkg_{f}"] for f in ("gempaterkini", "autogempa", "gempadirasakan")}
    )


def test_bmkg_reads_the_felt_quakes_the_m5_list_never_had():
    events = _bmkg()
    ids = {e.source_id for e in events}
    # 15 M5+ and 15 felt, with overlaps, plus the latest: one event per quake
    assert len(ids) == len(events) == 27
    felt_only = next(e for e in events if e.source_id == "2026-10-25T12:47:26+00:00")
    assert felt_only.magnitude == 3.8
    assert felt_only.raw["feeds"] == ["gempadirasakan"]
    assert felt_only.alert == "felt MMI 2"
    # the "Pusat gempa berada di laut" sentence is stripped to the place
    assert felt_only.place == "13 km timur laut Kota Sorong"


def test_bmkg_one_quake_in_two_feeds_is_one_event_with_the_m5_list_fields():
    events = _bmkg()
    both = next(e for e in events if e.source_id == "2026-10-22T10:57:49+00:00")
    assert both.raw["feeds"] == ["gempaterkini", "gempadirasakan"]
    assert both.place == "6 km BaratLaut LEBONG-BENGKULU"
    assert both.raw["potensi"] == "Tidak berpotensi tsunami"


def test_bmkg_a_felt_notice_is_not_a_tsunami_potential():
    """autogempa reuses `Potensi` for "felt, pass it on to the public"."""
    events = _bmkg()
    latest = next(e for e in events if e.source_id == "2026-10-26T04:53:42+00:00")
    assert "dirasakan" in latest.raw["potensi"]
    assert latest.tsunami is False
    assert latest.severity is not Severity.EXTREME
    assert bmkg_tsunami("Gempa ini dirasakan untuk diteruskan pada masyarakat") is False
    assert bmkg_tsunami("Tidak berpotensi tsunami") is False
    assert bmkg_tsunami("Berpotensi tsunami") is True


def test_bmkg_felt_intensity_is_the_highest_roman_numeral():
    assert bmkg_felt("III Bajawa, III Maumere, II - III Ruteng") == 3
    assert bmkg_felt("IV-V Kab. Manggarai, III Kab. Manggarai Barat") == 5
    assert bmkg_felt(None) is None


def test_bmkg_single_feed_shape_still_parses():
    events = BmkgSource().parse_payload(REAL["bmkg_gempaterkini"])
    assert len(events) == 15


# ------------------------------------------------------------------- ash


def test_a_sigmet_with_two_rings_no_longer_takes_the_source_down():
    """15.6 h down on production: `coords` was a list of rings."""
    sigmet = REAL["ash_nzzo36_two_rings"]
    assert sigmet["geom"] == "AREAS"
    (event,) = AshSource().parse_payload([sigmet], now=ASH_DURING)
    assert event.title == "Volcanic ash -- Whakaari/White Island"
    # one ring has lon 180.3: the box crosses the antimeridian (west > east)
    assert event.bbox == (179.0, -38.283, -179.7, -37.333)
    assert event.lon is not None and -180 <= event.lon <= 180
    assert event.expires == datetime.fromtimestamp(sigmet["validTimeTo"], tz=UTC)


def test_a_one_ring_sigmet_still_parses():
    sigmet = REAL["ash_waaf10_one_ring"]
    (event,) = AshSource().parse_payload([sigmet], now=ASH_DURING)
    assert event.place == "Karangetang"
    assert event.bbox == (125.267, 2.717, 125.45, 2.85)
    assert len(ash_points(sigmet["coords"])) == 5


def test_the_volcano_name_comes_from_the_raw_text_when_the_qualifier_is_bare():
    sigmet = REAL["ash_nzzo36_two_rings"]
    assert sigmet["qualifier"] == "MT"
    assert ash_volcano_name("MT", sigmet["rawSigmet"]) == "Whakaari/White Island"
    assert ash_volcano_name("ERUPTION MT REVENTADOR", None) == "Reventador"
    assert ash_volcano_name("FUEGO", None) == "Fuego"


# ------------------------------------------------------------------- WMO


def test_wmo_maps_its_expiry():
    item = REAL["wmo_item_no_met"]
    event = parse_wmo(item)
    assert event is not None
    # "2026-10-27 13:00:00" in the aggregate == 13:00+00:00 in the CAP document
    assert event.expires == datetime(2026, 10, 27, 13, 0, tzinfo=UTC)


def test_wmo_withdraws_what_has_expired():
    item = REAL["wmo_item_no_met"]
    source = WmoCapSource(max_severity_rank=4)
    after = datetime(2026, 10, 27, 13, 0, 1, tzinfo=UTC)
    assert source.build_events([item], now=after) == []
    assert source.expirations == [f"wmo:{item['id']}"]


def test_a_cap_document_gives_its_extent():
    position, extent = parse_cap_area(CAP_NO_MET)
    assert position is not None
    assert extent == (5.0323, 62.1837, 5.446, 62.351)


def test_the_cap_cache_keeps_the_extent_and_rereads_legacy_entries(tmp_path):
    path = tmp_path / "wmo-cap.json"
    path.write_text(json.dumps({"a.xml": [62.2, 5.2], "b.xml": None}), encoding="utf-8")
    cache = CapAreaCache(path)
    # a position from before extents were kept is still used ...
    assert cache.known("a.xml") == (62.2, 5.2)
    # ... but is read again; an unplaceable document is not
    assert cache.has("a.xml") is False
    assert cache.has("b.xml") is True


# ------------------------------------------------------------- area extents


def test_an_nws_polygon_gets_its_extent():
    event = parse_feature(REAL["nws_polygon_feature"])
    assert event is not None
    assert event.bbox == (-103.66, 29.0, -103.4, 29.23)


def test_an_nws_zone_alert_gets_the_extent_of_its_zones():
    zones = ZoneResolver()
    url = "https://api.weather.gov/zones/forecast/PKZ412"
    zone = REAL["nws_zone_PKZ412"]
    # what `resolve()` stores for that zone document
    from app.sources.nws_zones import _centroid_of, _outer_points

    zones._centroids[url] = _centroid_of(zone["geometry"])
    zones._extents[url] = geometry.extent(_outer_points(zone["geometry"]))
    event = parse_feature(REAL["nws_zone_feature"], zones)
    assert event is not None
    assert event.kind is Kind.STORM
    assert event.bbox == (-180.0, 56.0445, -170.999, 59.0)


def test_an_nws_zone_alert_without_every_zone_known_has_no_extent():
    event = parse_feature(REAL["nws_zone_feature"], ZoneResolver())
    assert event is not None
    assert event.bbox is None


def test_a_uk_meteoalarm_polygon_gets_its_extent():
    event = parse_meteoalarm(REAL["meteoalarm_uk_polygon"], "united-kingdom")
    assert event is not None
    assert event.bbox == (-3.1554, 54.5984, -3.1465, 54.6022)


def test_extent_across_the_antimeridian():
    # GDACS writes such a box as lon -182 .. -174
    box = geometry.extent([(-22.283, -182.0109), (-14.283, -174.0109)])
    assert box == (177.9891, -22.283, -174.0109, -14.283)
    assert geometry.center([(0, 179.0), (0, -179.0)]) == (0.0, 180.0)


def test_the_model_refuses_an_impossible_box():
    with pytest.raises(ValueError):
        Event(
            id="x:1",
            source="x",
            source_id="1",
            time=datetime(2026, 10, 26, tzinfo=UTC),
            bbox=(0.0, 10.0, 1.0, 5.0),  # south above north
        )


def test_a_box_enters_the_fingerprint_and_its_absence_changes_nothing():
    base = Event(id="x:1", source="x", source_id="1", time=datetime(2026, 10, 26, tzinfo=UTC))
    boxed = base.model_copy(update={"bbox": (0.0, 0.0, 1.0, 1.0)})
    assert base.fingerprint() != boxed.fingerprint()
    assert "bbox" in boxed.public()


def test_an_expired_sigmet_is_withdrawn():
    sigmet = REAL["ash_waaf10_one_ring"]
    source = AshSource()
    later = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
    assert source.parse_payload([sigmet], now=later) == []
    assert source.expirations == ["ash:WAAF:10:1792930200"]
