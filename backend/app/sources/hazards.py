"""High-value non-seismic hazards: tropical cyclones and volcanic ash.

GDACS sees cyclones, but coarsely and with delay. The NHC publishes the
position, winds and category of every active storm at each advisory. And for
volcanic ash, the VAACs only publish heterogeneous text: aviation SIGMETs are
its structured operational translation, and the only machine-readable
worldwide feed that exists for this hazard.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, to_utc, utcnow
from app.sources.base import Emit
from app.sources.regional import USER_AGENT, JsonPollSource

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


# --------------------------------------------------------------- forecast track
#
# CurrentStorms.json says WHERE a storm IS. The forecast track -- where the
# NHC expects it to GO, up to 5 days out -- lives in a separate ArcGIS
# service, one feature layer per storm bin (binNumber, e.g. "CP2"). Layer ids
# are not stable across the season (they shift as storms form and dissipate),
# so they must be resolved by NAME against the layer directory, not hardcoded.
NHC_ARCGIS_ROOT = (
    "https://mapservices.weather.noaa.gov/tropical/rest/services/"
    "tropical/NHC_tropical_weather/MapServer"
)

# Storm bins are reused within a season but not created/destroyed every poll:
# refreshing the layer directory this often is already generous, and it
# still gets a forced refresh whenever a bin we need is missing from it.
LAYER_CACHE_TTL = timedelta(hours=6)


def _epoch_ms_to_utc(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _resolve_valid_time(validtime: str | None, anchor: datetime | None) -> datetime | None:
    """`validtime` is "DD/HHMM" in UTC with no month or year of its own.

    `anchor` (the forecast package's own `idp_filedate`) supplies them. A
    forecast point never trails its package by more than a few hours, so a
    candidate that lands far in the past means the day number wrapped past
    the end of the month (a day-31 point anchored in an August package
    belongs to September) -- roll forward one month and retry.
    """
    if not validtime or anchor is None or "/" not in validtime:
        return None
    day_str, _, hm_str = validtime.partition("/")
    try:
        day = int(day_str)
        hour = int(hm_str[:2])
        minute = int(hm_str[2:4])
    except (ValueError, IndexError):
        return None

    def build(year: int, month: int) -> datetime | None:
        try:
            return datetime(year, month, day, hour, minute, tzinfo=UTC)
        except ValueError:
            return None

    candidate = build(anchor.year, anchor.month)
    if candidate is not None and candidate < anchor - timedelta(days=5):
        month = anchor.month + 1 if anchor.month < 12 else 1
        year = anchor.year if anchor.month < 12 else anchor.year + 1
        candidate = build(year, month) or candidate
    return candidate


def parse_forecast_track(data: Any) -> list[dict]:
    """Forecast points GeoJSON -> compact track, sorted by lead time.

    TRAP verified on the live feed: `properties.lat`/`lon` are truncated to
    whole degrees (rounding a 20.4N fix down to 20) -- the real precision is
    only in `geometry.coordinates`. Never read position off the properties.
    """
    points: list[dict] = []
    for feature in (data or {}).get("features") or []:
        props = feature.get("properties") or {}
        tau = props.get("tau")
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        if tau is None or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]

        try:
            wind_kt = float(props["maxwind"]) if props.get("maxwind") is not None else None
        except (TypeError, ValueError):
            wind_kt = None

        anchor = _epoch_ms_to_utc(props.get("idp_filedate"))
        valid = _resolve_valid_time(props.get("validtime"), anchor)

        points.append(
            {
                "tau": tau,
                # falls back to the raw "DD/HHMM" string if the anchor is
                # unusable -- still informative, never a hard failure
                "valid": valid.isoformat() if valid else props.get("validtime"),
                "lat": lat,
                "lon": lon,
                "wind_kt": wind_kt,
                # Saffir-Simpson category; 0 == tropical storm, per NHC
                "category": props.get("ssnum"),
            }
        )
    points.sort(key=lambda p: p["tau"])
    return points


class NhcSource(JsonPollSource):
    """National Hurricane Center: Atlantic, East and Central Pacific basins."""

    name = "nhc"
    kind = "poll"
    url = "https://www.nhc.noaa.gov/CurrentStorms.json"

    def __init__(self, poll_seconds: float = 300.0, url: str | None = None) -> None:
        super().__init__(poll_seconds=poll_seconds, url=url)
        # layer name ("CP2 Forecast Points") -> layer id, resolved once and
        # reused across polls; see LAYER_CACHE_TTL
        self._layer_ids: dict[str, int] | None = None
        self._layer_cache_time: datetime | None = None

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.build_url())
                    resp.raise_for_status()
                    data = resp.json()
                    events = self.parse_payload(data)
                    # forecast tracks are a supplementary detail on top of
                    # each storm's current position: a fetch failure here
                    # must never cost us the position itself, so it is
                    # isolated in its own try -- the storm event above is
                    # already built and will be emitted regardless.
                    try:
                        await self._attach_forecast_tracks(client, events, data)
                    except Exception as exc:
                        log.warning("%s: forecast tracks failed: %s", self.name, exc)
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)

    async def _attach_forecast_tracks(
        self, client: httpx.AsyncClient, events: list[Event], data: Any
    ) -> None:
        storms = (data or {}).get("activeStorms") or []
        events_by_id = {e.source_id: e for e in events}
        bins_needed = {s.get("binNumber") for s in storms if s.get("binNumber")}
        if not bins_needed:
            return

        try:
            await self._ensure_layer_cache(client, bins_needed)
        except Exception as exc:
            # this whole method is a supplementary detail: a failure to even
            # resolve the layer directory must not propagate any further
            log.warning("%s: layer directory fetch failed: %s", self.name, exc)
            return
        if not self._layer_ids:
            return

        for storm in storms:
            storm_id = storm.get("id")
            bin_number = storm.get("binNumber")
            event = events_by_id.get(str(storm_id)) if storm_id else None
            if event is None or not bin_number:
                continue

            layer_id = self._layer_ids.get(f"{bin_number} Forecast Points")
            if layer_id is None:
                # the bin has no forecast layer right now (storm just
                # formed, or it is a remnant with no active advisory)
                continue

            try:
                resp = await client.get(
                    f"{NHC_ARCGIS_ROOT}/{layer_id}/query",
                    params={"where": "1=1", "outFields": "*", "f": "geojson"},
                )
                resp.raise_for_status()
                track = parse_forecast_track(resp.json())
            except Exception as exc:
                log.warning("%s: forecast points for %s failed: %s", self.name, bin_number, exc)
                continue

            if track:
                event.raw["forecast_track"] = track
                # also as a first-class field: `public()` strips `raw`, so this
                # is the only copy the browser will ever see
                event.forecast_track = track
            # also as a first-class field: `public()` strips `raw`, so this is
            # the only copy the browser will ever see
            event.forecast_track = track

    def _layer_cache_stale(self, bins_needed: set[str]) -> bool:
        if self._layer_ids is None or self._layer_cache_time is None:
            return True
        if utcnow() - self._layer_cache_time > LAYER_CACHE_TTL:
            return True
        # a bin that just started publishing forecasts won't be in a cache
        # built before it existed -- force one refresh rather than wait
        # up to LAYER_CACHE_TTL to notice it
        return any(f"{b} Forecast Points" not in self._layer_ids for b in bins_needed)

    async def _ensure_layer_cache(self, client: httpx.AsyncClient, bins_needed: set[str]) -> None:
        if not self._layer_cache_stale(bins_needed):
            return
        resp = await client.get(f"{NHC_ARCGIS_ROOT}/layers", params={"f": "json"})
        resp.raise_for_status()
        data = resp.json()
        self._layer_ids = {
            layer["name"]: layer["id"]
            for layer in data.get("layers") or []
            if layer.get("name") and layer.get("id") is not None
        }
        self._layer_cache_time = utcnow()

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
