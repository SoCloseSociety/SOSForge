"""GDACS -- flux multi-alea (seismes majeurs, cyclones, inondations, volcans, feux).

C'est ce qui fait de SOSForge autre chose qu'un compteur de seismes: GDACS agrege
les alertes mondiales avec un niveau (Green/Orange/Red) deja calcule par la
Commission europeenne + l'ONU.
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
    # pubDate = derniere publication GDACS. C'est ca, la fraicheur: une secheresse
    # "en cours" depuis juillet 2025 a un fromdate d'il y a un an mais n'est pas
    # une info du moment.
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

    title = _text(item, "title") or "alerte GDACS"
    country = _text(item, "gdacs:country")
    kind = EVENT_TYPE_TO_KIND.get(event_type, Kind.OTHER)

    # gdacs:severity porte la valeur numerique dans l'ATTRIBUT `value`; le texte
    # ("Magnitude 5.8M, Depth:54.7km") est destine a un humain. L'unite change
    # selon le type: M pour un seisme, km/h pour un cyclone, ha pour un feu.
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
        # un evenement GDACS a plusieurs episodes: on garde l'eventid comme cle
        # pour que les episodes successifs mettent a jour la meme entree.
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
    """Le flux GDACS complet, c'est ~400 entrees dont ~344 feux verts et des
    secheresses ouvertes depuis un an. Non filtre, il noie les seismes et les
    tsunamis dans du bruit. Regle: on garde tout ce qui est orange/rouge (c'est
    l'interet de GDACS), et le vert seulement s'il vient d'etre publie.
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
        # gdacs.org sert 1.2 Mo de RSS et met regulierement 60s a repondre:
        # un timeout court ferait echouer la source a tous les cycles.
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
                    log.debug("gdacs: %d items, %d retenus", len(items), kept)
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("gdacs poll: %s", exc)
                await asyncio.sleep(self.poll_seconds)
