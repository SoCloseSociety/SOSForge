"""NOAA / NWS tsunami.gov -- bulletins des deux centres d'alerte americains.

PAAQ = National Tsunami Warning Center (Palmer, Alaska) -- couvre US/Canada.
PHEB = Pacific Tsunami Warning Center (Honolulu) -- couvre le Pacifique et les
Caraibes pour le compte de l'UNESCO/IOC.

Le flux Atom ne contient que le dernier bulletin emis, et la donnee qui compte
(la categorie: Information / Watch / Advisory / Warning) est enfouie dans le HTML
du `summary`. On l'extrait par regex: le format de ces bulletins est fige depuis
des annees, et dependre d'un parseur HTML complet pour trois champs serait pire.
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

# Warning > Advisory > Watch > Information (ordre decroissant de gravite)
CATEGORY_SEVERITY = {
    "warning": Severity.EXTREME,
    "advisory": Severity.SEVERE,
    "watch": Severity.SEVERE,
    "information": Severity.INFO,
    "statement": Severity.INFO,
    "cancellation": Severity.INFO,
}

# On regexe sur le texte DEBALISE, jamais sur le balisage: selon les bulletins,
# tsunami.gov sert le summary tantot en xhtml echappe (&lt;strong&gt;), tantot en
# vrais elements -- qui ressortent alors prefixes par le namespace Atom. Le texte
# nu, lui, est identique dans les deux cas.
RE_TAGS = re.compile(r"<[^>]+>")
RE_CATEGORY = re.compile(r"Category:?\s*([A-Za-z]+)", re.I)
RE_MAGNITUDE = re.compile(r"Preliminary Magnitude:?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
RE_MAG_FALLBACK = re.compile(r"Magnitude[^0-9]{0,40}([0-9]+\.[0-9])", re.I)


def strip_markup(value: str) -> str:
    """Balises supprimees, entites resolues, espaces normalises."""
    text = unescape(value)
    text = RE_TAGS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _inner_xml(node: ET.Element | None) -> str:
    """Reserialise un noeud (le summary Atom est du xhtml inline)."""
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

    place = _text(entry, "atom:title") or "region inconnue"
    severity = CATEGORY_SEVERITY.get(category, Severity.INFO)
    # Un bulletin "Information" dit en general "NO tsunami danger": ce n'est pas
    # une alerte tsunami, on ne doit pas faire hurler l'interface pour ca.
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
                            if event:
                                seen += 1
                                await emit(event)
                    except Exception as exc:
                        self.health.fail(exc)
                        log.warning("tsunami %s: %s", center, exc)
                # Ne JAMAIS se declarer en bonne sante si aucun feed n'a repondu:
                # une source d'alerte tsunami affichee verte alors qu'elle est
                # morte est exactement le mensonge que ce produit s'interdit.
                if alive:
                    self.health.ok(seen)
                await asyncio.sleep(self.poll_seconds)
