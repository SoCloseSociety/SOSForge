"""GDACS -- multi-hazard feed (major quakes, cyclones, floods, volcanoes, fires).

This is what makes SOSForge more than an earthquake counter: GDACS aggregates
worldwide alerts with a level (Green/Orange/Red) already computed by the
European Commission + the UN.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.models.event import Event, Kind, Severity
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

NS = {
    "gdacs": "http://www.gdacs.org",
    "georss": "http://www.georss.org/georss",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
}

EVENT_TYPE_TO_KIND = {
    "EQ": Kind.EARTHQUAKE,
    "TS": Kind.TSUNAMI,
    "TC": Kind.CYCLONE,
    "FL": Kind.FLOOD,
    "VO": Kind.VOLCANO,
    "DR": Kind.DROUGHT,
    "WF": Kind.WILDFIRE,
}

ALERT_TO_SEVERITY = {
    "green": Severity.MINOR,
    "orange": Severity.SEVERE,
    "red": Severity.EXTREME,
}


def _text(node: ET.Element, path: str) -> str | None:
    found = node.find(path, NS)
    return found.text.strip() if found is not None and found.text else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_item(item: ET.Element) -> Event | None:
    event_id = _text(item, "gdacs:eventid") or _text(item, "guid")
    if not event_id:
        return None
    event_type = (_text(item, "gdacs:eventtype") or "").upper()
    alert = (_text(item, "gdacs:alertlevel") or "").lower()

    time = (
        _parse_date(_text(item, "gdacs:fromdate"))
        or _parse_date(_text(item, "pubDate"))
        or datetime.now(UTC)
    )
    # pubDate = latest GDACS publication. That is what freshness means: a
    # drought "ongoing" since July 2025 has a fromdate a year old but is not
    # current news.
    published = _parse_date(_text(item, "pubDate")) or time

    lat = lon = None
    point = _text(item, "georss:point")
    if point:
        try:
            lat_s, lon_s = point.split()
            lat, lon = float(lat_s), float(lon_s)
        except ValueError:
            lat = lon = None
    if lat is None:
        try:
            lat = float(_text(item, "geo:lat") or "")
            lon = float(_text(item, "geo:long") or "")
        except (TypeError, ValueError):
            lat = lon = None

    title = _text(item, "title") or "GDACS alert"
    country = _text(item, "gdacs:country")
    kind = EVENT_TYPE_TO_KIND.get(event_type, Kind.OTHER)

    # gdacs:severity carries the numeric value in the `value` ATTRIBUTE; the
    # text ("Magnitude 5.8M, Depth:54.7km") is meant for a human. The unit
    # changes with the type: M for a quake, km/h for a cyclone, ha for a fire.
    severity_node = item.find("gdacs:severity", NS)
    severity_value: float | None = None
    severity_unit = None
    severity_text = None
    if severity_node is not None:
        severity_unit = severity_node.get("unit")
        severity_text = (severity_node.text or "").strip() or None
        try:
            severity_value = float(severity_node.get("value") or "")
        except ValueError:
            severity_value = None

    return Event(
        # a GDACS event has several episodes: keep the eventid as the key so
        # successive episodes update the same entry.
        id=f"gdacs:{event_type}{event_id}",
        source="gdacs",
        source_id=f"{event_type}{event_id}",
        kind=kind,
        time=time,
        updated_at=published,
        lat=lat,
        lon=lon,
        magnitude=severity_value if kind is Kind.EARTHQUAKE else None,
        mag_type=severity_unit if kind is Kind.EARTHQUAKE else None,
        place=country or title,
        country=country,
        severity=ALERT_TO_SEVERITY.get(alert, Severity.INFO),
        # GDACS itself says whether the event is still current
        ongoing=(_text(item, "gdacs:iscurrent") or "").lower() == "true",
        alert=alert or None,
        tsunami=kind is Kind.TSUNAMI,
        title=title,
        url=_text(item, "link"),
        raw={
            "alertlevel": alert,
            "eventtype": event_type,
            "episode": _text(item, "gdacs:episodeid"),
            "iscurrent": _text(item, "gdacs:iscurrent"),
            "severity": severity_text,
            "severity_unit": severity_unit,
            "population": _text(item, "gdacs:population"),
            "published": published.isoformat(),
        },
    )


def is_relevant(event: Event, max_age_days: float) -> bool:
    """The full GDACS feed is ~400 entries, ~344 of which are green fires plus
    droughts open for a year. Unfiltered, it drowns quakes and tsunamis in
    noise. Rule: keep everything orange/red (that is GDACS's whole point), and
    green only if it was just published.
    """
    if event.severity in (Severity.SEVERE, Severity.EXTREME):
        return True
    reference = event.updated_at or event.time
    age_days = (datetime.now(UTC) - reference).total_seconds() / 86400.0
    return age_days <= max_age_days


class GdacsSource(Source):
    name = "gdacs"
    kind = "poll"

    def __init__(
        self,
        url: str = "https://www.gdacs.org/xml/rss.xml",
        poll_seconds: float = 120.0,
        max_age_days: float = 3.0,
    ):
        super().__init__()
        self.url = url
        self.poll_seconds = poll_seconds
        self.max_age_days = max_age_days

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": "SOSForge/1.0 (+https://soclose.co)"}
        # gdacs.org serves 1.2 MB of RSS and regularly takes 60s to respond:
        # a short timeout would fail the source on every cycle.
        async with httpx.AsyncClient(
            timeout=120.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    root = ET.fromstring(resp.text)
                    items = root.findall(".//item")
                    kept = 0
                    for item in items:
                        event = parse_item(item)
                        if event and is_relevant(event, self.max_age_days):
                            kept += 1
                            await emit(event)
                    self.health.ok(kept)
                    log.debug("gdacs: %d items, %d kept", len(items), kept)
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("gdacs poll: %s", exc)
                await asyncio.sleep(self.poll_seconds)
