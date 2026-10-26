"""What one client wants to hear about: zones, a severity floor, kinds.

A scope is parsed once from query parameters (websocket or REST), validated
strictly, and then asked one question per event: is this in scope. The same
object serves the websocket fan-out, the opening snapshot and `/api/events`,
so a phone polling as a fallback sees exactly what its socket would send.

Position matching is a great-circle distance to the event's POINT, and,
when the source published a shape, to its bounding box (`Event.bbox`: west,
south, east, north; `west > east` crosses the antimeridian). A warning area
whose centre is 400 km away can still cover a watched place; matching the
centroid alone missed it. The box is filled by the NWS, WMO CAP, UK
Meteoalarm and ash normalizers; GDACS is left out on purpose (its box is a
fixed square around the point, not a footprint). An event without a box
still matches by its point only.

An event with NO position cannot be matched by distance at all (a Meteoalarm
warning, most of the WMO aggregate). What a zoned client gets for those is
its own explicit choice, `unplaced`:

- `drop` (default): none of them, and the count it did not get is reported
  next to the result, so the gap is visible instead of silent;
- `XX,YY`: the ones whose country is in that ISO 3166-1 alpha-2 list -- the
  only locator such an alert carries, the rule the local agent already uses;
- `all`: every one of them.

The default is `drop` because the server cannot know the country of a zone
(there is no offline point-to-country table here) and because country alone
is measured to be useless for a country the size of the United States
(lesson 24). The client knows its own country; it is the one that says so.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.event import Event, Kind, Severity

EARTH_RADIUS_KM = 6371.0088
MAX_ZONES = 10
MIN_RADIUS_KM = 1.0
# The local agent caps a felt radius at 2000 km, and nothing a phone wants to
# be woken for is further than that from the phone.
MAX_RADIUS_KM = 2000.0
MAX_COUNTRIES = 10

SEVERITY_RANK = {s.value: i for i, s in enumerate(Severity)}
SEVERE_RANK = SEVERITY_RANK[Severity.SEVERE.value]
SCOPE_PARAMS = ("zones", "min_severity", "kinds", "unplaced")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


class ScopeError(ValueError):
    """A parameter we refuse. `code` is stable for machines, `reason` for people."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


@dataclass(frozen=True)
class Zone:
    lat: float
    lon: float
    radius_km: float

    def contains(self, lat: float, lon: float) -> bool:
        return haversine_km(self.lat, self.lon, lat, lon) <= self.radius_km

    def touches(self, bbox: Sequence[float]) -> bool:
        """Does this zone reach the box (west, south, east, north)?

        The nearest point of the box is taken by clamping latitude and
        longitude separately. That is exact across the north and south edges
        and slightly overstates the gap to an east or west edge far from the
        equator (the parallel is not a great circle): a few tens of km at the
        largest radius, never a box that contains the zone's centre."""
        west, south, east, north = bbox
        lat = min(max(self.lat, south), north)
        if _lon_within(self.lon, west, east):
            lon = self.lon
        else:
            lon = west if _lon_gap(self.lon, west) <= _lon_gap(self.lon, east) else east
        return haversine_km(self.lat, self.lon, lat, lon) <= self.radius_km

    def public(self) -> dict[str, float]:
        return {"lat": self.lat, "lon": self.lon, "radius_km": self.radius_km}


def _lon_within(lon: float, west: float, east: float) -> bool:
    if west <= east:
        return west <= lon <= east
    return lon >= west or lon <= east  # the box crosses the antimeridian


