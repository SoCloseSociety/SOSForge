"""Cross-source dedup.

EMSC and USGS publish the same quake under two different identifiers, a few
seconds apart, with magnitudes that often diverge by 0.2 to 0.5. Nothing is
deleted -- events are clustered: the first to arrive becomes the cluster's
representative, later ones point at it. The UI shows only one representative
per cluster but can display both estimates.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import UTC, datetime, timedelta

from app.models.event import Event

EARTH_RADIUS_KM = 6371.0
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def _micros(moment: datetime) -> int:
    """Exact integer microseconds since the epoch: what `timedelta` arithmetic
    gives, without a float in between."""
    return (moment - _EPOCH) // _MICROSECOND


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


# Events the product generates about its own contents. They must never be
# compared with agency solutions, in either direction.
SYNTHETIC_SOURCES = frozenset({"swarm", "aftershock"})


class Deduper:
    def __init__(
        self,
        window_seconds: float = 90.0,
        radius_km: float = 250.0,
        mag_delta: float = 1.2,
        history: int = 800,
    ):
        self.window = window_seconds
        self.radius = radius_km
        self.mag_delta = mag_delta
        self._recent: deque[Event] = deque(maxlen=history)
        # Each entry's time, in the same order, as integer microseconds. The
        # scan below used to convert every entry's datetime twice per assign:
        # 1.38 million `timestamp()` calls for one startup replay, 0.37 s of
        # the deduper's 0.83 s, and live a 400-event poll held the event loop
        # for about 65 ms. Appended and rebuilt in lockstep with `_recent`
        # (same maxlen, so both drop their oldest together).
        self._micros: deque[int] = deque(maxlen=history)
        self._window_us = int(round(window_seconds * 1_000_000))
        # id -> its entry in the window. Each id is in the window at most once.
        self._index: dict[str, Event] = {}

    def assign(self, event: Event) -> Event:
        """Sets `cluster_id` on the event. Idempotent."""
        # SYNTHETIC events are ours: a swarm summary, an aftershock forecast.
        # They are built FROM events already in the store -- same place, same
        # time, same magnitude as the strongest member -- so the deduper
        # matched a swarm bulletin against the very quake it summarises and
        # marked our own alert a duplicate. During every live swarm, which is
        # the only time the feature matters, it was filtered out of the default
        # view. The reverse was worse: sitting in the window, the bulletin then
        # adopted the real quakes that followed, removing them from the feed
        # and from the swarm's own count.
        if event.source in SYNTHETIC_SOURCES:
            event.cluster_id = event.id
            return event

        if event.kind.value != "earthquake" or event.lat is None or event.lon is None:
            # These are NOT added to the history: they can never match (the
            # loop below only pairs located earthquakes), and adding them
            # flushed the window. Measured: ~146 re-emissions/minute from NWS,
            # GDACS and tsunami were enough to empty a deque of 800 in
            # 5.5 minutes -- while USGS publishes its solution 5 to 15 min
            # after the EMSC push. The EMSC/USGS dedup therefore missed its
            # target, and both solutions of the same quake showed up twice.
            event.cluster_id = event.cluster_id or event.id
            return event

        # An id already in the window answers from its OWN entry, first. The
        # scan used to look for it among the others, newest first: a row
        # re-listed on every poll met ANOTHER agency's newer copy of the same
        # quake before its own, matched it and was appended again. Two
        # agencies re-listing one quake added two entries per cycle; after
        # 399 cycles the 800-entry window held nothing else, an unrelated
        # quake had dropped out of it, and its late twin from a third agency
        # was shown as a quake of its own (lane Q, usgs:us7000ttfe and
        # emsc:20261027_0000114 both on the live map). The store keeps the
        # first cluster of a stored id anyway, so only the churn goes.
        own = self._index.get(event.id)
        if own is not None:
            event.cluster_id = own.cluster_id
            return event

        at = _micros(event.time)
        # anything older than this cannot be within the window of `at`
        floor = at - 2 * self._window_us
        for other, other_at in zip(reversed(self._recent), reversed(self._micros), strict=True):
            # NO early break here, and no ordering assumption at all. The deque
            # is ordered by ARRIVAL, not by event time: a catalog bulletin dated
            # twenty hours ago can arrive after a push dated one second ago.
            # Breaking on the first old entry stopped the scan right in front of
            # the match sitting behind it -- and finding that match is the
            # deduper's entire job.
            if other_at < floor:
                continue
            if other.source == event.source:
                continue
            if other.lat is None or other.lon is None:
                continue
            if abs(other_at - at) > self._window_us:
                continue
            if haversine_km(event.lat, event.lon, other.lat, other.lon) > self.radius:
                continue
            if (
                event.magnitude is not None
                and other.magnitude is not None
                and abs(event.magnitude - other.magnitude) > self.mag_delta
            ):
                continue
            event.cluster_id = other.cluster_id or other.id
            self._append(event, at)
            return event

        event.cluster_id = event.id
        self._append(event, at)
        return event

    def _append(self, event: Event, at: int) -> None:
        if len(self._recent) == self._recent.maxlen:
            # the deque is about to drop its oldest entry: so does the index
            oldest = self._recent[0]
            if self._index.get(oldest.id) is oldest:
                del self._index[oldest.id]
        self._recent.append(event)
        self._micros.append(at)
        self._index[event.id] = event

    def forget(self, event_ids: set[str]) -> int:
        """Drops events the store no longer has.

        The deque was an append-only shadow of the store with no notion of
        liveness, so it kept speaking for events that had been retracted. A
        cancelled early warning stayed in the window and adopted the real
        solutions that arrived after it -- and `primary_only`, which is what
        /api/events and the websocket snapshot use, hides anything whose
        cluster is not itself. The quake was ingested, stored, and shown to
        nobody.
        """
        if not event_ids:
            return 0
        pairs = zip(self._recent, self._micros, strict=True)
        survivors = [(e, at) for e, at in pairs if e.id not in event_ids]
        dropped = len(self._recent) - len(survivors)
        self._recent.clear()
        self._micros.clear()
        self._recent.extend(e for e, _ in survivors)
        self._micros.extend(at for _, at in survivors)
        self._index = {e.id: e for e in self._recent}
        # Anything that had been adopted by a removed event is orphaned: it
        # becomes its own representative rather than pointing at a ghost.
        for event in self._recent:
            if event.cluster_id in event_ids:
                event.cluster_id = event.id
        return dropped

    def is_primary(self, event: Event) -> bool:
        return event.cluster_id == event.id
