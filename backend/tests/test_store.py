"""Store tests: revisions, feed ordering, cluster representative."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.dedupe import Deduper
from app.models.event import Event, Kind, Severity
from app.pipeline import Pipeline
from app.store.ring import EventStore


def make_event(event_id: str, minutes_ago: float, magnitude: float | None = 3.0) -> Event:
    return Event(
        id=event_id,
        source=event_id.split(":")[0],
        source_id=event_id.split(":")[-1],
        kind=Kind.EARTHQUAKE,
        time=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        lat=10.0,
        lon=20.0,
        magnitude=magnitude,
        place="somewhere",
        severity=Severity.MINOR,
    )


@pytest.fixture
def store() -> EventStore:
    return EventStore(maxlen=50, data_dir=None, persist=False)


def test_insert_then_identical_reinsert_is_a_noop(store: EventStore):
    """Every poll returns the same events: without this rule, the stream would
    rebroadcast the whole feed every 5 seconds."""
    event = make_event("usgs:a", 1)
    _, action = store.upsert(event)
    assert action == "new"

    _, action = store.upsert(make_event("usgs:a", 1))
    assert action == "noop"


def test_changed_magnitude_is_a_revision(store: EventStore):
    store.upsert(make_event("usgs:a", 1, magnitude=3.0))
    revised, action = store.upsert(make_event("usgs:a", 1, magnitude=4.2))
    assert action == "update"
    assert revised.revision == 1
    assert revised.magnitude == 4.2
    assert revised.updated_at is not None
    # a revision does not create a second entry
    assert len(store.recent(limit=100)) == 1


def test_recent_is_sorted_by_event_time_not_arrival(store: EventStore):
    """A three-day-old bulletin re-polled just now must not squat at the head
    of the feed."""
    store.upsert(make_event("usgs:recent", 1))
    store.upsert(make_event("usgs:old", 4000))
    store.upsert(make_event("usgs:middle", 30))

    order = [e.id for e in store.recent(limit=10)]
    assert order == ["usgs:recent", "usgs:middle", "usgs:old"]


def test_primary_only_keeps_one_event_per_cluster(store: EventStore):
    deduper = Deduper()
    first = make_event("emsc:1", 0.2)
    second = make_event("usgs:2", 0.3)  # same place, same instant, another source
    deduper.assign(first)
    deduper.assign(second)
    store.upsert(first)
    store.upsert(second)

    assert len(store.recent(limit=10, primary_only=False)) == 2
    kept = store.recent(limit=10, primary_only=True)
    assert [e.id for e in kept] == ["emsc:1"]


def test_ring_eviction_purges_the_index():
    small = EventStore(maxlen=3, data_dir=None, persist=False)
    for i in range(6):
        small.upsert(make_event(f"usgs:{i}", i))
    assert len(small.recent(limit=50)) == 3
    # evicted events must no longer be addressable
    assert small.get("usgs:0") is None
    assert small.get("usgs:5") is not None


@pytest.mark.asyncio
async def test_only_a_genuinely_recent_event_is_announced_as_breaking(monkeypatch, store):
    """GDACS keeps its alerts for days: on the first cycle, a hundred old
    events come in at once. They must appear on the map, never blink nor ring
    as if they had just landed."""
    from app import pipeline as pipeline_module

    sent: list[dict] = []

    async def capture(message: dict) -> None:
        sent.append(message)

    monkeypatch.setattr(pipeline_module.hub, "broadcast", capture)
    pipeline = Pipeline(store, Deduper())

    await pipeline.emit(make_event("usgs:just-now", 0.5))
    await pipeline.emit(make_event("gdacs:EQ42", 60 * 26))  # published 26 h ago

    assert [m["breaking"] for m in sent] == [True, False]


@pytest.mark.asyncio
async def test_an_archive_entry_is_dropped_but_never_a_severe_alert(monkeypatch, store):
    """The JMA list goes back more than nine months and GDACS keeps its alerts
    for weeks: without a horizon, these archives evict the current events from
    the ring. But an ongoing red cyclone does not lapse at three days."""
    from app.core import config

    monkeypatch.setattr(config.settings, "max_event_age_days", 3.0)
    pipeline = Pipeline(store, Deduper())

    await pipeline.emit(make_event("jma:old", 60 * 24 * 200))  # 200 days
    await pipeline.emit(make_event("usgs:recent", 30))

    cyclone = make_event("gdacs:cyclone", 60 * 24 * 9)  # 9 days, but red
    cyclone.severity = Severity.EXTREME
    cyclone.kind = Kind.CYCLONE  # a cyclone is ONGOING, it lasts
    await pipeline.emit(cyclone)

    # ... whereas an earthquake is instantaneous: past the horizon it is
    # history, even at magnitude 8
    old_giant = make_event("jma:old-big", 60 * 24 * 200, magnitude=8.0)
    old_giant.severity = Severity.EXTREME
    await pipeline.emit(old_giant)

    assert sorted(e.id for e in store.recent(limit=10)) == ["gdacs:cyclone", "usgs:recent"]
    assert pipeline.dropped == 2


@pytest.mark.asyncio
async def test_pipeline_filters_below_minimum_magnitude(monkeypatch, store: EventStore):
    from app.core import config

    monkeypatch.setattr(config.settings, "min_magnitude", 2.0)
    pipeline = Pipeline(store, Deduper())

    await pipeline.emit(make_event("usgs:small", 1, magnitude=0.5))
    await pipeline.emit(make_event("usgs:big", 1, magnitude=5.0))

    assert [e.id for e in store.recent(limit=10)] == ["usgs:big"]
    assert pipeline.dropped == 1
