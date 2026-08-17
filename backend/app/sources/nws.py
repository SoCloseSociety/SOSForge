"""NWS api.weather.gov -- alertes officielles US, tous aleas.

C'est la source la plus reactive du lot pour les alertes: elle publie a la seconde
ou presque (cache 5s cote NWS), et couvre ce que les feeds sismiques ignorent --
tornades, crues eclair, vents violents, chaleur extreme, et les Tsunami Warning
cote americain.

Le NWS impose un User-Agent identifiant: sans lui, on se fait bloquer.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

import httpx

from app.models.event import Event, Kind, Severity, to_utc
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

URL = (
    "https://api.weather.gov/alerts/active?status=actual&message_type=alert&severity=Extreme,Severe"
)

NWS_SEVERITY = {
    "Extreme": Severity.EXTREME,
    "Severe": Severity.SEVERE,
    "Moderate": Severity.MODERATE,
    "Minor": Severity.MINOR,
    "Unknown": Severity.INFO,
}

# Ordre important: le premier motif trouve gagne ("Tsunami" avant "Flood").
#
# Les motifs sont des MOTS ENTIERS, jamais des sous-chaines. La recherche par
# sous-chaine fabrique des faux positifs invisibles: "Flash Flood" contient
# "ash" et devenait une alerte volcanique dans le classifieur voisin. Le meme
# mecanisme a produit des facettes fantomes dans un autre produit de la suite
# ("rate" trouve dans "curated"). On ne le laisse pas en place ici.
KIND_PATTERNS: list[tuple[tuple[str, ...], Kind]] = [
    (("tsunami",), Kind.TSUNAMI),
    (("volcano", "volcanic", "ash", "ashfall"), Kind.VOLCANO),
    (("hurricane", "tropical", "typhoon", "surge"), Kind.CYCLONE),
    (("flood", "flooding"), Kind.FLOOD),
    (("fire", "wildfire", "flag"), Kind.WILDFIRE),
    (("earthquake",), Kind.EARTHQUAKE),
    (("heat",), Kind.HEAT),
    (
        (
            "tornado",
            "thunderstorm",
            "wind",
            "blizzard",
            "winter",
            "ice",
            "snow",
            "rain",
            "freeze",
            "freezing",
            "frost",
            "gale",
        ),
        Kind.STORM,
    ),
    (("drought",), Kind.DROUGHT),
]


def classify(event_name: str) -> Kind:
    words = set(re.findall(r"[a-z]+", (event_name or "").lower()))
    for patterns, kind in KIND_PATTERNS:
        if words & set(patterns):
            return kind
    return Kind.OTHER


def _centroid(geometry: dict | None) -> tuple[float | None, float | None]:
    """Les alertes NWS sont des polygones (ou rien du tout quand la zone est
    decrite par des codes UGC). On reduit au barycentre pour poser un point."""
    if not geometry:
        return None, None
    coords = geometry.get("coordinates")
    points: list[tuple[float, float]] = []

    def walk(node) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) == 2
            and all(isinstance(v, (int, float)) for v in node)
        ):
            points.append((float(node[0]), float(node[1])))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(coords)
    if not points:
        return None, None
    lon = sum(p[0] for p in points) / len(points)
    lat = sum(p[1] for p in points) / len(points)
    return lat, lon


def _parse_time(value: str | None) -> datetime:
    return to_utc(value) or datetime.now(UTC)


def parse_feature(feature: dict) -> Event | None:
    props = feature.get("properties") or {}
    alert_id = props.get("id") or feature.get("id")
    if not alert_id:
        return None

    event_name = props.get("event") or "alerte"
    kind = classify(event_name)
    lat, lon = _centroid(feature.get("geometry"))
    severity = NWS_SEVERITY.get(props.get("severity") or "", Severity.INFO)

    return Event(
        id=f"nws:{str(alert_id).rsplit(':', 1)[-1]}",
        source="nws",
        source_id=str(alert_id),
        kind=kind,
        time=_parse_time(props.get("sent") or props.get("effective")),
        lat=lat,
        lon=lon,
        place=props.get("areaDesc") or "zone non precisee",
        country="United States",
        severity=severity,
        tsunami=kind is Kind.TSUNAMI,
        alert=(props.get("urgency") or "").lower() or None,
        title=props.get("headline") or event_name,
        url=props.get("@id") or f"https://api.weather.gov/alerts/{alert_id}",
        raw={
            "event": event_name,
            "urgency": props.get("urgency"),
            "certainty": props.get("certainty"),
            "expires": props.get("expires"),
            "sender": props.get("senderName"),
        },
    )


class NwsSource(Source):
    name = "nws"
    kind = "poll"

    def __init__(self, poll_seconds: float = 20.0, url: str = URL):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url

    async def run(self, emit: Emit) -> None:
        headers = {
            "User-Agent": "SOSForge/1.0 (contact: ops@soclose.co)",
            "Accept": "application/geo+json",
        }
        async with httpx.AsyncClient(
            timeout=25.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    features = (resp.json() or {}).get("features") or []
                    for feature in features:
                        event = parse_feature(feature)
                        if event:
                            await emit(event)
                    self.health.ok(len(features))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("nws: %s", exc)
                await asyncio.sleep(self.poll_seconds)
