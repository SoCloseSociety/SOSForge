"""Round 2, lane P -- what a restart gives back, and what the journal costs.

Measured on the real journal of 2026-10-26 (3 h, 3535 lines, every source),
restarting five minutes after its last line: the replay restored 679 of the
1347 events the running store held. `read_journal` skipped every event whose
`time` was more than 24 h old, while the pipeline's own horizon is three
days and exempts ongoing alerts -- so 362 of 499 alerts (227 Meteoalarm
warnings, 75 GDACS, 20 EONET...) left the map until their source's next
poll, and the quakes between 24 h and 3 days old left it for good. The UI
offers "24 h" and "all": after a deploy, "all" silently meant "24 h".

And every journal line carried `raw`, the verbatim upstream payload: 19.4 %
of the live journal's bytes, never read back from a restored event.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app import pipeline as pipeline_module
from app.dedupe import Deduper
from app.models.event import Event, Kind, Severity, utcnow
from app.pipeline import Pipeline
from app.store.ring import EventStore


@pytest.fixture(autouse=True)
def quiet_hub(monkeypatch) -> None:
    async def nowhere(message: dict) -> None:
        return None

    monkeypatch.setattr(pipeline_module.hub, "broadcast", nowhere)


def event(event_id: str, **kw) -> Event:
    source, source_id = event_id.split(":", 1)
    base = {
        "id": event_id,
        "source": source,
        "source_id": source_id,
        "kind": Kind.EARTHQUAKE,
        "time": utcnow() - timedelta(minutes=5),
        "lat": 38.3,
        "lon": 142.4,
        "magnitude": 4.6,
        "place": "off Miyagi",
        "severity": Severity.MODERATE,
        "title": "M4.6 off Miyagi",
        "raw": {"properties": {"verbatim": "x" * 400}},
    }
    base.update(kw)
    return Event(**base)


async def run_then_restart(tmp_path, *events: Event) -> EventStore:
    """Ingest through a live pipeline that journals, then replay that journal
    into a fresh process, the way the lifespan does."""
    live = Pipeline(EventStore(data_dir=tmp_path), Deduper())
    for e in events:
        await live.emit(e)
    restarted = EventStore(data_dir=tmp_path)
    pipeline = Pipeline(restarted, Deduper())
    for journal in sorted(tmp_path.glob("events-*.jsonl")):
        await pipeline.replay(journal)
    return restarted


class TestARestartGivesBackWhatTheStoreHeld:
    async def test_an_ongoing_alert_dated_a_month_ago_comes_back(self, tmp_path):
        """A GDACS drought open since last month, re-listed every cycle: the
        horizon exempts it live, so a restart must not drop it."""
        drought = event(
            "gdacs:DR1",
            kind=Kind.DROUGHT,
            time=utcnow() - timedelta(days=30),
            lat=None,
            lon=None,
            magnitude=None,
            severity=Severity.SEVERE,
            ongoing=True,
        )
        store = await run_then_restart(tmp_path, drought)
        assert store.get("gdacs:DR1") is not None, "an alert in force vanished at the restart"

    async def test_a_quake_two_days_old_comes_back(self, tmp_path):
        quake = event("geofon:q2", time=utcnow() - timedelta(days=2))
        store = await run_then_restart(tmp_path, quake)
        assert store.get("geofon:q2") is not None, (
            "the live store kept it (3-day horizon), the restart dropped it (24 h)"
        )

    async def test_a_quake_past_the_horizon_does_not(self, tmp_path):
        """Journaled while it was current, older than the horizon now: the
        pipeline's rule decides, the same one that applies live."""
        old = event("geofon:q4", time=utcnow() - timedelta(days=4))
        line = {"action": "new", **old.model_dump(mode="json")}
        (tmp_path / f"events-{utcnow():%Y-%m-%d}.jsonl").write_text(json.dumps(line) + "\n")
        store = EventStore(data_dir=tmp_path)
        await Pipeline(store, Deduper()).replay(next(tmp_path.glob("*.jsonl")))
        assert store.get("geofon:q4") is None

    async def test_an_alert_that_expired_since_it_was_journaled_does_not(self, tmp_path):
        ended = event(
            "nws:w1",
            kind=Kind.STORM,
            severity=Severity.SEVERE,
            ongoing=True,
            expires=utcnow() - timedelta(minutes=1),
        )
        line = {"action": "new", **ended.model_dump(mode="json")}
        (tmp_path / f"events-{utcnow():%Y-%m-%d}.jsonl").write_text(json.dumps(line) + "\n")
        store = EventStore(data_dir=tmp_path)
        await Pipeline(store, Deduper()).replay(next(tmp_path.glob("*.jsonl")))
        assert store.get("nws:w1") is None

    async def test_a_line_cut_by_a_crash_costs_that_line_only(self, tmp_path):
        await run_then_restart(tmp_path, event("usgs:a"), event("usgs:b", lat=10.0))
        journal = next(tmp_path.glob("events-*.jsonl"))
        with journal.open("a", encoding="utf-8") as fh:
            fh.write('{"action": "new", "id": "usgs:c", "sou')  # power cut mid-write
        store = EventStore(data_dir=tmp_path)
        assert await Pipeline(store, Deduper()).replay(journal) == 2


class TestTheJournalCarriesNoRawPayload:
    async def test_a_line_has_no_raw(self, tmp_path):
        await run_then_restart(tmp_path, event("usgs:r1"))
        line = json.loads(next(tmp_path.glob("events-*.jsonl")).read_text().splitlines()[0])
        assert "raw" not in line
        assert line["id"] == "usgs:r1"

    async def test_the_restored_event_is_the_one_clients_were_shown(self, tmp_path):
        live = Pipeline(EventStore(data_dir=tmp_path), Deduper())
        original = event("usgs:r2", felt_reports=12, intensity_mmi=4.1)
        await live.emit(original)
        shown = live.store.get("usgs:r2").public()

        restarted = EventStore(data_dir=tmp_path)
        await Pipeline(restarted, Deduper()).replay(next(tmp_path.glob("*.jsonl")))
        assert restarted.get("usgs:r2").public() == shown
