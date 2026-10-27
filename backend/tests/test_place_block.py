"""The place report's envelope, its unknown values, its messages, and the
integrity of every fixture it is tested on."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.place import block as B
from app.place import messages
from tests.place_helpers import FIXTURES

SPEC = B.Spec(
    "test.block", "observation", "observed", 3600.0, B.Source("S", "https://x", "A", "CC BY 4.0")
)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_unavailable_never_carries_a_value() -> None:
    out = B.unavailable(SPEC, "timeout", "no answer from api.met.no within 10 s").public(NOW, "fr")
    assert out["status"] == "unavailable"
    assert out["data"] is None and out["as_of"] is None and out["stale"] is False
    assert out["error"]["code"] == "timeout"
    assert out["error"]["message"] == "La source n’a pas répondu à temps."


def test_ok_is_stale_past_its_max_age_and_by_its_own_rule() -> None:
    fresh = B.ok(SPEC, NOW - timedelta(minutes=59), {"v": 1})
    old = B.ok(SPEC, NOW - timedelta(minutes=61), {"v": 1})
    ruled = B.ok(SPEC, NOW, {"v": 1}, stale_rule=True)
    assert fresh.public(NOW, "en")["stale"] is False
    assert old.public(NOW, "en")["stale"] is True
    assert ruled.public(NOW, "en")["stale"] is True
    assert fresh.public(NOW, "en")["as_of"] == "2026-10-27T11:01:00Z"


def test_not_applicable_has_one_localized_sentence_with_its_distance() -> None:
    out = B.not_applicable(SPEC, "inland", "nearest ocean coastline 150 km", km=150).public(
        NOW, "en"
    )
    assert out["error"]["message"] == "The nearest ocean coastline is 150 km away."
    assert out["data"] is None


@pytest.mark.parametrize(
    "value", ["", " ", "MM", "NaN", "No Data", "NoData", None, -9999, -327.68, math.nan, "x"]
)
def test_every_sentinel_is_null(value: object) -> None:
    assert B.number(value) is None


@pytest.mark.parametrize("value", [0, 0.0, "0", "0.0"])
def test_a_real_zero_stays_zero(value: object) -> None:
    assert B.number(value) == 0.0


def test_every_code_has_a_sentence_in_every_language() -> None:
    for code in (*B.UNAVAILABLE, *B.NOT_APPLICABLE):
        assert code in messages.MESSAGES, code
    for key, sentences in messages.MESSAGES.items():
        assert set(sentences) == set(messages.LANGS), key
        for text in sentences.values():
            assert "—" not in text  # never an em dash


def test_attribution_year_is_the_data_year() -> None:
    assert B.years_of(datetime(2027, 1, 2, tzinfo=UTC)) == "2027"


def _manifests() -> list[Path]:
    return sorted(FIXTURES.glob("*/MANIFEST.json"))


def test_every_family_has_a_manifest() -> None:
    families = {p.parent.name for p in _manifests()}
    assert families == {"air", "history", "risk", "sea", "weather"}


@pytest.mark.parametrize("manifest", _manifests(), ids=lambda p: p.parent.name)
def test_every_fixture_matches_its_manifest(manifest: Path) -> None:
    listed = json.loads(manifest.read_text())["files"]
    assert listed
    for name, entry in listed.items():
        data = (manifest.parent / name).read_bytes()
        assert len(data) == entry["bytes"], name
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], name
    on_disk = {
        str(p.relative_to(manifest.parent))
        for p in manifest.parent.rglob("*")
        if p.is_file() and p.name != "MANIFEST.json"
    }
    assert on_disk == set(listed), "a fixture is on disk without a manifest entry, or the reverse"


def test_countries_table_is_the_frontend_one_byte_for_byte() -> None:
    root = Path(__file__).resolve().parents[2]
    backend = root / "backend" / "app" / "data" / "countries.json"
    frontend = root / "frontend" / "src" / "data" / "countries.json"
    assert backend.read_bytes() == frontend.read_bytes()


# ---------------------------------------------------------------- alerts_now


def _health(name: str, last_ok_ago_s: float | None) -> dict:
    from datetime import timedelta

    from app.sources.base import SourceHealth

    health = SourceHealth(name)
    if last_ok_ago_s is not None:
        health.ok()
        assert health.last_ok is not None
        health.last_ok -= timedelta(seconds=last_ok_ago_s)
        health.fail(TimeoutError("no answer"))  # failing ever since
    return health.snapshot()


def test_alerts_as_of_is_the_newest_source_poll_not_the_server_clock() -> None:
    from app.place import alerts, compose
    from tests.place_helpers import ctx_for

    now = datetime.now(UTC)
    ctx = ctx_for(9.512, 100.013, now)
    # every source stopped delivering two hours ago; the server still answers
    frozen = [_health("usgs", 7200.0), _health("emsc", 7300.0), _health("gdacs", None)]
    block = alerts.build(ctx, lambda: ([], frozen))
    body = compose.body(ctx, {"alerts_now": block}, True)
    out = body["blocks"]["alerts_now"]
    assert out["status"] == "ok" and out["as_of_kind"] == "live"
    assert out["as_of"] == frozen[0]["last_ok"][:19] + "Z"
    assert out["stale"] is True  # older than its 300 s
    assert out["data"]["sources_down"] == ["emsc", "gdacs", "usgs"]
    live = [_health("usgs", 4.0), _health("emsc", 7300.0)]
    fresh = compose.body(ctx, {"alerts_now": alerts.build(ctx, lambda: ([], live))}, True)
    assert fresh["blocks"]["alerts_now"]["stale"] is False


def test_alerts_warming_before_any_source_cycle() -> None:
    from app.place import alerts
    from tests.place_helpers import ctx_for

    ctx = ctx_for(9.512, 100.013, datetime.now(UTC))
    block = alerts.build(ctx, lambda: ([], [_health("usgs", None)]))
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "warming"
