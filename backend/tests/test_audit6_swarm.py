"""Sixth audit -- the swarm detector counted itself and forked its own entries.

Live on production (2026-10-26 19:23 UTC): 28 swarm entries, 18 pairs of them
less than 30 km apart -- the radius that defines a single swarm. Four entries
around one point of Mindanao, three at Kilauea, two in Salta. Two causes:

* `detect()` read the whole store, and a swarm entry IS an earthquake with
  `cluster_id == id`. The previous sweep's entry joined the next sweep's
  cluster as a member, at the centroid, carrying the max magnitude;
* the id was the centroid rounded to 0.1 degree. A swarm drifting a few
  hundred metres across a rounding line got a new id, a second marker, and
  the first one lingered for six hours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.event import Event, Kind
from app.swarm import as_event, detect


def quake(event_id: str, minutes_ago: float, lat: float, lon: float, mag: float = 2.0) -> Event:
    return Event(
        id=event_id,
        source="emsc",
        source_id=event_id,
        kind=Kind.EARTHQUAKE,
        time=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        lat=lat,
        lon=lon,
        magnitude=mag,
        place="MINDANAO, PHILIPPINES",
        cluster_id=event_id,
    )


def cluster(prefix: str, lat: float, lon: float, n: int = 9, start: int = 0) -> list[Event]:
    return [
        quake(f"emsc:{prefix}{i}", minutes_ago=20 * (n - i) + 1, lat=lat + i * 0.004, lon=lon)
        for i in range(start, start + n)
    ]


class TestTheDetectorDoesNotCountItself:
    def test_its_own_entry_is_not_a_member(self):
        quakes = cluster("a", 5.55, 125.25)
        entry = as_event(detect(quakes)[0])

        again = detect([*quakes, entry])

        assert len(again) == 1
        assert again[0].count == len(quakes), "the previous swarm entry was counted as a quake"
        assert entry.id not in {e.id for e in again[0].events}

    def test_an_aftershock_sequence_entry_is_not_a_member(self):
        quakes = cluster("b", 5.55, 125.25)
        sequence = quake("aftershock:emsc:main", minutes_ago=1, lat=5.56, lon=125.25, mag=6.1)
        sequence.source = "aftershock"
        sequence.ongoing = True

        swarms = detect([*quakes, sequence])

        assert swarms[0].count == len(quakes)
        assert swarms[0].max_magnitude == 2.0, "a synthetic M6.1 became the swarm's maximum"

    def test_its_own_entry_cannot_keep_a_dying_swarm_alive(self):
        """Seven real quakes are below the threshold of eight. With the entry
        counted as the eighth, the swarm sustained itself on its own marker."""
        entry = as_event(detect(cluster("c", 5.55, 125.25))[0])
        remaining = cluster("c", 5.55, 125.25, n=7)

        assert detect([*remaining, entry]) == []


class TestASwarmKeepsItsIdentity:
    def test_a_drift_across_a_rounding_line_keeps_the_same_entry(self):
        """The entry as the previous code named it -- the rounded centroid --
        which is what the production store and journal hold today."""
        first = detect(cluster("d", 5.533, 125.25))
        entry = as_event(first[0])
        entry.id = f"swarm:{first[0].lat:.1f}:{first[0].lon:.1f}"
        assert entry.id == "swarm:5.5:125.2"

        drifted = cluster("d", 5.533, 125.25) + [
            quake(f"emsc:dx{i}", minutes_ago=1 + i, lat=5.585, lon=125.25) for i in range(4)
        ]
        later = detect([*drifted, entry])

        assert f"{later[0].lat:.1f}" == "5.6", "the test no longer crosses the rounding line"
        assert as_event(later[0]).id == entry.id, "the drift forked the swarm into a second entry"

    def test_its_first_quake_leaving_the_window_keeps_the_same_entry(self):
        """A long-lived swarm (The Geysers never stops) loses its earliest
        member every time the 24 h window slides past it."""
        old_members = cluster("g", 5.55, 125.25)
        entry = as_event(detect(old_members)[0])

        old_members[0].time = datetime.now(UTC) - timedelta(hours=25)
        newcomer = quake("emsc:g-new", minutes_ago=1, lat=5.56, lon=125.25)
        later = detect([*old_members, newcomer, entry])

        assert later[0].events[0].id != old_members[0].id
        assert as_event(later[0]).id == entry.id

    def test_two_distinct_swarms_never_share_one_entry(self):
        """Matching is one to one: two clusters near the same old entry must
        not both write to it, or each sweep overwrites the other."""
        north = cluster("n", 5.80, 125.25)
        south = cluster("s", 5.40, 125.25)
        old = as_event(detect(north)[0])

        swarms = detect([*north, *south, old])
        ids = [as_event(s).id for s in swarms]

        assert len(swarms) == 2
        assert len(set(ids)) == 2
        nearest = min(swarms, key=lambda s: abs(s.lat - old.lat))
        assert as_event(nearest).id == old.id

    def test_an_entry_beyond_the_radius_is_not_adopted(self):
        old = as_event(detect(cluster("e", 10.0, 125.25))[0])
        swarm = detect([*cluster("f", 5.55, 125.25), old])[0]
        assert as_event(swarm).id != old.id

    def test_a_new_swarm_gets_an_id_no_other_swarm_can_take(self):
        """Clustering is single-link on the FIRST quake of each cluster, so two
        clusters can be distinct by the radius while their centroids fall in
        the same 0.1 degree cell -- and then share an id and overwrite each
        other at every sweep."""
        km = 1 / 110.8  # one km of longitude at 5.5 N, in degrees
        origin = 124.9344
        west = [quake("emsc:w-head", 600, 5.53, origin)] + [
            quake(f"emsc:w{i}", 500 - i, 5.53, origin + 27 * km) for i in range(8)
        ]
        east = [quake("emsc:e-head", 590, 5.53, origin + 31 * km)] + [
            quake(f"emsc:e{i}", 400 - i, 5.53, origin + 32 * km) for i in range(8)
        ]

        swarms = detect([*west, *east])

        assert len(swarms) == 2
        assert {f"{s.lon:.1f}" for s in swarms} == {"125.2"}, "both centroids share a cell"
        assert len({as_event(s).id for s in swarms}) == 2, "two swarms, one id"
