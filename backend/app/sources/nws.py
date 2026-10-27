"""NWS api.weather.gov -- official US alerts, all hazards.

This is the most reactive alert source of the lot: it publishes within about a
second (5s cache on the NWS side), and covers what the seismic feeds ignore --
tornadoes, flash floods, violent winds, extreme heat, and the US-side Tsunami
Warnings.

The NWS requires an identifying User-Agent: without it, we get blocked.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.models.event import Event, Kind, severity_for_cap, to_utc
from app.sources import geometry
from app.sources.base import Emit, Source
from app.sources.nws_zones import ZoneResolver

log = logging.getLogger(__name__)

URL = (
    "https://api.weather.gov/alerts/active?status=actual&message_type=alert&severity=Extreme,Severe"
)

# Order matters: the first pattern found wins ("Tsunami" before "Flood").

# Matching: substring ABOVE a length floor, whole word below it.
#
# The three possible rules were measured on the real feeds (2190 WMO alerts,
# 335 NWS alerts), and the result settled it:
#
# - substring everywhere: "Flash Flood" becomes a VOLCANIC alert, because
#   "Flash" contains "ash";
# - whole words everywhere: the false positive disappears, but **621 real
#   alerts are lost** -- "Forestfire", "Thunderstorms", "Rainstorm" are
#   compound or inflected forms that no whole word finds. Fixing a
#   classification problem by shrinking detection is a bad trade;
# - length floor: **zero loss**, and the 22 "Flash Flood" reclassified
#   correctly. This is the rule.
#
# A short pattern ("ash", "ice", "hot") is the kind that hides inside other
# words: it therefore requires a whole word. A pattern of four letters or more
# is specific enough to be searched as a substring.
MIN_SUBSTRING = 4


def _matches(text: str, words: set[str], patterns: tuple[str, ...]) -> bool:
    return any((p in text) if len(p) >= MIN_SUBSTRING else (p in words) for p in patterns)


KIND_PATTERNS: list[tuple[tuple[str, ...], Kind]] = [
    (("tsunami",), Kind.TSUNAMI),
    (("volcano", "volcanic", "ash", "ashfall"), Kind.VOLCANO),
    # BEFORE the cyclone rule. "Hurricane Force Wind Warning" is the NWS
    # marine product for a NON-tropical low with hurricane-force winds (the
    # tropical one is "Hurricane Warning"). Read on the word "hurricane", two
    # Alaskan gale centres were published as EXTREME tropical cyclones on
    # 2026-10-26 -- a cyclone marker in the Bering Sea in October.
    (("hurricane force",), Kind.STORM),
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
            # The marine "Storm Warning" (48-63 kt) and "Storm Watch", and the
            # "Dust Storm Warning": none of them matched, and the 21 live
            # Storm Warnings of 2026-10-26 sat in OTHER at SEVERE. Tropical
            # Storm and Storm Surge are caught by the cyclone rule above first.
            "storm",
        ),
        Kind.STORM,
    ),
    # A Kind of its own since Meteoalarm brought avalanches in; NWS issues
    # Avalanche Warning / Watch / Advisory and they were filed under OTHER.
    (("avalanche",), Kind.AVALANCHE),
    (("drought",), Kind.DROUGHT),
]


def classify(event_name: str) -> Kind:
    text = (event_name or "").lower()
    words = set(re.findall(r"[a-z]+", text))
    for patterns, kind in KIND_PATTERNS:
        if _matches(text, words, patterns):
            return kind
    return Kind.OTHER


def _points(feature_geometry: dict | None) -> list[tuple[float, float]]:
    """(lat, lon) of every vertex of a GeoJSON geometry, any nesting depth."""
    points: list[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) == 2
            and all(isinstance(v, (int, float)) for v in node)
        ):
            points.append((float(node[1]), float(node[0])))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk((feature_geometry or {}).get("coordinates"))
    return points


def _parse_time(value: str | None) -> datetime:
    return to_utc(value) or datetime.now(UTC)


def parse_feature(feature: dict, zones: ZoneResolver | None = None) -> Event | None:
    props = feature.get("properties") or {}
    alert_id = props.get("id") or feature.get("id")
    if not alert_id:
        return None

    event_name = props.get("event") or "alert"
    kind = classify(event_name)
    vertices = _points(feature.get("geometry"))
    middle = geometry.center(vertices)
    lat, lon = middle if middle else (None, None)
    extent = geometry.extent(vertices)
    affected = props.get("affectedZones") or []
    if (lat is None or lon is None) and zones is not None:
        # The alert did not draw itself, but it said which zones it covers.
        for zone in affected:
            known = zones.known(zone)
            if known:
                lat, lon = known
                break
    if extent is None and zones is not None and affected:
        # Its extent is the union of those zones -- and only once EVERY one is
        # known: a box over half the zones is not a smaller truth, it is a
        # wrong one, and the reader falls back to the centre meanwhile.
        boxes = [zones.extent(zone) for zone in affected]
        if all(box is not None for box in boxes):
            extent = geometry.union(boxes)
    # The NWS publishes all three CAP axes on every alert and only `severity`
    # was read. Measured on the live feed of 2026-08-26: a "Fire Weather Watch"
    # (Severe, but urgency Future and certainty Possible -- the forecaster is
    # saying conditions MAY become favourable the day after tomorrow) carried
    # the same rank as a M6.5 earthquake. Three of the three alerts the feed
    # served at Severe that day were fire-weather or heat products.
    #
    # Tsunami is the one exception, and it is about consistency rather than
    # calibration: `tsunami.py` already ranks a Warning EXTREME and an Advisory
    # or Watch SEVERE, and the NWS relays THE SAME bulletins as CAP alerts.
    # Reading one copy as a forecast and the other as a fact would publish one
    # wave at two ranks depending on which feed reached us first.
    severity = severity_for_cap(
        props.get("severity"),
        props.get("urgency"),
        props.get("certainty"),
        observed=kind is Kind.TSUNAMI,
    )

    # `/alerts/active` returns, by construction, only alerts in force: every
    # feature it serves is ongoing. Saying so is what lets the horizon keep it
    # and, above all, what puts it in reach of the sweep -- which only ever
    # looks at ongoing events. Left at its default False, a tornado warning was
    # treated exactly like a three-day-old quake.
    # `ends` is when the ALERT stops; `expires` is when the MESSAGE announcing
    # it stops being valid. They are not the same thing and the difference is
    # not academic: measured on the live feed of 2026-08-18, 82 of 295 active
    # alerts had `expires` already in the past while `ends` was still hours
    # ahead. Purging on `expires` would have erased 28% of the running US
    # warnings. `ends` first, `expires` only for the 23 that publish no `ends`.
    end_of_alert = props.get("ends") or props.get("expires")
    expires = _parse_time(end_of_alert) if end_of_alert else None

    return Event(
        id=f"nws:{str(alert_id).rsplit(':', 1)[-1]}",
        source="nws",
        source_id=str(alert_id),
        kind=kind,
        time=_parse_time(props.get("sent") or props.get("effective")),
        lat=lat,
        lon=lon,
        bbox=extent,
        place=props.get("areaDesc") or "unspecified area",
        country="United States",
        severity=severity,
        ongoing=True,
        expires=expires,
        tsunami=kind is Kind.TSUNAMI,
        alert=(props.get("urgency") or "").lower() or None,
        title=props.get("headline") or event_name,
        url=props.get("@id") or f"https://api.weather.gov/alerts/{alert_id}",
        # The three fields this parser used to download and drop. Measured on
        # the 1631 alerts NWS published at Severe or Extreme between
        # 2026-08-21 and 2026-08-26: `description` 100%, `response` 99.9%,
        # `instruction` 85.2% (it is absent from Watches by design -- a watch
        # says "conditions are favourable", it has nothing to instruct yet).
        instruction=props.get("instruction"),
        description=props.get("description"),
        # CAP `responseType`, called `response` in the GeoJSON rendering. On
        # that same sample: Shelter 1006, Avoid 421, Monitor 93, Prepare 60,
        # Execute 46, Evacuate 4.
        response_type=props.get("response"),
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

    def __init__(self, poll_seconds: float = 20.0, url: str = URL, zone_cache: Path | None = None):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url
        # 89% of live alerts arrive with no geometry, and every one of them
        # names zones that have one. Without this they cannot be mapped, cannot
        # be ranked by distance, and cannot answer "is that near me".
        self.zones = ZoneResolver(zone_cache)

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

                    # Learn a few unknown zones per cycle, starting with the
                    # alerts that need them. NWS republishes its active list
                    # every cycle, so anything not placed this time is placed
                    # on the next -- and the fingerprint now includes the
                    # position, so the fix reaches open tabs as a revision.
                    wanted = [
                        zone
                        for feature in features
                        if not feature.get("geometry")
                        for zone in ((feature.get("properties") or {}).get("affectedZones") or [])
                    ]
                    if wanted:
                        await self.zones.resolve(client, wanted)

                    for feature in features:
                        event = parse_feature(feature, self.zones)
                        if event:
                            await emit(event)
                    self.health.ok(len(features))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("nws: %s", exc)
                await asyncio.sleep(self.poll_seconds)
