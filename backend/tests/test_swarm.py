"""Seismic swarm detection.

A swarm is one of the few precursory signals with an established operational
use, and it is also the easiest thing to cry wolf about. These tests protect
both sides: it must fire on a real cluster, and stay silent on ordinary
background seismicity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.event import Event, Kind, Severity
from app.swarm import as_event, detect


def quake(
    event_id: str,
    minutes_ago: float,
    lat: float = 64.0,
    lon: float = -21.0,
    magnitude: float = 2.0,
) -> Event:
    return Event(
        id=event_id,
        source="emsc",
        source_id=event_id,
        kind=Kind.EARTHQUAKE,
        time=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        lat=lat,
        lon=lon,
        magnitude=magnitude,
        place="Reykjanes peninsula",
        cluster_id=event_id,
    )


def test_a_tight_cluster_is_detected():
    """Twelve quakes inside a few kilometres over six hours: this is what an
    observatory watches, and what preceded several Icelandic eruptions."""
    events = [quake(f"q{i}", minutes_ago=i * 30, lat=64.0 + i * 0.005) for i in range(12)]
    swarms = detect(events)

    assert len(swarms) == 1
    assert swarms[0].count == 12
    assert swarms[0].duration_hours > 5


def test_ordinary_background_seismicity_is_not_a_swarm():
    """Quakes scattered across the world must never be grouped: crying swarm
    over routine activity teaches readers to ignore the signal."""
    # scattered across the globe, and within real coordinate bounds -- the
    # model rejects anything else, as it should
    events = [
        quake(f"q{i}", minutes_ago=i * 30, lat=-40 + i * 7.0, lon=-170 + i * 28.0)
        for i in range(12)
    ]
    assert detect(events) == []


def test_too_few_quakes_is_not_a_swarm():
    events = [quake(f"q{i}", minutes_ago=i * 10) for i in range(5)]
    assert detect(events) == []


def test_old_activity_falls_out_of_the_window():
    events = [quake(f"q{i}", minutes_ago=60 * 48 + i) for i in range(15)]
    assert detect(events) == []


def test_the_same_quake_seen_by_three_agencies_is_counted_once():
    """Without this, a single earthquake reported by EMSC, USGS and GEOFON
    would inflate every cluster threefold."""
    events = []
    for i in range(10):
        primary = quake(f"emsc:{i}", minutes_ago=i * 20)
        events.append(primary)
        for other in ("usgs", "geofon"):
            secondary = quake(f"{other}:{i}", minutes_ago=i * 20)
            secondary.cluster_id = primary.id  # grouped by the deduper
            events.append(secondary)

    swarms = detect(events)
    assert len(swarms) == 1
    assert swarms[0].count == 10, "secondary solutions must not inflate the count"


def test_severity_follows_what_is_actually_at_stake():
    small = detect([quake(f"q{i}", minutes_ago=i * 20, magnitude=1.5) for i in range(10)])
    assert as_event(small[0]).severity is Severity.MODERATE

    with_a_strong_one = [quake(f"q{i}", minutes_ago=i * 20, magnitude=1.5) for i in range(10)]
    with_a_strong_one[0].magnitude = 5.2
    assert as_event(detect(with_a_strong_one)[0]).severity is Severity.SEVERE


def test_the_swarm_event_keeps_the_same_id_as_it_grows():
    """Keyed on place and start hour: a swarm that keeps producing quakes must
    update one entry, not spawn a new marker every five minutes."""
    first = detect([quake(f"q{i}", minutes_ago=i * 20) for i in range(10)])
    grown = detect([quake(f"q{i}", minutes_ago=i * 20) for i in range(14)])

    assert as_event(first[0]).id == as_event(grown[0]).id
    assert as_event(grown[0]).raw["count"] == 14
    assert as_event(grown[0]).ongoing is True
