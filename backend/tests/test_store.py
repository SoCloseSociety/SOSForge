"""Tests du store: revisions, tri du flux, representant de cluster."""

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
        place="quelque part",
        severity=Severity.MINOR,
    )


@pytest.fixture
def store() -> EventStore:
    return EventStore(maxlen=50, data_dir=None, persist=False)


def test_insert_then_identical_reinsert_is_a_noop(store: EventStore):
    """Chaque poll renvoie les memes evenements: sans cette regle, le flux
    rediffuserait tout le feed toutes les 5 secondes."""
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
    # une revision ne cree pas une deuxieme entree
    assert len(store.recent(limit=100)) == 1


def test_recent_is_sorted_by_event_time_not_arrival(store: EventStore):
    """Un bulletin vieux de trois jours re-poll a l'instant ne doit pas squatter
    la tete du flux."""
    store.upsert(make_event("usgs:recent", 1))
    store.upsert(make_event("usgs:old", 4000))
    store.upsert(make_event("usgs:middle", 30))

    order = [e.id for e in store.recent(limit=10)]
    assert order == ["usgs:recent", "usgs:middle", "usgs:old"]


def test_primary_only_keeps_one_event_per_cluster(store: EventStore):
    deduper = Deduper()
    first = make_event("emsc:1", 0.2)
    second = make_event("usgs:2", 0.3)  # meme lieu, meme instant, autre source
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
    # les evenements evinces ne doivent plus etre adressables
    assert small.get("usgs:0") is None
    assert small.get("usgs:5") is not None


@pytest.mark.asyncio
async def test_only_a_genuinely_recent_event_is_announced_as_breaking(monkeypatch, store):
    """GDACS garde ses alertes des jours: au premier cycle, une centaine
    d'evenements anciens entrent d'un coup. Ils doivent apparaitre sur la carte,
    jamais clignoter ni sonner comme s'ils venaient de tomber."""
    from app import pipeline as pipeline_module

    sent: list[dict] = []

    async def capture(message: dict) -> None:
        sent.append(message)

    monkeypatch.setattr(pipeline_module.hub, "broadcast", capture)
    pipeline = Pipeline(store, Deduper())

    await pipeline.emit(make_event("usgs:just-now", 0.5))
    await pipeline.emit(make_event("gdacs:EQ42", 60 * 26))  # publie il y a 26 h

    assert [m["breaking"] for m in sent] == [True, False]


@pytest.mark.asyncio
async def test_an_archive_entry_is_dropped_but_never_a_severe_alert(monkeypatch, store):
    """La liste JMA remonte a plus de neuf mois et GDACS garde ses alertes des
    semaines: sans horizon, ces archives evincent du ring les evenements du
    moment. Mais un cyclone rouge en cours ne devient pas caduc a trois jours."""
    from app.core import config

    monkeypatch.setattr(config.settings, "max_event_age_days", 3.0)
    pipeline = Pipeline(store, Deduper())

    await pipeline.emit(make_event("jma:vieux", 60 * 24 * 200))  # 200 jours
    await pipeline.emit(make_event("usgs:recent", 30))

    grave = make_event("gdacs:cyclone", 60 * 24 * 9)  # 9 jours, mais rouge
    grave.severity = Severity.EXTREME
    grave.kind = Kind.CYCLONE  # un cyclone est EN COURS, il dure
    await pipeline.emit(grave)

    # ... alors qu'un seisme est instantane: passe l'horizon c'est de l'histoire,
    # meme a magnitude 8
    vieux_gros = make_event("jma:vieux-gros", 60 * 24 * 200, magnitude=8.0)
    vieux_gros.severity = Severity.EXTREME
    await pipeline.emit(vieux_gros)

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
