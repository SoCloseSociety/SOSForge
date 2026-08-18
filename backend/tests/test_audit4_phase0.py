"""Phase 0 -- defects the audit reported as fixed, which were not fixed here.

Each test states the rule it protects and fails without the corresponding fix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.dedupe import Deduper
from app.models.event import Event, Kind


def _base(**over):
    data = dict(
        id="usgs:x",
        source="usgs",
        source_id="x",
        kind=Kind.EARTHQUAKE,
        time=datetime.now(UTC),
        place="somewhere",
    )
    data.update(over)
    return data


class TestCoordinateBounds:
    """Lesson 15: a wrong position is far worse than a missing one. Only
    `parse_iso6709` bounded anything, so every other source could inject a point
    off the globe and nothing downstream would notice."""

    def test_an_impossible_latitude_is_rejected(self):
        with pytest.raises(ValidationError):
            Event(**_base(lat=3237.5, lon=13.0))

    def test_an_impossible_longitude_is_rejected(self):
        with pytest.raises(ValidationError):
            Event(**_base(lat=45.0, lon=200.0))

    def test_the_poles_and_the_antimeridian_remain_valid(self):
        assert Event(**_base(lat=90.0, lon=180.0)).lat == 90.0
        assert Event(**_base(lat=-90.0, lon=-180.0)).lon == -180.0

    def test_no_position_is_still_allowed(self):
        event = Event(**_base(lat=None, lon=None))
        assert event.lat is None


class TestAwareTimestamps:
    """A naive datetime compared to an aware one raises TypeError and kills the
    source. Every normalizer is careful about this today; the model was not."""

    def test_a_naive_timestamp_is_made_utc(self):
        event = Event(**_base(time=datetime(2026, 8, 18, 12, 0, 0)))
        assert event.time.tzinfo is not None
        assert event.time.hour == 12

    def test_ages_are_computable_on_every_event(self):
        assert Event(**_base(time=datetime(2026, 8, 18, 12, 0, 0))).age_seconds is not None


class TestDeduperScansTheWholeWindow:
    """The deque is ordered by ARRIVAL, not by event time. An early break that
    assumes chronological order stops the scan at the first old arrival and
    misses the match sitting behind it -- which is the whole job of the deduper.
    """

    def test_a_match_behind_an_older_arrival_is_still_found(self):
        deduper = Deduper()
        now = datetime.now(UTC)

        # arrives FIRST and is dated now: this is the one the next event must
        # match
        emsc = Event(**_base(id="emsc:1", source="emsc", time=now, lat=10, lon=20, magnitude=5.0))
        deduper.assign(emsc)

        # arrives AFTER but is dated old: a catalog bulletin re-polled from
        # GDACS or JMA. Scanning newest-arrival-first, this one comes up before
        # the EMSC entry -- and an early break on its age hides everything
        # behind it.
        old_arrival = Event(
            **_base(id="gdacs:1", source="gdacs", time=now - timedelta(hours=20), lat=10, lon=20)
        )
        deduper.assign(old_arrival)

        usgs = Event(
            **_base(id="usgs:1", source="usgs", time=now, lat=10.05, lon=20.05, magnitude=5.2)
        )
        deduper.assign(usgs)

        assert usgs.cluster_id == emsc.cluster_id, (
            "the scan must not stop at the first old arrival: the deque is "
            "ordered by arrival, not by event time"
        )


class TestApiContract:
    """The API is public and its answers are consumed by machines. Saying
    "found: false" with a 200 makes every client treat a missing event as a
    successful read."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        import app.main as main

        # TestClient without the lifespan: we are testing the routes, not the
        # nineteen ingesters
        return TestClient(main.app)

    def test_an_unknown_event_is_a_404(self, client):
        assert client.get("/api/events/does-not-exist").status_code == 404

    def test_an_unknown_kind_is_rejected_not_silently_empty(self, client):
        """An empty list for a typo reads as "nothing is happening", which on
        this product is the worst possible answer."""
        assert client.get("/api/events?kind=earthquak").status_code == 422

    def test_a_negative_magnitude_filter_is_rejected(self, client):
        assert client.get("/api/events?min_magnitude=-5").status_code == 422

    def test_readyz_reports_whether_the_sources_are_actually_up(self, client):
        """`/healthz` answers "ok" as long as the process lives. An orchestrator
        needs to know whether the thing is doing its job."""
        response = client.get("/readyz")
        assert response.status_code in (200, 503)
        body = response.json()
        assert "sources_up" in body and "sources_total" in body


class TestUsgsRobustness:
    """One malformed record must never cost the rest of a batch, and a position
    that omits its optional third element is valid GeoJSON."""

    def test_a_two_element_position_is_accepted(self):
        from app.sources.usgs import parse_feature

        event = parse_feature(
            {
                "type": "Feature",
                "id": "ci1",
                "properties": {"mag": 2.0, "place": "somewhere", "time": 1786985672530},
                # a GeoJSON position may legitimately be [lon, lat]
                "geometry": {"type": "Point", "coordinates": [-116.6, 33.6]},
            }
        )
        assert event is not None
        assert event.lat == 33.6
        assert event.depth_km is None

    def test_an_out_of_globe_position_is_refused_not_stored(self):
        """Lesson 15 again, this time at the source boundary."""
        from app.sources.usgs import parse_feature

        with pytest.raises(ValidationError):
            parse_feature(
                {
                    "type": "Feature",
                    "id": "ci2",
                    "properties": {"mag": 2.0, "place": "nowhere", "time": 1786985672530},
                    "geometry": {"type": "Point", "coordinates": [999.0, 999.0]},
                }
            )
