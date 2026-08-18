"""Normalized event model, shared by all sources."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


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
    # No position, forecast-oriented by nature, and its own iconography:
    # it does not belong in the OTHER catch-all with landslides and dust.
    SPACE_WEATHER = "space_weather"
    OTHER = "other"


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(value: str | None) -> datetime | None:
    """Parses an ISO timestamp and brings it back to UTC.

    The trap this helper exists to close: `fromisoformat("2026-08-17T10:00")`
    returns a NAIVE datetime, and `.astimezone(UTC)` then interprets it as the
    SERVER'S LOCAL time. A backend in Paris would silently shift every event
    of a source that omits its timezone by two hours -- no crash, just wrong
    times on an emergency product. Here, a timestamp without a timezone is
    declared UTC, which is what all our sources do.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Event(BaseModel):
    """An event, whatever its source."""

    id: str = Field(description="stable identifier: <source>:<source_id>")
    source: str
    source_id: str
    kind: Kind = Kind.OTHER

    # time of the event itself, and time when SOSForge saw it go by
    time: datetime
    received_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None
    # last time a source mentioned this event. Used to purge "ongoing" alerts
    # whose source has stopped publishing them: without it, a dissipated
    # cyclone or an old volcanic bulletin stayed displayed indefinitely, since
    # the ingestion horizon precisely exempts ongoing alerts.
    last_seen: datetime = Field(default_factory=utcnow)

    lat: float | None = None
    lon: float | None = None
    depth_km: float | None = None

    magnitude: float | None = None
    mag_type: str | None = None

    place: str = ""
    region: str | None = None
    country: str | None = None
    # ISO 3166-1 alpha-2 code, resolved in the pipeline. None when we cannot
    # honestly conclude (high seas): the UI then shows a globe.
    country_code: str | None = None

    severity: Severity = Severity.INFO
    # Event declared ONGOING by its source (active EONET fire, active NHC
    # storm, current GDACS alert). It escapes the ingestion horizon as long as
    # its source keeps publishing it -- the sweep of alerts gone silent is
    # what will remove it, not its age.
    ongoing: bool = False
    # When the SOURCE states an expiry (NWS, Meteoalarm and the CAP feeds all
    # do). An explicit end beats every heuristic we could apply: waiting for
    # six hours of silence to drop a tornado warning that expired at 15:00
    # keeps a red polygon over a quiet county all afternoon.
    expires: datetime | None = None
    tsunami: bool = False
    alert: str | None = Field(default=None, description="USGS PAGER: green/yellow/orange/red")

    title: str = ""
    url: str | None = None

    # Forecast positions, when the source publishes them (NHC cyclone tracks).
    # A first-class field rather than a corner of `raw`, because `public()`
    # strips `raw` before sending to the browser -- and a forecast the client
    # never receives is a forecast that does not exist.
    forecast_track: list[dict[str, Any]] | None = None

    # filled by the deduplicator: several sources describe the same event
    cluster_id: str | None = None
    revision: int = 0

    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("time", "received_at", "updated_at", "last_seen", "expires")
    @classmethod
    def _must_be_aware(cls, value: datetime | None) -> datetime | None:
        """A naive datetime compared to an aware one raises TypeError and kills
        the source that produced it. Every normalizer is careful about this;
        the model is where the guarantee belongs, so a future one cannot forget.
        Naive means UTC here, which is what all our sources publish."""
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)

    @field_validator("lat")
    @classmethod
    def _valid_latitude(cls, value: float | None) -> float | None:
        if value is not None and not -90 <= value <= 90:
            # Lesson 15: a wrong position is far worse than a missing one. Only
            # parse_iso6709 bounded anything, so any other source could inject a
            # point off the globe and nothing downstream would notice.
            raise ValueError(f"latitude out of range: {value}")
        return value

    @field_validator("lon")
    @classmethod
    def _valid_longitude(cls, value: float | None) -> float | None:
        if value is not None and not -180 <= value <= 180:
            raise ValueError(f"longitude out of range: {value}")
        return value

    @property
    def age_seconds(self) -> float:
        return (utcnow() - self.time).total_seconds()

    def fingerprint(self) -> str:
        """Content fingerprint: used to detect a revision of an event already seen."""
        parts = [
            f"{self.magnitude}",
            f"{round(self.lat, 3) if self.lat is not None else None}",
            f"{round(self.lon, 3) if self.lon is not None else None}",
            f"{self.depth_km}",
            self.place,
            self.severity.value,
            str(self.tsunami),
            # The END of the alert belongs here. Without it, a warning
            # re-issued with a later `ends` -- how NWS extends a tornado
            # warning, several times an hour -- looked identical to the one
            # already stored, and kept the old end: purged while still in
            # force, or left standing after being cut short.
            str(self.ongoing),
            self.expires.isoformat() if self.expires else "None",
        ]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]

    def public(self) -> dict[str, Any]:
        """Payload sent to the browser (without `raw`, which is heavy)."""
        return self.model_dump(mode="json", exclude={"raw"})


def severity_from_magnitude(mag: float | None, tsunami: bool = False) -> Severity:
    """In-house severity scale, calibrated on typical felt intensity/damage."""
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
