"""Meteoalarm replaces a warning under a NEW identifier that `references` the
old one. Fixtures: `fixtures/meteoalarm_references_2026-10-27.json`, two
warnings copied unmodified from the live country feeds of 2026-10-27."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.sources.alerts_world import MeteoalarmSource, meteoalarm_references, parse_meteoalarm

REAL = json.loads(
    (Path(__file__).parent / "fixtures" / "meteoalarm_references_2026-10-27.json").read_text(
        encoding="utf-8"
    )
)


def test_references_are_read_as_sender_identifier_sent_triples():
    warning = REAL["allclear"]["warning"]
    (replaced,) = meteoalarm_references(warning)
    assert replaced.startswith("2.49.0.0.250.0.FR.")
    assert replaced != warning["alert"]["identifier"]


def test_a_level_1_allclear_lifts_the_warning_it_references():
    """Meteo-France lifts with an AllClear at level 1 green: below the orange
    threshold, so it used to be dropped before anyone read its references,
    and the orange it lifted stayed on every client."""
    entry = REAL["allclear"]
    event = parse_meteoalarm(entry["warning"], entry["country"])
    assert event is not None and event.alert == "lifted"
    source = MeteoalarmSource(min_level=3)
    assert source.select({"warnings": [entry["warning"]]}, entry["country"]) == []
    (replaced,) = meteoalarm_references(entry["warning"])
    assert source.lifts == [f"meteoalarm:{replaced}"]


def test_an_update_withdraws_its_predecessor_and_is_published_itself():
    entry = REAL["update"]
    warning = entry["warning"]
    source = MeteoalarmSource(min_level=3)
    before_expiry = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
    (published,) = source.select({"warnings": [warning]}, entry["country"], now=before_expiry)
    assert published.source_id == warning["alert"]["identifier"]
    (replaced,) = meteoalarm_references(warning)
    assert source.expirations == [f"meteoalarm:{replaced}"]
    assert source.lifts == []


def test_a_warning_with_no_references_withdraws_nothing():
    warning = json.loads(json.dumps(REAL["update"]["warning"]))
    warning["alert"].pop("references", None)
    source = MeteoalarmSource(min_level=3)
    before_expiry = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
    source.select({"warnings": [warning]}, REAL["update"]["country"], now=before_expiry)
    assert source.lifts == [] and source.expirations == []
