"""Seismic swarm detection, computed on our own feed.

**What this is, and what it is not.** It does not predict earthquakes -- nothing
does. It reports a pattern that is already visible in the data and that
volcanological and seismological observatories watch operationally: an unusual
concentration of earthquakes in a small area over a short time.

Why that matters. A swarm is one of the few precursory signals with an
established operational use: it precedes many eruptions (magma moving), and it
accompanies the reactivation of a fault segment. Icelandic, Italian and Japanese
observatories raise alert levels on exactly this signal. Reporting "37 quakes
within 15 km in 6 hours" is a statement of fact about the present, and the
reader draws the conclusion.

What we deliberately do NOT say: any probability that a bigger event follows.
That number exists in the literature but depends on a regional calibration we do
not have, and a made-up probability on an emergency product would be worse than
silence.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.dedupe import haversine_km
from app.models.event import Event, Kind, Severity, utcnow

log = logging.getLogger(__name__)


class Swarm:
    """A cluster of earthquakes close in space and time."""

    def __init__(self, events: list[Event]):
        self.events = sorted(events, key=lambda e: e.time)
        magnitudes = [e.magnitude for e in events if e.magnitude is not None]
        self.count = len(events)
        self.max_magnitude = max(magnitudes) if magnitudes else None
        # the centroid is good enough: a swarm is by definition compact
        self.lat = sum(e.lat or 0.0 for e in events) / self.count
        self.lon = sum(e.lon or 0.0 for e in events) / self.count
        self.started = self.events[0].time
        self.latest = self.events[-1].time

    @property
    def duration_hours(self) -> float:
        return (self.latest - self.started).total_seconds() / 3600.0

    @property
    def place(self) -> str:
        # the largest event names the swarm: it is the one people will have felt
        strongest = max(self.events, key=lambda e: e.magnitude or 0)
        return strongest.place


def detect(
    events: list[Event],
    radius_km: float = 30.0,
    window_hours: float = 24.0,
    min_count: int = 8,
) -> list[Swarm]:
    """Groups recent earthquakes into swarms.

    The thresholds are deliberately conservative. A tracker that cries "swarm"
    over five ordinary aftershocks teaches its readers to ignore it, and an
    alert nobody reads is worse than no alert.

    Clustering is single-link on distance: each quake joins the first cluster it
    is within `radius_km` of. It is O(n*k) rather than a real clustering
    algorithm, and that is on purpose -- with a few hundred events per window,
    a dependency-free pass beats a library we would have to keep alive.
    """
    cutoff = utcnow() - timedelta(hours=window_hours)
    quakes = [
        e
        for e in events
        if e.kind is Kind.EARTHQUAKE
        and e.lat is not None
        and e.lon is not None
        and e.time >= cutoff
        # a cluster's members must be distinct events, not the same quake seen
        # by three agencies
        and (e.cluster_id is None or e.cluster_id == e.id)
    ]

    # Carry the coordinates alongside: the filter above guarantees they exist,
    # but only a local binding makes that guarantee readable (and checkable).
    located = [
        (e, float(e.lat), float(e.lon)) for e in quakes if e.lat is not None and e.lon is not None
    ]

    clusters: list[list[Event]] = []
    heads: list[tuple[float, float]] = []
    for quake, lat, lon in sorted(located, key=lambda item: item[0].time):
        for index, (head_lat, head_lon) in enumerate(heads):
            if haversine_km(lat, lon, head_lat, head_lon) <= radius_km:
                clusters[index].append(quake)
                break
        else:
            clusters.append([quake])
            heads.append((lat, lon))

    return [Swarm(c) for c in clusters if len(c) >= min_count]


def as_event(swarm: Swarm) -> Event:
    """Turns a swarm into an event of its own, so it travels the same pipeline,
    appears in the same feed and can be filtered like anything else."""
    # Severity follows what is actually at stake: a swarm of micro-quakes is
    # noteworthy, a swarm containing a M5 is serious.
    severity = Severity.MODERATE
    if (swarm.max_magnitude or 0) >= 5.0 or swarm.count >= 30:
        severity = Severity.SEVERE

    return Event(
        # Keyed on LOCATION ONLY, rounded to about 11 km.
        #
        # The start time looked like the natural second half of the key, and it
        # was wrong: a swarm grows in both directions, so an older quake joining
        # moved `started` back an hour, changed the id, and spawned a second
        # marker for the same swarm. Location is the part that does not move --
        # the centroid of a cluster capped at 30 km barely drifts.
        #
        # The trade-off: two genuinely distinct swarms less than 11 km apart
        # merge into one entry. At that distance they are the same tectonic
        # story anyway, and `ongoing` plus the stale sweep retire the entry once
        # the quakes stop.
        id=f"swarm:{swarm.lat:.1f}:{swarm.lon:.1f}",
        source="swarm",
        source_id=f"{swarm.lat:.2f},{swarm.lon:.2f}",
        kind=Kind.EARTHQUAKE,
        time=swarm.latest,
        lat=swarm.lat,
        lon=swarm.lon,
        magnitude=swarm.max_magnitude,
        mag_type="max",
        place=swarm.place,
        severity=severity,
        # a swarm lasts as long as it keeps producing quakes
        ongoing=True,
        alert="swarm",
        title=(
            f"Seismic swarm -- {swarm.count} quakes in "
            f"{swarm.duration_hours:.0f} h near {swarm.place}"
        ),
        raw={
            "count": swarm.count,
            "duration_hours": round(swarm.duration_hours, 1),
            "max_magnitude": swarm.max_magnitude,
            "started": swarm.started.isoformat(),
            "member_ids": [e.id for e in swarm.events][:50],
        },
    )
