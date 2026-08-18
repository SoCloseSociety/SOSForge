"""High-value non-seismic hazards: tropical cyclones and volcanic ash.

GDACS sees cyclones, but coarsely and with delay. The NHC publishes the
position, winds and category of every active storm at each advisory. And for
volcanic ash, the VAACs only publish heterogeneous text: aviation SIGMETs are
its structured operational translation, and the only machine-readable
worldwide feed that exists for this hazard.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.models.event import Event, Kind, Severity, to_utc
from app.sources.regional import JsonPollSource

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- NHC


# Saffir-Simpson scale, in knots. A tropical storm is not a major hurricane:
# severity must follow the wind, not the fact that the storm has a name.
def cyclone_severity(wind_kt: float | None, classification: str) -> Severity:
    if classification in ("HU", "MH", "TY", "STY"):
        if wind_kt is not None and wind_kt >= 96:  # category 3+
            return Severity.EXTREME
        return Severity.SEVERE
    if classification in ("TS", "STS"):
        return Severity.MODERATE
    return Severity.MINOR


class NhcSource(JsonPollSource):
    """National Hurricane Center: Atlantic, East and Central Pacific basins."""

    name = "nhc"
    kind = "poll"
    url = "https://www.nhc.noaa.gov/CurrentStorms.json"

    def parse_payload(self, data) -> list[Event]:
        events: list[Event] = []
        for storm in (data or {}).get("activeStorms") or []:
            storm_id = storm.get("id")
            if not storm_id:
                continue

            # everything is a string in this feed, including the numbers
            def number(value) -> float | None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            wind_kt = number(storm.get("intensity"))
            pressure = number(storm.get("pressure"))
            classification = (storm.get("classification") or "").upper()

            # latitude "20.4N" is a string: the *Numeric fields are the right ones
            lat = storm.get("latitudeNumeric")
            lon = storm.get("longitudeNumeric")

            time = to_utc(storm.get("lastUpdate"))
            if time is None:
                continue

            name = storm.get("name") or "unnamed"
            advisory = (storm.get("publicAdvisory") or {}).get("url")

            events.append(
                Event(
                    # `id` is stable for the whole season; `binNumber` (CP2) is
                    # recycled from one storm to the next and must never be a key
                    id=f"nhc:{storm_id}",
                    source="nhc",
                    source_id=str(storm_id),
                    kind=Kind.CYCLONE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    magnitude=wind_kt,
                    mag_type="kt",
                    place=name,
                    severity=cyclone_severity(wind_kt, classification),
                    # CurrentStorms lists ONLY active storms
                    ongoing=True,
                    alert=classification.lower() or None,
                    title=f"{classification} {name} -- {wind_kt or '?'} kt",
                    url=advisory or "https://www.nhc.noaa.gov/",
                    raw={
                        "classification": classification,
                        "pressure_mb": pressure,
                        "movement_dir": storm.get("movementDir"),
                        "movement_speed_kt": storm.get("movementSpeed"),
                        "basin": storm.get("binNumber"),
                    },
                )
            )
        return events


# ---------------------------------------------------------------------- ash (VA)


class AshSource(JsonPollSource):
    """International volcanic ash SIGMETs (Aviation Weather Center).

    A SIGMET has no event identifier: it is re-issued every six hours with a
    new serial number. The key is therefore composite (FIR + serial + start of
    validity), otherwise each re-issue would create a duplicate on the map.
    """

    name = "ash"
    kind = "poll"
    url = "https://aviationweather.gov/api/data/isigmet?format=json&hazard=VA"

    def parse_payload(self, data) -> list[Event]:
        events: list[Event] = []
        for sigmet in data or []:
            fir = sigmet.get("firId") or sigmet.get("icaoId")
            series = sigmet.get("seriesId") or "0"
            valid_from = sigmet.get("validTimeFrom")
            if not fir or valid_from is None:
                continue

            try:
                # this feed mixes epoch SECONDS (validTime*) and ISO Z (receiptTime)
                time = datetime.fromtimestamp(float(valid_from), tz=UTC)
            except (TypeError, ValueError, OSError):
                continue

            # the polygon gives the cloud's extent; we place the point at its center
            coords = sigmet.get("coords") or []
            lat = lon = None
            if coords:
                lats = [c.get("lat") for c in coords if c.get("lat") is not None]
                lons = [c.get("lon") for c in coords if c.get("lon") is not None]
                if lats and lons:
                    lat = sum(lats) / len(lats)
                    lon = sum(lons) / len(lons)

            volcano = sigmet.get("qualifier") or "unnamed volcano"
            top_ft = sigmet.get("top")
            # a plume that rises high is a dangerous plume
            severity = Severity.SEVERE if (top_ft or 0) >= 25000 else Severity.MODERATE

            events.append(
                Event(
                    id=f"ash:{fir}:{series}:{int(float(valid_from))}",
                    source="ash",
                    source_id=f"{fir}-{series}",
                    kind=Kind.VOLCANO,
                    time=time,
                    lat=lat,
                    lon=lon,
                    place=volcano.title(),
                    severity=severity,
                    alert="ash",
                    title=f"Volcanic ash -- {volcano.title()}",
                    url="https://aviationweather.gov/gfa/#sigmet",
                    raw={
                        "fir": sigmet.get("firName"),
                        # `top` is in feet, not meters
                        "top_ft": top_ft,
                        "base_ft": sigmet.get("base"),
                        "direction": sigmet.get("dir"),
                        "speed_kt": sigmet.get("spd"),
                    },
                )
            )
        return events


# ------------------------------------------------------------------------- EONET


def centroid(coords) -> tuple[float | None, float | None]:
    """Centroid of a GeoJSON geometry of arbitrary nesting depth."""
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
    return (
        sum(p[1] for p in points) / len(points),
        sum(p[0] for p in points) / len(points),
    )


# EONET classifies by category; we map back to our own hazard types.
EONET_KIND = {
    "wildfires": Kind.WILDFIRE,
    "severeStorms": Kind.CYCLONE,
    "volcanoes": Kind.VOLCANO,
    "floods": Kind.FLOOD,
    "drought": Kind.DROUGHT,
    "earthquakes": Kind.EARTHQUAKE,
    "landslides": Kind.OTHER,
    "seaLakeIce": Kind.OTHER,
    "snow": Kind.STORM,
    "dustHaze": Kind.OTHER,
    "manmade": Kind.OTHER,
    "waterColor": Kind.OTHER,
    "temperatureExtremes": Kind.HEAT,
}


class EonetSource(JsonPollSource):
    """NASA EONET -- ongoing natural events, observed from space.

    What it brings that nothing else here has: **wildfires** tracked as events
    (with a stable identifier and a trajectory), where FIRMS only gives hot
    pixels to cluster yourself and GDACS only sees the biggest ones. It is
    also a worldwide second opinion on storms.

    Latency: EONET aggregates sources ranging from a minute to a few hours.
    So it is not "live" in the EMSC sense, and the pipeline's `breaking` flag
    handles that by itself by looking at the event's real age.
    """

    name = "eonet"
    kind = "poll"
    # `days=14`: EONET keeps events "open" for a very long time (an unclosed
    # fire stays open for weeks after its last observation). Without this
    # bound, 250 fires, many dormant for a month, drowned the map.
    url = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=14&limit=200"

    def parse_payload(self, data) -> list[Event]:
        events: list[Event] = []
        for row in (data or {}).get("events") or []:
            event_id = row.get("id")
            geometry = row.get("geometry") or []
            if not event_id or not geometry:
                continue

            # `geometry` is a TRAJECTORY: the last entry is the current
            # position. Taking the first would display a cyclone where it was
            # three days ago.
            last = geometry[-1]
            time = to_utc(last.get("date"))
            if time is None:
                continue

            coords = last.get("coordinates") or []
            lat = lon = None
            if last.get("type") == "Point" and len(coords) >= 2:
                lon, lat = coords[0], coords[1]
            elif coords:
                # extent (Polygon): we place the point at its center
                lat, lon = centroid(coords)

            categories = row.get("categories") or []
            category = (categories[0] or {}).get("id") if categories else None
            kind = EONET_KIND.get(category or "", Kind.OTHER)

            magnitude = last.get("magnitudeValue")
            unit = last.get("magnitudeUnit")
            severity = Severity.MODERATE
            if kind is Kind.CYCLONE and isinstance(magnitude, (int, float)):
                severity = cyclone_severity(float(magnitude), "HU" if magnitude >= 64 else "TS")

            title = row.get("title") or "EONET event"
            events.append(
                Event(
                    id=f"eonet:{event_id}",
                    source="eonet",
                    source_id=str(event_id),
                    kind=kind,
                    time=time,
                    lat=lat,
                    lon=lon,
                    magnitude=float(magnitude) if isinstance(magnitude, (int, float)) else None,
                    mag_type=unit,
                    place=title,
                    severity=severity,
                    # we query EONET with status=open: ongoing by definition
                    ongoing=True,
                    title=title,
                    url=row.get("link"),
                    raw={
                        "category": category,
                        "sources": [s.get("id") for s in row.get("sources") or []],
                        "track_points": len(geometry),
                    },
                )
            )
        return events
