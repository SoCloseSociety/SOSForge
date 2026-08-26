"""NOAA / NWS tsunami.gov -- bulletins from the two US warning centers.

PAAQ = National Tsunami Warning Center (Palmer, Alaska) -- covers US/Canada.
PHEB = Pacific Tsunami Warning Center (Honolulu) -- covers the Pacific and the
Caribbean on behalf of UNESCO/IOC.

The Atom feed only contains the latest bulletin issued, and the data that
matters (the category: Information / Watch / Advisory / Warning) is buried in
the `summary` HTML. We extract it by regex: the format of these bulletins has
been frozen for years, and depending on a full HTML parser for three fields
would be worse.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html import unescape

import httpx

from app.models.event import Event, Kind, Severity
from app.sources.base import Emit, Source
from app.sources.tsunami_bulletin import Bulletin, BulletinCache

log = logging.getLogger(__name__)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
}

FEEDS = {
    "PAAQ": "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
    "PHEB": "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
}

CENTER_NAMES = {
    "PAAQ": "National Tsunami Warning Center",
    "PHEB": "Pacific Tsunami Warning Center",
}

# Warning > Advisory > Watch > Information (decreasing order of severity)
CATEGORY_SEVERITY = {
    "warning": Severity.EXTREME,
    "advisory": Severity.SEVERE,
    "watch": Severity.SEVERE,
    "information": Severity.INFO,
    "statement": Severity.INFO,
    "cancellation": Severity.INFO,
}

# We regex on the STRIPPED text, never on the markup: depending on the
# bulletin, tsunami.gov serves the summary sometimes as escaped xhtml
# (&lt;strong&gt;), sometimes as real elements -- which then come out prefixed
# with the Atom namespace. The bare text is identical in both cases.
RE_TAGS = re.compile(r"<[^>]+>")
RE_CATEGORY = re.compile(r"Category:?\s*([A-Za-z]+)", re.I)
RE_MAGNITUDE = re.compile(r"Preliminary Magnitude:?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
RE_MAG_FALLBACK = re.compile(r"Magnitude[^0-9]{0,40}([0-9]+\.[0-9])", re.I)


def strip_markup(value: str) -> str:
    """Tags removed, entities resolved, whitespace normalized."""
    text = unescape(value)
    text = RE_TAGS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _inner_xml(node: ET.Element | None) -> str:
    """Re-serializes a node (the Atom summary is inline xhtml)."""
    if node is None:
        return ""
    chunks = [node.text or ""]
    for child in node:
        chunks.append(ET.tostring(child, encoding="unicode"))
    return "".join(chunks)


def _text(node: ET.Element, path: str) -> str | None:
    found = node.find(path, NS)
    return found.text.strip() if found is not None and found.text else None


def _parse_time(value: str | None) -> datetime:
    if value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def text_bulletin_link(entry: ET.Element) -> str | None:
    """The plain-text bulletin behind an Atom entry.

    The entry offers two documents: a CAP XML (`rel="related"`, type
    `application/cap+xml`) and the bulletin itself (`rel="alternate"`,
    `.../WEAK53.txt`). Only the second one carries the arrival table and the
    gauge readings -- both were checked against the real files for the same
    event. So this deliberately picks the `.txt`, not the first link, and
    returns nothing rather than falling back to a document that cannot
    contain the answer.
    """
    for node in entry.findall("atom:link", NS):
        href = node.get("href") or ""
        if href.lower().endswith(".txt"):
            return href.strip()
    return None


def apply_bulletin(event: Event, bulletin: Bulletin) -> Event:
    """Attach what the bulletin text added, if it added anything.

    Nothing is invented and nothing is cleared: an empty bulletin leaves the
    event exactly as the Atom feed described it. A later message that has
    dropped its arrival table (they do, once the wave has passed) must not
    erase the arrival time an earlier one gave, but each bulletin URL is a
    separate document and the event is rebuilt from the latest one -- so the
    honest statement is that the event says what its CURRENT bulletin says.
    """
    arrival = bulletin.first_arrival
    if arrival:
        event.wave_eta = arrival.at
        event.wave_eta_site = arrival.site
    largest = bulletin.largest
    if largest:
        event.wave_max_m = largest.metres
        event.wave_max_site = largest.site
    return event


def parse_entry(entry: ET.Element, center: str) -> Event | None:
    entry_id = _text(entry, "atom:id") or _text(entry, "atom:updated")
    if not entry_id:
        return None

    summary = strip_markup(_inner_xml(entry.find("atom:summary", NS)))
    match = RE_CATEGORY.search(summary)
    category = (match.group(1) if match else "information").lower()

    mag_match = RE_MAGNITUDE.search(summary) or RE_MAG_FALLBACK.search(summary)
    magnitude = float(mag_match.group(1)) if mag_match else None

    lat = lon = None
    try:
        lat = float(_text(entry, "geo:lat") or "")
        lon = float(_text(entry, "geo:long") or "")
    except (TypeError, ValueError):
        lat = lon = None

    place = _text(entry, "atom:title") or "unknown region"
    severity = CATEGORY_SEVERITY.get(category, Severity.INFO)
    # An "Information" bulletin generally says "NO tsunami danger": it is not
    # a tsunami alert, and the UI must not scream over it.
    is_alert = category in ("warning", "advisory", "watch")

    link = None
    for node in entry.findall("atom:link", NS):
        href = node.get("href") or ""
        if node.get("type") == "application/cap+xml" or href.endswith(".txt"):
            link = href
            break

    return Event(
        id=f"tsunami:{center}:{entry_id.rsplit(':', 1)[-1]}",
        source="tsunami",
        source_id=entry_id,
        kind=Kind.TSUNAMI,
        time=_parse_time(_text(entry, "atom:updated")),
        lat=lat,
        lon=lon,
        magnitude=magnitude,
        place=place,
        severity=severity,
        # A warning/advisory/watch runs until a centre supersedes or lifts it:
        # it is an ongoing alert, and the sweep must be able to reach it. An
        # "Information" bulletin states a fact ("no tsunami danger") -- it is
        # not in force, it has nothing to expire.
        ongoing=is_alert,
        tsunami=is_alert,
        alert=category,
        title=f"Tsunami {category.upper()} -- {place}",
        url=link,
        raw={
            "center": CENTER_NAMES.get(center, center),
            "category": category,
            "summary": summary[:1200],
        },
    )


class TsunamiSource(Source):
    name = "tsunami"
    kind = "poll"

    def __init__(self, poll_seconds: float = 30.0, feeds: dict[str, str] | None = None):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.feeds = feeds or FEEDS
        # The Atom summary answers "how big was the earthquake". The two
        # questions a person on a coast actually has -- when does it reach me,
        # and has a gauge seen anything -- are only in the text bulletin.
        self.bulletins = BulletinCache()

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": "SOSForge/1.0 (+https://soclose.co)"}
        async with httpx.AsyncClient(
            timeout=20.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                seen = 0
                alive = 0
                for center, url in self.feeds.items():
                    try:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        root = ET.fromstring(resp.content)
                        alive += 1
                        for entry in root.findall(".//atom:entry", NS):
                            event = parse_entry(entry, center)
                            if not event:
                                continue
                            # One extra GET the first time a bulletin appears,
                            # and none ever again: the document is immutable
                            # and the cache keeps the parsed result, empty
                            # ones included. A failure here costs the arrival
                            # time, never the alert.
                            link = text_bulletin_link(entry)
                            if link:
                                apply_bulletin(
                                    event,
                                    await self.bulletins.get(client, link, event.time),
                                )
                            seen += 1
                            await emit(event)
                    except Exception as exc:
                        self.health.fail(exc)
                        log.warning("tsunami %s: %s", center, exc)
                # NEVER declare good health if no feed responded: a tsunami
                # alert source shown green while it is dead is exactly the lie
                # this product forbids itself.
                if alive:
                    self.health.ok(seen)
                await asyncio.sleep(self.poll_seconds)
