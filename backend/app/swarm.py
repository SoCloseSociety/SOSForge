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

from app.dedupe import SYNTHETIC_SOURCES, haversine_km
from app.models.event import Event, Kind, Severity, utcnow

log = logging.getLogger(__name__)


def _wrap(longitude: float) -> float:
    """Brings a longitude back into [-180, 180]."""
    return (longitude + 180.0) % 360.0 - 180.0


class Swarm:
    """A cluster of earthquakes close in space and time."""

    def __init__(self, events: list[Event], key: str | None = None):
        self.events = sorted(events, key=lambda e: e.time)
        # The id of the feed entry this swarm already has, when it has one.
        # Assigned by `detect()`, which is the only place that sees both the
        # new clusters and the entries left by the previous sweep.
        self.key = key
        magnitudes = [e.magnitude for e in events if e.magnitude is not None]
        self.count = len(events)
        self.max_magnitude = max(magnitudes) if magnitudes else None
        # The centroid is good enough -- a swarm is by definition compact --
        # but longitudes must be averaged UNWRAPPED. Kermadec, the Rat Islands
        # and Fiji sit on the 180th meridian and are among the most swarm-prone
        # ground on Earth: their quakes alternate between +179.9 and -179.9,
        # whose arithmetic mean is 0, in the Gulf of Guinea. Eighteen thousand
        # kilometres out, and the coordinate validators cannot catch it because
        # 0.0 is a perfectly valid longitude.
        self.lat = sum(e.lat or 0.0 for e in events) / self.count
        anchor = events[0].lon or 0.0
        offsets = [_wrap((e.lon or 0.0) - anchor) for e in events]
        self.lon = _wrap(anchor + sum(offsets) / self.count)
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
        # Our own bulletins are earthquakes by kind, primaries by cluster, and
        # sit at the very centroid of what they summarise. Read back from the
        # store, the previous sweep's entry joined the next cluster as a
        # member carrying the max magnitude -- a swarm counted itself, and
        # with seven real quakes the marker was the eighth that kept it alive.
        and e.source not in SYNTHETIC_SOURCES
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

    swarms = [Swarm(c) for c in clusters if len(c) >= min_count]
    _keep_identities(swarms, [e for e in events if e.source == "swarm"], radius_km)
    return swarms


def _keep_identities(swarms: list[Swarm], entries: list[Event], radius_km: float) -> None:
    """Gives each swarm the id of the entry it already has in the feed.

    The id used to BE the centroid rounded to 0.1 degree. A swarm drifting a
    few hundred metres across a rounding line got a new id and a second
    marker, and the first one stayed on the map until the silence sweep:
    live on 2026-10-26, 28 swarm entries of which 18 pairs sat closer than
    the 30 km that define one swarm.

    Matching is one to one and nearest first: two clusters near one old entry
    must not both write to it, or every sweep overwrites the other. A swarm
    with no entry yet is named by its rounded centroid as before, unless that
    name is taken -- single-link clustering can leave two distinct clusters
    whose centroids share a cell -- and then by its earliest member, which no
    other cluster can share.
    """
    candidates = sorted(
        (haversine_km(s.lat, s.lon, e.lat, e.lon), i, e.id)
        for i, s in enumerate(swarms)
        for e in entries
        if e.lat is not None and e.lon is not None
    )
    taken: set[str] = set()
    for distance, index, entry_id in candidates:
        if distance > radius_km:
            break
        if swarms[index].key is None and entry_id not in taken:
            swarms[index].key = entry_id
            taken.add(entry_id)
    for swarm in swarms:
        if swarm.key is None:
            name = _cell_key(swarm)
            swarm.key = name if name not in taken else f"swarm:{swarm.events[0].id}"
            taken.add(swarm.key)


def _cell_key(swarm: Swarm) -> str:
    return f"swarm:{swarm.lat:.1f}:{swarm.lon:.1f}"


def as_event(swarm: Swarm) -> Event:
    """Turns a swarm into an event of its own, so it travels the same pipeline,
    appears in the same feed and can be filtered like anything else."""
    # Severity follows what is actually at stake: a swarm of micro-quakes is
    # noteworthy, a swarm containing a M5 is serious.
    severity = Severity.MODERATE
    if (swarm.max_magnitude or 0) >= 5.0 or swarm.count >= 30:
        severity = Severity.SEVERE

    # Identity is the ENTRY the swarm already has (see `_keep_identities`),
    # not the place it is at now. The rounded centroid alone "barely drifts"
    # until it crosses a rounding line; the start time is no anchor either,
    # since a swarm grows in both directions. `ongoing` plus the stale sweep
    # retire the entry once the quakes stop.
    key = swarm.key or _cell_key(swarm)
    return Event(
        id=key,
        source="swarm",
        source_id=key.removeprefix("swarm:"),
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
