"""Dedup inter-sources.

EMSC et USGS publient le meme seisme sous deux identifiants differents, a quelques
secondes d'ecart, avec des magnitudes qui divergent souvent de 0.2 a 0.5. On ne
supprime rien -- on regroupe: le premier arrive devient le representant du cluster,
les suivants pointent dessus. L'UI n'affiche qu'un representant par cluster mais
peut montrer les deux estimations.
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
        """Pose `cluster_id` sur l'evenement. Idempotent."""
        if event.kind.value != "earthquake" or event.lat is None or event.lon is None:
            # On ne les met PAS dans l'historique: ils ne peuvent jamais matcher
            # (la boucle ci-dessous n'apparie que des seismes localises), et les
            # y mettre balayait la fenetre. Mesure: ~146 re-emissions/minute de
            # NWS, GDACS et tsunami suffisaient a vider une deque de 800 en
            # 5,5 minutes -- alors que l'USGS publie sa solution 5 a 15 min apres
            # le push EMSC. Le dedup EMSC/USGS ratait donc sa cible, et les deux
            # solutions du meme seisme s'affichaient en double.
            event.cluster_id = event.cluster_id or event.id
            return event

        cutoff = event.time.timestamp() - self.window
        for other in reversed(self._recent):
            if other.id == event.id:
                event.cluster_id = other.cluster_id
                return event
            # la deque est chronologique: passe la fenetre, plus rien ne peut
            # matcher, on arrete de la parcourir
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
