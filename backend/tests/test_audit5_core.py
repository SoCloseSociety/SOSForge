"""Fifth audit -- the deduper speaks for events the store no longer has, and
treats the product's own bulletins as if they were an agency's.

Both defects are invisible in the test suite as it stood, because the swarm
detector was only ever tested in isolation: `detect()` returns the right
clusters. It is what happens to the resulting bulletin on its way through the
pipeline that was broken, and nothing exercised that path.
"""

from __future__ import annotations

from datetime import timedelta

from app.dedupe import Deduper
from app.models.event import Event, Kind, Severity, utcnow
from app.pipeline import Pipeline
from app.store.ring import EventStore
from app.swarm import Swarm, as_event, detect


def quake(event_id: str, *, source: str, lat: float, lon: float, mag: float, ago_s: float = 30.0):
    return Event(
        id=event_id,
        source=source,
        source_id=event_id,
        kind=Kind.EARTHQUAKE,
        time=utcnow() - timedelta(seconds=ago_s),
        lat=lat,
        lon=lon,
        magnitude=mag,
        place="somewhere",
        severity=Severity.MODERATE,
        title="t",
    )


class TestARetractedEventStopsSpeakingForItsCluster:
    """A cancelled early warning is removed from the store -- and stayed in the
    deduper's window, where it kept adopting the solutions that arrived after.

    The EMSC solution of the same real quake then joined a cluster whose
    representative no longer exists anywhere, and `primary_only` -- which is
    what /api/events and the websocket snapshot use by default -- hides any
    event whose cluster_id is not its own id. So the quake was ingested, stored,
    and shown to nobody. The feed lying by omission is still the feed lying.
    """

    async def test_the_real_quake_is_still_visible_after_a_cancellation(self):
        store = EventStore(maxlen=50, persist=False)
        deduper = Deduper()
        pipeline = Pipeline(store, deduper)

        await pipeline.emit(quake("jma_eew:X1", source="jma_eew", lat=35.0, lon=139.0, mag=6.0))
        await pipeline.retract("jma_eew:X1", "cancelled")

        # the same quake, seen properly by EMSC ten seconds later
        await pipeline.emit(quake("emsc:1", source="emsc", lat=35.05, lon=139.02, mag=6.1))
        await pipeline.emit(quake("usgs:1", source="usgs", lat=35.06, lon=139.03, mag=6.2))

        visible = [e.id for e in store.recent(limit=50, primary_only=True)]
        assert visible, "the quake vanished from every default view"
        assert "emsc:1" in visible

    async def test_no_survivor_points_at_something_that_is_gone(self):
        store = EventStore(maxlen=50, persist=False)
        pipeline = Pipeline(store, Deduper())
        await pipeline.emit(quake("jma_eew:X2", source="jma_eew", lat=-5.0, lon=150.0, mag=6.4))
        await pipeline.retract("jma_eew:X2", "cancelled")
        await pipeline.emit(quake("emsc:2", source="emsc", lat=-5.02, lon=150.01, mag=6.5))

        for event in store.recent(limit=50):
            assert store.get(event.cluster_id) is not None, (
                f"{event.id} belongs to cluster {event.cluster_id}, which no longer exists"
            )


class TestTheSwarmBulletinIsNotAnAgencySolution:
    """The swarm marker is OUR OWN summary of events already in the store. It
    is placed at the cluster centroid, dated at its latest member, and carries
    that member's magnitude -- so the deduper matched it against the very quake
    it summarises and marked it a duplicate.

    Result: during every live swarm -- the exact situation the feature exists
    for -- the swarm alert was broadcast as non-primary and filtered out of
    /api/events and the snapshot. Shipped, tested, and invisible.
    """

    def members(self, n: int = 9) -> list[Event]:
        return [
            quake(
                f"usgs:q{i}",
                source="usgs",
                lat=38.0 + i * 0.01,
                lon=142.0,
                mag=3.5 + i * 0.05,
                ago_s=600 - i * 60,
            )
            for i in range(n)
        ]

    async def test_the_swarm_alert_reaches_the_default_view(self):
        store = EventStore(maxlen=200, persist=False)
        pipeline = Pipeline(store, Deduper())
        for member in self.members():
            await pipeline.emit(member)

        swarms = detect(store.recent(limit=200, primary_only=True), min_count=8)
        assert swarms, "the detector itself must still find it"
        await pipeline.emit(as_event(swarms[0]))

        visible = [e.id for e in store.recent(limit=200, primary_only=True)]
        assert any(i.startswith("swarm:") for i in visible), (
            "the swarm bulletin was deduped against one of its own members"
        )

    async def test_a_swarm_bulletin_never_adopts_a_later_quake(self):
        """The other direction, and the worse one: the marker sat in the
        deduper's window and swallowed the real quakes that followed, which
        then disappeared from the feed AND stopped counting toward the swarm."""
        store = EventStore(maxlen=200, persist=False)
        deduper = Deduper()
        pipeline = Pipeline(store, deduper)
        for member in self.members():
            await pipeline.emit(member)
        swarms = detect(store.recent(limit=200, primary_only=True), min_count=8)
        await pipeline.emit(as_event(swarms[0]))

        # the strongest quake of the sequence, arriving after the bulletin
        await pipeline.emit(
            quake("emsc:big", source="emsc", lat=38.04, lon=142.0, mag=3.9, ago_s=5)
        )

        big = store.get("emsc:big")
        assert big is not None
        assert big.cluster_id == "emsc:big", "the real quake was adopted by our own bulletin"


class TestASwarmOnTheAntimeridianIsNotInTheAtlantic:
    """Kermadec, the Rat Islands, Fiji: some of the most swarm-prone ground on
    Earth sits on the 180th meridian, where longitudes alternate between +179.9
    and -179.9. The arithmetic mean of those is 0 -- the Gulf of Guinea.

    The coordinate validators cannot catch it: 0.0 is a perfectly valid
    longitude. Lesson 15 in its purest form -- a wrong position is far worse
    than a missing one.
    """

    def test_the_centroid_stays_with_the_quakes(self):
        members = [
            quake(
                f"usgs:k{i}",
                source="usgs",
                lat=-30.0,
                lon=179.97 if i % 2 else -179.97,
                mag=4.0,
                ago_s=100 * i,
            )
            for i in range(10)
        ]
        swarm = Swarm(members)
        assert abs(swarm.lon) > 179.0, f"centroid longitude {swarm.lon} is on the wrong side"
        assert abs(swarm.lat + 30.0) < 1.0

    def test_an_ordinary_swarm_is_unaffected(self):
        members = [
            quake(
                f"usgs:j{i}", source="usgs", lat=38.0, lon=142.0 + i * 0.01, mag=4.0, ago_s=100 * i
            )
            for i in range(10)
        ]
        swarm = Swarm(members)
        assert 141.9 < swarm.lon < 142.2
