"""Normalized event model, shared by all sources."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


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
    # An automatic solution, not yet reviewed by a human. The first automatic
    # magnitude of a large quake is routinely off by up to a full unit and
    # relocated by tens of kilometres in the minutes that follow. Every agency
    # labels it; so must we, or a screenshot of the wrong number travels
    # further than the correction.
    preliminary: bool = False
    # Shaking, as opposed to size. The only quantity that answers the question
    # the person who just felt it is actually asking, and magnitude does not.
    intensity_mmi: float | None = None
    # How many people reported feeling it. Absent is NOT zero: most of the
    # planet has no reporters, and a zero would be a claim we cannot make.
    felt_reports: int | None = None
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

    @field_validator("url")
    @classmethod
    def _only_real_links(cls, value: str | None) -> str | None:
        """A feed's `url` ends up in an `href`. Escaping protects the
        ATTRIBUTE, never the SCHEME: `javascript:alert(1)` survives every
        escape intact and becomes a link that runs as the page.

        The live site's Content-Security-Policy blocks that today, but that
        policy lives in an nginx file on one VPS, outside this repository. Any
        redeploy from this repo, any second host, any container run on its own
        has no such protection -- so the product carries its own.

        Only absolute http(s) survives. A relative URL is dropped too: nothing
        here legitimately serves its own pages from a feed value.
        """
        if value is None:
            return None
        # Control characters are the classic way to smuggle a scheme past a
        # naive check: `java\tscript:` is read as `javascript:` by browsers.
        cleaned = "".join(c for c in value if c.isprintable()).strip()
        if not cleaned:
            return None
        scheme, _, rest = cleaned.partition(":")
        if scheme.lower() not in ("http", "https") or not rest.startswith("//"):
            log.debug("dropped a non-http link from a feed: %.60s", value)
            return None
        return cleaned

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


# Below this depth, a large quake is a long slow sway a thousand kilometres
# wide and damages almost nothing. Fiji-Tonga, Vrancea and the Sea of Okhotsk
# produce M7s at these depths routinely.
DEEP_FOCUS_KM = 300.0
INTERMEDIATE_KM = 150.0

_LADDER = list(Severity)


def _step(severity: Severity, by: int) -> Severity:
    index = max(0, min(len(_LADDER) - 1, _LADDER.index(severity) + by))
    return _LADDER[index]


def severity_for_quake(
    mag: float | None,
    depth_km: float | None = None,
    pager: str | None = None,
) -> Severity:
    """Magnitude is where this starts, not where it ends.

    A M7.2 at 600 km under the Sea of Okhotsk is felt as a sway and breaks
    nothing. A M6.2 at 10 km under a city is a mass-casualty event. Ranking
    them on magnitude alone put the harmless one above the deadly one, and the
    depth was sitting in the event the whole time, unread.

    `pager` is the USGS PAGER estimate, which already folds in depth AND the
    population actually exposed -- when it speaks, it knows more than we do.
    """
    severity = severity_from_magnitude(mag)

    if depth_km is not None and depth_km >= DEEP_FOCUS_KM:
        # never the top alarm, whatever the magnitude
        severity = min(severity, Severity.MODERATE, key=_LADDER.index)
    elif depth_km is not None and depth_km >= INTERMEDIATE_KM:
        severity = _step(severity, -1)

    # PAGER only ever promotes. A green estimate on a M7.4 means "few
    # casualties expected", not "small quake", and demoting on it would hide
    # an event that is about to be revised.
    if pager:
        rank = pager.strip().lower()
        if rank == "red":
            severity = Severity.EXTREME
        elif rank == "orange":
            severity = max(severity, Severity.SEVERE, key=_LADDER.index)

    return severity


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
