"""Non-regressions from the adversarial audit of 2026-08-17.

Each test corresponds to a defect that had been REPRODUCED on the live system.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.dedupe import Deduper
from app.hub import QUEUE_MAX, Client, Hub
from app.models.event import Event, Kind, Severity
from app.pipeline import Pipeline
from app.sources.tsunami import TsunamiSource
from app.store.ring import EventStore


def quake(event_id: str, minutes_ago: float, lat: float = 10.0, lon: float = 20.0) -> Event:
    return Event(
        id=event_id,
        source=event_id.split(":")[0],
        source_id=event_id,
        kind=Kind.EARTHQUAKE,
        time=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        lat=lat,
        lon=lon,
        magnitude=4.0,
        place="somewhere",
    )


def alert(event_id: str) -> Event:
    return Event(
        id=event_id,
        source="nws",
        source_id=event_id,
        kind=Kind.FLOOD,
        time=datetime.now(UTC),
        lat=1.0,
        lon=1.0,
        place="zone",
        severity=Severity.SEVERE,
    )


def test_alert_repolls_no_longer_flush_the_dedup_window():
    """Defect 1. Alerts re-emitted every cycle (NWS, GDACS, tsunami:
    ~146/minute, measured) filled the deduper's history and evicted the EMSC
    entry before USGS published its own solution, 5 to 15 minutes later.
    """
    deduper = Deduper(history=50)
    emsc = quake("emsc:1", minutes_ago=1)
    deduper.assign(emsc)

    # the background noise: ten times the history's capacity
    for i in range(500):
        deduper.assign(alert(f"nws:{i}"))

    usgs = quake("usgs:1", minutes_ago=0.5)
    deduper.assign(usgs)

    assert usgs.cluster_id == emsc.cluster_id, "the EMSC/USGS dedup must survive the noise"


@pytest.mark.asyncio
async def test_a_source_whose_every_feed_failed_is_not_green():
    """Defect 2. `health.ok()` was called even when both tsunami centres were
    unreachable: the interface showed a healthy tsunami alert source when it
    was dead."""
    # two feeds on a closed port
    source = TsunamiSource(
        poll_seconds=0.01,
        feeds={"A": "http://127.0.0.1:9/a.xml", "B": "http://127.0.0.1:9/b.xml"},
    )

    async def emit(_: Event) -> None:  # pragma: no cover - never called
        raise AssertionError("no event can come out of a dead feed")

    task = asyncio.create_task(source.run(emit))
    await asyncio.sleep(0.4)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    snapshot = source.health.snapshot()
    assert snapshot["connected"] is False
    assert snapshot["last_error"] is not None
    assert snapshot["errors"] > 0


@pytest.mark.asyncio
async def test_an_evicted_client_is_signalled_not_left_hanging():
    """Defect 3. The too-slow client was removed from the hub but its websocket
    stayed open and silent: its send task slept forever on queue.get()."""
    hub = Hub()
    client = Client("slow")
    await hub.register(client)

    for i in range(QUEUE_MAX + 5):
        await hub.broadcast({"type": "tick", "n": i})

    assert hub.client_count == 0
    assert client.evicted.is_set(), "the eviction must be signalled to the send task"


@pytest.mark.asyncio
async def test_a_future_dated_event_is_rejected(monkeypatch):
    """Defect 7. A future timestamp (timezone mishandled source-side) went
    through everything: negative age so the horizon passed, `breaking` always
    true, and descending date sort -- it nailed itself to the head of the feed
    forever.
    """
    from app import pipeline as pipeline_module
    from app.core import config

    sent: list[dict] = []

    async def capture(message: dict) -> None:
        sent.append(message)

    monkeypatch.setattr(pipeline_module.hub, "broadcast", capture)
    monkeypatch.setattr(config.settings, "future_tolerance_seconds", 120.0)

    store = EventStore(maxlen=50, data_dir=None, persist=False)
    pipeline = Pipeline(store, Deduper())

    await pipeline.emit(quake("bad:1", minutes_ago=-30))  # dated 30 min ahead
    assert store.recent(limit=10) == []
    assert pipeline.dropped == 1
    assert sent == []

    # one minute ahead is still tolerated: clocks are never exact
    await pipeline.emit(quake("ok:1", minutes_ago=-1))
    assert [e.id for e in store.recent(limit=10)] == ["ok:1"]
    # ... but it is not announced as "just happened"
    assert sent[-1]["breaking"] is False


def test_evicting_a_cluster_primary_promotes_a_survivor():
    """Defect 4. `primary_only` hides any event whose cluster_id is not its
    own. When the ring evicted the representative (the EMSC entry, first in,
    first out), the earthquake vanished from the feed entirely."""
    store = EventStore(maxlen=3, data_dir=None, persist=False)

    emsc = quake("emsc:1", 5)
    emsc.cluster_id = "emsc:1"
    usgs = quake("usgs:1", 4)
    usgs.cluster_id = "emsc:1"  # same cluster, secondary
    store.upsert(emsc)
    store.upsert(usgs)

    # saturate the ring: emsc:1 is the oldest, it goes
    store.upsert(quake("x:1", 3))
    store.upsert(quake("x:2", 2))

    assert store.get("emsc:1") is None
    ids = [e.id for e in store.recent(limit=10, primary_only=True)]
    assert "usgs:1" in ids, "the survivor must be promoted, not erased from the feed"


def test_replaying_the_journal_does_not_rewrite_it(tmp_path):
    """Defect 5. Every restart rewrote the whole day's journal: 747 lines
    including 368 duplicates after two restarts."""
    store = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    store.upsert(quake("usgs:a", 1))
    journal = next(tmp_path.glob("events-*.jsonl"))
    before = journal.read_text().count("\n")

    reloaded = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    assert reloaded.load_backlog(journal) == 1
    assert journal.read_text().count("\n") == before, "replaying must not rewrite"


# --- Second adversarial audit (defects confirmed on real data) ----------------


def test_a_warning_issued_in_advance_is_not_rejected_as_a_clock_error():
    """Defect 8. A weather warning is PUBLISHED BEFORE it starts: advance
    notice is its whole point. Its `onset` is therefore legitimately in the
    future, and the anti-future filter rejected it on every cycle."""
    from app.models.event import Kind as K

    warning = quake("meteoalarm:1", minutes_ago=-96)  # starts in 1 h 36
    warning.kind = K.STORM
    warning.ongoing = True
    assert warning.age_seconds < 0

    store = EventStore(maxlen=10, data_dir=None, persist=False)
    pipeline = Pipeline(store, Deduper())
    asyncio.run(pipeline.emit(warning))
    assert [e.id for e in store.recent(limit=5)] == ["meteoalarm:1"]

    # an earthquake, though, cannot be dated in advance
    future_quake = quake("usgs:future", minutes_ago=-96)
    asyncio.run(pipeline.emit(future_quake))
    assert store.get("usgs:future") is None


def test_the_sweep_only_removes_ongoing_alerts():
    """Defect 9. Without the `ongoing` filter, the sweep erased ordinary
    earthquakes after six hours of silence: the store kept only seven hours of
    history while the interface offers 24 h and "all"."""
    from datetime import timedelta as _td

    store = EventStore(maxlen=50, data_dir=None, persist=False)

    old_quake = quake("usgs:old", 60 * 8)
    old_quake.last_seen = datetime.now(UTC) - _td(hours=8)
    store.upsert(old_quake)

    mute_alert = alert("gdacs:mute")
    mute_alert.ongoing = True
    mute_alert.last_seen = datetime.now(UTC) - _td(hours=8)
    store.upsert(mute_alert)

    removed = store.prune_stale(max_silence_hours=6)
    assert [e.id for e in removed] == ["gdacs:mute"]
    assert store.get("usgs:old") is not None


def test_replaying_the_journal_does_not_reset_the_silence_clock(tmp_path):
    """Defect 10. The replay refreshed `last_seen`, which handed six more
    hours of reprieve to every dead alert on EVERY restart -- and masked the
    sweep entirely in production."""
    from datetime import timedelta as _td

    store = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    old_alert = alert("gdacs:x")
    old_alert.ongoing = True
    old_alert.last_seen = datetime.now(UTC) - _td(hours=20)
    store.upsert(old_alert)

    journal = next(tmp_path.glob("events-*.jsonl"))
    reloaded = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    reloaded.load_backlog(journal)
    reloaded.load_backlog(journal)  # second pass: the noop path

    restored = reloaded.get("gdacs:x")
    assert restored is not None
    silence_h = (datetime.now(UTC) - restored.last_seen).total_seconds() / 3600
    assert silence_h > 19, "the silence must survive the replay"
    assert reloaded.prune_stale(max_silence_hours=6)


def test_old_journals_are_purged(tmp_path):
    """Defect 11. The journal grows by about 5 MB per day and was never
    purged: on a service that runs continuously, the volume ends up saturating
    a disk shared with the other products of the suite."""
    from datetime import timedelta as _td

    store = EventStore(maxlen=10, data_dir=tmp_path, persist=True)
    today = datetime.now(UTC)
    for age in (0, 1, 9, 30):
        (tmp_path / f"events-{(today - _td(days=age)):%Y-%m-%d}.jsonl").write_text("{}\n")
    # a file that does not follow the convention must not be touched
    (tmp_path / "notes.txt").write_text("keep")

    removed = store.purge_journals(keep_days=7)

    assert {p.name for p in removed} == {
        f"events-{(today - _td(days=9)):%Y-%m-%d}.jsonl",
        f"events-{(today - _td(days=30)):%Y-%m-%d}.jsonl",
    }
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / f"events-{today:%Y-%m-%d}.jsonl").exists()
