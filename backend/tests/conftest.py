"""Fixtures for the v2 alerting contract tests. Nothing here is autouse."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest

from app.changelog import ChangeLog
from app.dedupe import Deduper
from app.models.event import Event, Kind, Severity, utcnow
from app.pipeline import Pipeline
from app.store.ring import EventStore

TOKYO = (35.6762, 139.6503)
YOKOHAMA = (35.4437, 139.6380)  # ~26 km from Tokyo
SAPPORO = (43.0618, 141.3545)  # ~830 km from Tokyo
LOS_ANGELES = (34.0522, -118.2437)


def make_event(
    sid: str,
    lat: float | None,
    lon: float | None,
    *,
    severity: Severity = Severity.MODERATE,
    kind: Kind = Kind.STORM,
    minutes_ago: float = 5,
    country: str | None = None,
    **extra,
) -> Event:
    return Event(
        id=f"test:{sid}",
        source="test",
        source_id=sid,
        kind=kind,
        time=utcnow() - timedelta(minutes=minutes_ago),
        lat=lat,
        lon=lon,
        severity=severity,
        country_code=country,
        place=f"place {sid}",
        **extra,
    )


@dataclass
class V2:
    main: object
    store: EventStore
    pipeline: Pipeline


@pytest.fixture
def v2(monkeypatch):
    """The app with a fresh, non-persisting store, its own pipeline and a
    fresh change log, so no test writes into the product's journal and no
    test sees another one's events."""
    import app.main as main
    from app.hub import hub

    store = EventStore(maxlen=5000, persist=False)
    pipeline = Pipeline(store, Deduper(window_seconds=90, radius_km=250, mag_delta=1.2))
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "pipeline", pipeline)
    monkeypatch.setattr(hub, "changelog", ChangeLog())
    return V2(main, store, pipeline)


@pytest.fixture
def ev():
    """The event factory, as a fixture."""
    return make_event
