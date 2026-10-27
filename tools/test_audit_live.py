"""Tests for tools/audit_live.py, on records really served on 2026-10-27.

Run: backend/.venv/bin/python -m pytest tools/test_audit_live.py -q

The fixture (audit_live_fixture.json) is verbatim: every record is what
production or the branch returned on that morning, saved by `--save`. Each
test pins one defect the audit reported, so a check that silently stops
firing is caught here before it is trusted on the next deploy.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("audit_live", HERE / "audit_live.py")
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_live"] = audit
spec.loader.exec_module(audit)

FIX = json.loads((HERE / "audit_live_fixture.json").read_text(encoding="utf-8"))


def snapshot(
    name: str,
    server_time: str,
    events: list[dict],
    sources: list[dict],
    uptime: float | None = None,
):
    raw = {
        "healthz": {"uptime_seconds": uptime} if uptime else {},
        "sources": {"sources": sources},
        "feed": {"server_time": server_time, "events": events},
        "quakes_24h": {
            "server_time": server_time,
            "events": [e for e in events if e["kind"] == "earthquake"],
        },
    }
    return audit.build_snapshot(name, raw)


def test_the_page_count_claim_is_read_from_the_real_sentence():
    claims = audit._claimed_counts(FIX["prod_index_sentence"])
    assert [n for _, n in claims] == [26]


def test_number_words_cover_the_counts_the_pages_have_used():
    for text, n in (
        ("nineteen official sources", 19),
        ("Thirty-two official sources", 32),
        ("28 feeds", 28),
    ):
        assert audit._claimed_counts(text)[0][1] == n


def test_a_deep_emsc_quake_rated_on_magnitude_alone_is_over_graded():
    e = FIX["prod_deep_emsc"]  # M4.2 at 181 km, served as "minor" by production
    assert e["severity"] == "minor"
    assert audit.expected_quake_severity(e["magnitude"], e["depth_km"], e.get("alert")) == "info"


def test_identical_emsc_and_ingv_solutions_shown_twice_are_counted():
    a, b = FIX["prod_twins"]
    assert (a["time"], a["lat"], a["lon"], a["magnitude"]) == (
        b["time"],
        b["lat"],
        b["lon"],
        b["magnitude"],
    )
    snap = snapshot("prod", FIX["prod_server_time"], [a, b], [])
    _, summary, defects = audit.precision(snap)
    assert summary["groups_split"] == 1 and summary["extra_primaries"] == 1
    assert defects[0].check == "duplicate_quake_on_screen"


def test_a_meteoalarm_all_clear_is_caught_as_a_cancellation():
    e = FIX["prod_meteoalarm_cancel"]
    snap = snapshot("prod", FIX["prod_server_time"], [e], [])
    _, defects = audit.validity(snap, [])
    assert "drill_or_cancelled" in {d.check for d in defects}


def test_the_footer_last_event_of_bmkg_is_not_a_new_event():
    snap = snapshot(
        "branch",
        FIX["branch_server_time"],
        FIX["branch_bmkg_events"],
        [FIX["branch_bmkg_health"]],
        FIX["branch_uptime_seconds"],
    )
    fresh = audit.freshness(snap)
    rows, defects = audit.fake_checks(snap, fresh)
    hit = [d for d in defects if d.check == "footer_last_event_not_new"]
    assert hit and hit[0].source == "bmkg"
    # the newest BMKG quake is from 2026-10-26 04:53, about 29 h before the read
    assert 28 * 3600 < fresh["bmkg"]["newest_origin_age"] < 30 * 3600


def test_a_gdacs_event_marked_not_current_is_no_reference():
    feature = FIX["gdacs_over_drought"]
    # a STRING in the GDACS API, not a JSON boolean
    assert feature["properties"]["iscurrent"] == "false"
    snap = snapshot("prod", FIX["prod_server_time"], [], [])
    items = audit.missing([snap], {"gdacs_orange_red": {"features": [feature]}})
    assert items == []