def _lon_gap(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class Unplaced:
    """Events that passed the severity and kind filters but carry no position.

    `dropped` is what a zoned client did NOT get because of that, `delivered`
    what it got anyway through its `unplaced` choice (country or all). Both
    are zero for a scope without zones: nothing is decided by distance there.
    """

    dropped: int = 0
    delivered: int = 0

    def public(self) -> dict[str, int]:
        return {"dropped": self.dropped, "delivered": self.delivered}


@dataclass(frozen=True)
class Scope:
    zones: tuple[Zone, ...] = ()
    min_severity: int = 0
    kinds: frozenset[str] | None = None
    # `drop`, `all`, or `country` (then `countries` says which)
    unplaced: str = "drop"
    countries: frozenset[str] = frozenset()

    @property
    def zoned(self) -> bool:
        return bool(self.zones)

    def matches(self, event: Event) -> bool:
        return self.matches_fields(
            event.lat,
            event.lon,
            event.severity.value,
            event.kind.value,
            event.country_code,
            event.bbox,
        )

    def matches_public(self, event: dict[str, Any]) -> bool:
        """Same question, asked of the `public()` dict that travels in a
        broadcast. The fan-out never rebuilds the model, so the match must not
        either."""
        return self.matches_fields(
            event.get("lat"),
            event.get("lon"),
            str(event.get("severity") or "info"),
            str(event.get("kind") or "other"),
            event.get("country_code"),
            event.get("bbox"),
        )

    def passes_filters(self, severity: str, kind: str) -> bool:
        """The non-geographic half of the question."""
        if SEVERITY_RANK.get(severity, 0) < self.min_severity:
            return False
        return self.kinds is None or kind in self.kinds

    def takes_unplaced(self, country_code: str | None) -> bool:
        if self.unplaced == "all":
            return True
        if self.unplaced == "country":
            return bool(country_code) and str(country_code).upper() in self.countries
        return False

    def matches_fields(
        self,
        lat: float | None,
        lon: float | None,
        severity: str,
        kind: str,
        country_code: str | None = None,
        bbox: Sequence[float] | None = None,
    ) -> bool:
        if not self.passes_filters(severity, kind):
            return False
        if not self.zones:
            return True
        if bbox is not None and any(z.touches(bbox) for z in self.zones):
            return True
        if lat is None or lon is None:
            return bbox is None and self.takes_unplaced(country_code)
        return any(z.contains(lat, lon) for z in self.zones)

    def apply(self, events: Iterable[Event]) -> tuple[list[Event], Unplaced]:
        """The events in scope, in the order given, and the unplaced count."""
        kept: list[Event] = []
        dropped = delivered = 0
        for event in events:
            if not self.passes_filters(event.severity.value, event.kind.value):
                continue
            if self.zoned and event.bbox is None and (event.lat is None or event.lon is None):
                if self.takes_unplaced(event.country_code):
                    delivered += 1
                    kept.append(event)
                else:
                    dropped += 1
                continue
            if self.matches(event):
                kept.append(event)
        return kept, Unplaced(dropped, delivered)

    def public(self) -> dict[str, Any]:
        """Echoed back to the client: what the server understood."""
        return {
            "zones": [z.public() for z in self.zones],
            "min_severity": list(Severity)[self.min_severity].value,
            "kinds": sorted(self.kinds) if self.kinds is not None else None,
            "unplaced": (
                ",".join(sorted(self.countries)) if self.unplaced == "country" else self.unplaced
            ),
        }


# ------------------------------------------------------------------- parsing


def _number(text: str, what: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ScopeError("bad_zones", f"{what} is not a number: {text!r}") from None
    if not math.isfinite(value):
        raise ScopeError("bad_zones", f"{what} is not finite: {text!r}")
    return value


def parse_zone(text: str) -> Zone:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3 or not all(parts):
        raise ScopeError("bad_zones", f"a zone is lat,lon,radius_km -- got {text!r}")
    lat = _number(parts[0], "latitude")
    lon = _number(parts[1], "longitude")
    radius = _number(parts[2], "radius_km")
    if not -90 <= lat <= 90:
        raise ScopeError("bad_zones", f"latitude out of range: {lat:g}")
    if not -180 <= lon <= 180:
        raise ScopeError("bad_zones", f"longitude out of range: {lon:g}")
    if not MIN_RADIUS_KM <= radius <= MAX_RADIUS_KM:
        raise ScopeError(
            "bad_zones",
            f"radius_km must be within {MIN_RADIUS_KM:g}..{MAX_RADIUS_KM:g}: {radius:g}",
        )
    return Zone(lat, lon, radius)


def parse_zones(text: str | None) -> tuple[Zone, ...]:
    if text is None:
        return ()
    chunks = [c for c in text.split(";") if c.strip()]
    if not chunks:
        # present but empty: a client that meant to send its zones and sent
        # nothing must not silently receive the whole planet
        raise ScopeError("bad_zones", "zones is empty")
    if len(chunks) > MAX_ZONES:
        raise ScopeError("bad_zones", f"at most {MAX_ZONES} zones, got {len(chunks)}")
    return tuple(parse_zone(c) for c in chunks)


def parse_min_severity(text: str | None) -> int:
    if text is None:
        return 0
    key = text.strip().lower()
    if key not in SEVERITY_RANK:
        raise ScopeError(
            "bad_min_severity",
            f"min_severity must be one of {', '.join(SEVERITY_RANK)} -- got {text!r}",
        )
    return SEVERITY_RANK[key]


def parse_kinds(text: str | None) -> frozenset[str] | None:
    if text is None:
        return None
    valid = {k.value for k in Kind}
    wanted = {k.strip().lower() for k in text.split(",") if k.strip()}
    if not wanted:
        raise ScopeError("bad_kinds", "kinds is empty")
    unknown = sorted(wanted - valid)
    if unknown:
        raise ScopeError(
            "bad_kinds",
            f"unknown kind(s): {', '.join(unknown)} (valid: {', '.join(sorted(valid))})",
        )
    return frozenset(wanted)


def parse_unplaced(text: str | None) -> tuple[str, frozenset[str]]:
    if text is None:
        return "drop", frozenset()
    key = text.strip()
    if key.lower() in ("drop", "all"):
        return key.lower(), frozenset()
    codes = [c.strip().upper() for c in key.split(",") if c.strip()]
    if not codes or not all(_COUNTRY.match(c) for c in codes):
        raise ScopeError(
            "bad_unplaced",
            f"unplaced is drop, all, or a list of ISO 3166-1 alpha-2 codes -- got {text!r}",
        )
    if len(codes) > MAX_COUNTRIES:
        raise ScopeError("bad_unplaced", f"at most {MAX_COUNTRIES} countries, got {len(codes)}")
    return "country", frozenset(codes)


def parse_scope(params: dict[str, str | None]) -> Scope | None:
    """The query parameters of a request, turned into a scope.

    Returns None when NONE of the scope parameters is present: that is the
    legacy client, and it gets the legacy behaviour. A parameter that is
    present but invalid raises `ScopeError`; it is never ignored, because a
    client that asked for Tokyo and silently got the whole planet would
    believe every alert it receives is nearby.
    """
    present = {k: params.get(k) for k in SCOPE_PARAMS if params.get(k) is not None}
    if not present:
        return None
    zones = parse_zones(present.get("zones"))
    unplaced, countries = parse_unplaced(present.get("unplaced"))
    if "unplaced" in present and not zones:
        raise ScopeError("bad_unplaced", "unplaced only means something with zones")
    return Scope(
        zones=zones,
        min_severity=parse_min_severity(present.get("min_severity")),
        kinds=parse_kinds(present.get("kinds")),
        unplaced=unplaced,
        countries=countries,
    )


def parse_point_scope(lat: float | None, lon: float | None, radius_km: float | None) -> str | None:
    """`/api/events` takes `lat`, `lon`, `radius_km` as three separate numeric
    parameters. All three or none: returns the equivalent `zones` text, or
    None when none is given, and raises when the triple is incomplete."""
    given = [v is not None for v in (lat, lon, radius_km)]
    if not any(given):
        return None
    if not all(given):
        raise ScopeError("bad_zones", "lat, lon and radius_km go together: give all three or none")
    return f"{lat},{lon},{radius_km}"


def pinned(event: Event) -> bool:
    """An event a snapshot must carry whatever its age: still running, or
    dangerous. The latest-N cut reaches back only hours on a busy day, and a
    day-old red cyclone or an ongoing hurricane-force wind warning fell off a
    fresh tab entirely."""
    return event.ongoing or event.tsunami or SEVERITY_RANK[event.severity.value] >= SEVERE_RANK


def compose(events: list[Event], size: int, scope: Scope | None) -> tuple[list[Event], Unplaced]:
    """A snapshot (or a v2 full read): the latest `size` events of the scope,
    plus every older one that is `pinned`.

    `events` must be sorted newest first (what `EventStore.recent` returns).
    The scope is applied BEFORE the size cut, otherwise a Tokyo client would
    get the few Tokyo events that happen to sit in the planet's latest 300.
    """
    unplaced = Unplaced()
    if scope is not None:
        events, unplaced = scope.apply(events)
    head = events[:size]
    tail = [e for e in events[size:] if pinned(e)]
    return head + tail, unplaced
