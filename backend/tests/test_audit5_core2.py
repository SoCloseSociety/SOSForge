"""Fifth audit, remainder -- what the content fingerprint cannot see.

`upsert` decides "new / update / noop" by comparing a fingerprint. Anything the
fingerprint ignores is a revision the store discards, and a client that keeps
stale data while the server believes it is current.
"""

from __future__ import annotations

from datetime import timedelta

from app.models.event import Event, Kind, Severity, utcnow
from app.store.ring import EventStore


def storm(event_id: str = "nhc:al012026", **kw) -> Event:
    base = {
        "id": event_id,
        "source": "nhc",
        "source_id": event_id,
        "kind": Kind.CYCLONE,
        "time": utcnow() - timedelta(hours=1),
        "lat": 25.0,
        "lon": -70.0,
        "magnitude": None,
        "place": "Atlantic",
        "severity": Severity.SEVERE,
        "ongoing": True,
        "title": "Tropical Storm Iselle",
    }
    base.update(kw)
    return Event(**base)


class TestARevisionTheFingerprintCouldNotSee:
    def test_a_new_forecast_track_is_a_revision(self):
        """`forecast_track` was made a first-class field precisely because "a
        forecast the client never receives is a forecast that does not exist".
        The fingerprint then ignored it: a stationary storm whose FORECAST
        shifts -- same position, same severity -- looked identical, so the
        store kept the old track and broadcast nothing."""
        store = EventStore(maxlen=50, persist=False)
        store.upsert(storm(forecast_track=[{"tau": 24, "lat": 26.0, "lon": -71.0}]))

        moved = storm(forecast_track=[{"tau": 24, "lat": 29.5, "lon": -75.0}])
        stored, action = store.upsert(moved)

        assert action == "update", "the storm's forecast changed and nobody was told"
        assert stored.forecast_track is not None
        assert stored.forecast_track[0]["lat"] == 29.5

    def test_a_corrected_origin_time_is_a_revision(self):
        """Agencies revise origin times. Two events fifteen minutes apart
        fingerprinted identically, so the wrong time stayed in the feed."""
        store = EventStore(maxlen=50, persist=False)
        first = storm("usgs:1", kind=Kind.EARTHQUAKE, magnitude=5.4)
        store.upsert(first)

        corrected = storm(
            "usgs:1", kind=Kind.EARTHQUAKE, magnitude=5.4, time=first.time - timedelta(minutes=15)
        )
        _, action = store.upsert(corrected)

        assert action == "update"

    def test_a_new_title_is_a_revision(self):
        """The swarm bulletin's count reaches the browser ONLY through its
        title -- `public()` strips `raw`. A swarm growing from 9 quakes to 30
        therefore never updated on screen."""
        store = EventStore(maxlen=50, persist=False)
        store.upsert(storm("swarm:38.0:142.0", title="Seismic swarm -- 9 quakes in 6 h"))

        _, action = store.upsert(
            storm("swarm:38.0:142.0", title="Seismic swarm -- 30 quakes in 9 h")
        )

        assert action == "update"

    def test_an_identical_republication_is_still_a_noop(self):
        """The counterpart, and the reason the fingerprint exists: sources
        republish their whole list every cycle. If that became an update, the
        product would rebroadcast everything it knows, every few seconds."""
        store = EventStore(maxlen=50, persist=False)
        original = storm(forecast_track=[{"tau": 24, "lat": 26.0, "lon": -71.0}])
        store.upsert(original)

        same = storm(time=original.time, forecast_track=[{"tau": 24, "lat": 26.0, "lon": -71.0}])
        _, action = store.upsert(same)

        assert action == "noop"


class TestReplayDoesNotInflateTheSourceCounters:
    """`/api/sources` shows what each source has delivered THIS session. The
    counter was bumped during journal replay too, so after a restart every
    source claimed credit for events it had not fetched -- the exact misleading
    observability the events_seen/ingested split was built to avoid.
    """

    def test_a_replayed_event_is_not_counted_as_delivered(self, tmp_path):
        store = EventStore(maxlen=50, data_dir=tmp_path, persist=False)
        with store.replaying():
            store.upsert(storm("usgs:restored", kind=Kind.EARTHQUAKE, magnitude=4.0))

        assert store.counters.get("nhc", 0) == 0

    def test_a_live_event_still_is(self):
        store = EventStore(maxlen=50, persist=False)
        store.upsert(storm())
        assert store.counters.get("nhc") == 1
