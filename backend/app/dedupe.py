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

from app.models.event import Event

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


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

    def assign(self, event: Event) -> Event:
        """Sets `cluster_id` on the event. Idempotent."""
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

        cutoff = event.time.timestamp() - self.window
        for other in reversed(self._recent):
            if other.id == event.id:
                event.cluster_id = other.cluster_id
                return event
            # the deque is chronological: past the window, nothing can match
            # anymore, so stop scanning it
            if other.time.timestamp() < cutoff - self.window:
                break
            if other.source == event.source:
                continue
            if other.lat is None or other.lon is None:
                continue
            if abs((other.time - event.time).total_seconds()) > self.window:
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
            self._recent.append(event)
            return event

        event.cluster_id = event.id
        self._recent.append(event)
        return event

    def is_primary(self, event: Event) -> bool:
        return event.cluster_id == event.id
