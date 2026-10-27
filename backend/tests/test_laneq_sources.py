"""Lane Q's live audit (2026-10-27), source side. Fixtures: the real objects in
`fixtures/laneq_2026-10-27.json`, and the real PHIVOLCS rows of
`test_phivolcs.py`. Where a test needs a second identity for one quake (the
INGV re-identification), it is derived from the real record in one visible
line, and the live service's 302 from the old id to the new one is the proof
that the two are the same quake."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.sources.base import SourceHealth
from app.sources.eew import JmaEewSource
from app.sources.nws_zones import ZoneResolver, _centroid_of
from app.sources.phivolcs import PhivolcsSource
from app.sources.regional import IngvSource, ingv_country
from app.sources.usgs import UsgsSource, usgs_aliases
from app.sources.volcano import VolcanoSource
from tests.test_phivolcs import NOW as PHIVOLCS_NOW
from tests.test_phivolcs import PHIVOLCS_ROWS

FX = Path(__file__).parent / "fixtures"
REAL = json.loads((FX / "laneq_2026-10-27.json").read_text(encoding="utf-8"))
AUDIT6 = json.loads((FX / "audit6_2026-10-26.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ PHIVOLCS


def _phivolcs_events():
    return PhivolcsSource().parse_payload(PHIVOLCS_ROWS, now=PHIVOLCS_NOW)


def test_a_bulletin_that_left_the_page_inside_the_window_is_withdrawn():
    events = _phivolcs_events()
    assert len(events) >= 2
    source = PhivolcsSource()
    assert source.superseded(events, PHIVOLCS_NOW) == []  # first read: the baseline
    replaced = events[0]
    assert source.superseded(events[1:], PHIVOLCS_NOW) == [replaced.id]


def test_a_bulletin_that_aged_out_of_the_window_is_not_withdrawn():
    events = _phivolcs_events()
    source = PhivolcsSource()
    source.superseded(events, PHIVOLCS_NOW)
    oldest = min(events, key=lambda e: e.time)
    later = oldest.time + timedelta(days=source.window_days)  # it has just aged out
    remaining = [e for e in events if e.id != oldest.id]
    assert oldest.id not in source.superseded(remaining, later)


def test_a_short_page_withdraws_nothing():
    events = _phivolcs_events()
    source = PhivolcsSource()
    source.superseded(events, PHIVOLCS_NOW)
    assert source.superseded(events[:0], PHIVOLCS_NOW) == []


# ---------------------------------------------------------------------- USGS


def test_usgs_aliases_are_withdrawn():
    feature = REAL["usgs_feature_with_aliases"]
    assert usgs_aliases(feature) == ["aka2026vgyfgb"]
    source = UsgsSource(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
    )
    source.withdraw_aliases([feature])
    assert source.expirations == ["usgs:aka2026vgyfgb"]
    assert source.alias_url.endswith("all_day.geojson")


# ---------------------------------------------------------------------- INGV


def test_an_ingv_reidentified_event_withdraws_the_old_id():
    new = REAL["ingv_feature_47630362"]
    old = json.loads(json.dumps(new))
    old["properties"]["eventId"] = 46940061  # what eventid=46940061 302s to
    source = IngvSource()
    source.parse_payload({"features": [old]})
    (event,) = source.parse_payload({"features": [new]})
    assert event.id == "ingv:47630362"
    assert source.expirations == ["ingv:46940061"]


TWO_SOLUTIONS = json.loads((FX / "ingv_two_solutions_2026-10-27.json").read_text(encoding="utf-8"))
TWO_SOLUTION_PAGE = {
    "features": TWO_SOLUTIONS["sefro_2026-10-07T00:40"]["features"]
    + TWO_SOLUTIONS["eolie_2026-10-18T13:25"]["features"]
}


def test_two_ingv_solutions_listed_together_are_both_kept_every_cycle():
    # PR #5 review: each withdrew the other from the second cycle on, and the
    # quake came back as a new event every 60 s
    source = IngvSource()
    for _cycle in range(3):
        events = source.parse_payload(TWO_SOLUTION_PAGE)
        assert sorted(e.id for e in events) == [
            "ingv:47459342",
            "ingv:47459372",
            "ingv:47569772",
            "ingv:47569802",
        ]
        assert source.expirations == []


def test_an_ingv_solution_the_page_stops_listing_is_withdrawn():
    source = IngvSource()
    source.parse_payload(TWO_SOLUTION_PAGE)
    # the same page once the catalogue folds 47569772 into 47569802
    page = {
        "features": [
            f for f in TWO_SOLUTION_PAGE["features"] if f["properties"]["eventId"] != 47569772
        ]
    }
    source.parse_payload(page)
    assert source.expirations == ["ingv:47569772"]


def test_ingv_stamps_italy_only_on_an_italian_province():
    places = REAL["ingv_places"]
    assert ingv_country("7 km NE Lauria (PZ)") == "Italy"
    assert ingv_country("Vanuatu Islands [Sea: Vanuatu]") is None
    assert ingv_country("Tirreno Meridionale [Mare]") is None
    assert ingv_country("China [Land]") is None
    # on the real feed, every "(XX)" place is a real province
    for place in places:
        if place.endswith(")") and place[-4] == "(" and place[-3:-1].isupper():
            assert ingv_country(place) == "Italy", place


# ----------------------------------------------------------------------- NWS


def test_a_zone_across_the_antimeridian_is_placed_in_the_bering_sea():
    zone = REAL["nws_zone_PKZ767"]
    lat, lon = _centroid_of(zone["geometry"])
    # production drew it at 59.99 N 26.48 W, south-east of Greenland
    assert abs(lat - 59.99) < 0.01
    assert -180 <= lon < -170


def test_a_zone_cache_from_before_the_fix_is_discarded(tmp_path):
    path = tmp_path / "nws-zones.json"
    url = "https://api.weather.gov/zones/forecast/PKZ767"
    path.write_text(json.dumps({url: [59.99, -26.48]}), encoding="utf-8")
    resolver = ZoneResolver(path)
    assert resolver.known(url) is None and resolver.has(url) is False


# --------------------------------------------------------------- freshness


def test_a_relisted_event_is_not_new_data():
    health = SourceHealth("bmkg")
    quake_time = datetime(2026, 10, 26, 4, 53, 42, tzinfo=UTC)
    health.observe("bmkg:2026-10-26T04:53:42+00:00", quake_time)
    health.ok(1)  # first cycle: the baseline
    snap = health.snapshot()
    assert snap["last_event_at"] is None
    assert snap["newest_event_time"] == quake_time.isoformat()
    assert snap["last_delivery_at"] is not None
    health.observe("bmkg:2026-10-26T04:53:42+00:00", quake_time)  # re-listed
    health.ok(1)
    assert health.snapshot()["last_event_at"] is None
    health.observe("bmkg:new", datetime.now(UTC))  # a genuinely new quake
    assert health.snapshot()["last_event_at"] is not None


# --------------------------------------------------------- preliminary, flags


def test_an_early_warning_is_preliminary():
    (event,) = JmaEewSource().parse_payload(AUDIT6["jma_eew_latest"])
    assert event.preliminary is True


def test_a_northern_mariana_volcano_gets_its_own_flag():
    source = VolcanoSource()
    rows = {row["volcano_name"]: row for row in REAL["hans_elevated"]}
    ahyi = source.parse(rows["Ahyi Seamount"])
    assert ahyi is not None and ahyi.country_code == "MP"
    kilauea = source.parse(rows["Kilauea"])
    assert kilauea is not None and kilauea.country_code is None  # resolved downstream


# ------------------------------------------------------------------- GDACS


def test_a_green_gdacs_quake_is_graded_on_its_magnitude_too():
    import xml.etree.ElementTree as ET

    from app.models.event import Severity
    from app.sources.gdacs import parse_item

    root = ET.parse(FX / "gdacs_2026-10-26_green_quake.xml").getroot()
    (item,) = root.findall(".//item")
    event = parse_item(item)
    assert event is not None
    # green M5.5 at 641.8 km: GDACS's colour said MINOR, the depth rule says
    # MODERATE (a deep-focus quake is capped there, never raised above it)
    assert event.magnitude == 5.5
    assert event.severity is Severity.MODERATE


# ------------------------------------------------------------------ memory


def test_phivolcs_and_ingv_remember_across_a_restart(tmp_path, monkeypatch):
    from app.sources.base import Source

    monkeypatch.setattr(Source, "memory_path", lambda self: tmp_path / f"memory-{self.name}.json")
    ingv = IngvSource()
    ingv.parse_payload({"features": [REAL["ingv_feature_47630362"]]})
    ingv.persist()
    restarted = IngvSource()
    restarted.restore()
    assert "ingv:47630362" in restarted._emitted

    phivolcs = PhivolcsSource()
    phivolcs.superseded(_phivolcs_events(), PHIVOLCS_NOW)
    phivolcs.save_memory({"listed": {k: v.isoformat() for k, v in phivolcs._listed.items()}})
    assert set(PhivolcsSource().load_memory()["listed"]) == set(phivolcs._listed)
