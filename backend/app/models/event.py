"""Modele d'evenement normalise, commun a toutes les sources."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Kind(str, Enum):
    EARTHQUAKE = "earthquake"
    TSUNAMI = "tsunami"
    VOLCANO = "volcano"
    CYCLONE = "cyclone"
    FLOOD = "flood"
    WILDFIRE = "wildfire"
    DROUGHT = "drought"
    STORM = "storm"
    HEAT = "heat"
    OTHER = "other"


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    """Un evenement, quelle que soit sa source."""

    id: str = Field(description="identifiant stable: <source>:<source_id>")
    source: str
    source_id: str
    kind: Kind = Kind.OTHER

    # temps de l'evenement lui-meme, et temps ou SOSForge l'a vu passer
    time: datetime
    received_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None

    lat: float | None = None
    lon: float | None = None
    depth_km: float | None = None

    magnitude: float | None = None
    mag_type: str | None = None

    place: str = ""
    region: str | None = None
    country: str | None = None
    # code ISO 3166-1 alpha-2, resolu dans le pipeline. None quand on ne peut pas
    # conclure honnetement (haute mer): l'interface affiche alors un globe.
    country_code: str | None = None

    severity: Severity = Severity.INFO
    tsunami: bool = False
    alert: str | None = Field(default=None, description="PAGER USGS: green/yellow/orange/red")

    title: str = ""
    url: str | None = None

    # rempli par le deduplicateur: plusieurs sources decrivent le meme evenement
    cluster_id: str | None = None
    revision: int = 0

    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def age_seconds(self) -> float:
        return (utcnow() - self.time).total_seconds()

    def fingerprint(self) -> str:
        """Empreinte du contenu: sert a detecter une revision d'un evenement deja vu."""
        parts = [
            f"{self.magnitude}",
            f"{round(self.lat, 3) if self.lat is not None else None}",
            f"{round(self.lon, 3) if self.lon is not None else None}",
            f"{self.depth_km}",
            self.place,
            self.severity.value,
            str(self.tsunami),
        ]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]

    def public(self) -> dict[str, Any]:
        """Payload envoye au navigateur (sans le `raw`, qui est lourd)."""
        return self.model_dump(mode="json", exclude={"raw"})


def severity_from_magnitude(mag: float | None, tsunami: bool = False) -> Severity:
    """Echelle de gravite maison, calee sur le ressenti/degats typiques."""
    if tsunami:
        return Severity.EXTREME
    if mag is None:
        return Severity.INFO
    if mag >= 7.0:
        return Severity.EXTREME
    if mag >= 6.0:
        return Severity.SEVERE
    if mag >= 4.5:
        return Severity.MODERATE
    if mag >= 2.5:
        return Severity.MINOR
    return Severity.INFO
