"""Early warning and Asian coverage, via the Wolfx relay.

**What these sources change in kind.** The sixteen other sources publish
AFTER the fact: a quake happened, an agency locates it, we display it. The
Japanese early warning (EEW) is issued **while** the waves are propagating,
a few seconds after detection by the nearest stations, before the destructive
waves reach the cities. It is the only category of information in this product
that can still be used to take cover.

**Acknowledged caveat, read before trusting it.** Wolfx is an **unofficial
third-party** relay. JMA and CENC publish no open API; this service rebroadcasts
their feeds. So we treat it as a "best effort" source: it enriches, it is
authoritative on nothing, and its failure must break nothing. Their websockets
are behind Cloudflare and refuse any non-browser client (403), hence the
polling.

Two timezone traps, the main reason this module is careful: JMA timestamps in
Japan time and CENC in Beijing time, **both without indicating the offset**. A
naive `fromisoformat` would date them in server time, seven to nine hours off
on a product where the second matters.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.models.event import Event, Kind, Severity, severity_from_magnitude
from app.sources.regional import SHINDO_SEVERITY, JsonPollSource

log = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
CST = ZoneInfo("Asia/Shanghai")

# A cancelled EEW (the detection was a false positive, common early in an
# alert) must absolutely not stay displayed as an ongoing alert.
JMA_CANCELLED = "キャンセル"
# 警報 = warning (strong shaking expected), 予報 = forecast (information)
JMA_WARNING_MARK = "警報"


def _parse_local(value: str | None, zone: ZoneInfo) -> datetime | None:
    """Local timestamp WITHOUT an offset: we attach the timezone ourselves."""
    if not value:
        return None
    text = value.strip().replace("/", "-")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=zone).astimezone(UTC)


class JmaEewSource(JsonPollSource):
    """Japanese early warning (緊急地震速報).

    The endpoint returns only ONE alert: the latest issued, whether it is ten
    seconds or six hours old. Freshness is therefore judged by the pipeline on
    the real timestamp, not on the fact that we just read it.

    The same alert is re-issued several times with an increasing `Serial`,
    the magnitude and intensity getting more precise with each send. The key
    is the `EventID`, so these revisions update the same entry instead of
    piling up.
    """

    name = "jma_eew"
    kind = "poll"
    url = "https://api.wolfx.jp/jma_eew.json"

    def parse_payload(self, data: Any) -> list[Event]:
        if not isinstance(data, dict):
            return []
        event_id = data.get("EventID")
        if not event_id:
            return []

        status = ((data.get("Issue") or {}).get("Status")) or ""
        if JMA_CANCELLED in status:
            log.info("JMA EEW %s cancelled by the source", event_id)
            return []

        time = _parse_local(data.get("OriginTime"), JST) or _parse_local(
            data.get("AnnouncedTime"), JST
        )
        if time is None:
            return []

        # yes, the key really is spelled "Magunitude" in their API
        magnitude = data.get("Magunitude")
        shindo = str(data.get("MaxIntensity") or "").strip()
        place = data.get("Hypocenter") or "Japan"
        title = data.get("Title") or ""

        severity = severity_from_magnitude(
            float(magnitude) if isinstance(magnitude, (int, float)) else None
        )
        if shindo in SHINDO_SEVERITY:
            # the expected ground intensity wins: it is what says whether to
            # take cover
            severity = max(
                severity,
                SHINDO_SEVERITY[shindo],
                key=lambda s: list(Severity).index(s),
            )
        # a warning (警報) is always worth at least "severe", even if the first
        # magnitude estimate is low: that is the principle of early warning
        if JMA_WARNING_MARK in title and severity is not Severity.EXTREME:
            severity = Severity.SEVERE

        return [
            Event(
                id=f"jma_eew:{event_id}",
                source="jma_eew",
                source_id=str(event_id),
                kind=Kind.EARTHQUAKE,
                time=time,
                lat=data.get("Latitude"),
                lon=data.get("Longitude"),
                depth_km=data.get("Depth"),
                magnitude=float(magnitude) if isinstance(magnitude, (int, float)) else None,
                mag_type="Mj",
                place=place,
                country="Japan",
                country_code="JP",
                severity=severity,
                alert=f"EEW shindo {shindo}" if shindo else "EEW",
                title=f"Early warning -- {place} (shindo {shindo})" if shindo else place,
                url="https://www.jma.go.jp/bosai/map.html#contents=earthquake_map",
                raw={
                    "serial": data.get("Serial"),
                    "report_title": title,
                    "status": status,
                    "max_intensity": shindo,
                    "is_final": data.get("isFinal"),
                    "relay": "wolfx (unofficial)",
                },
            )
        ]


class CencSource(JsonPollSource):
    """CENC (China) -- mainland China has no other coverage here.

    The payload is not an array but a dictionary indexed `No1`, `No2`...
    Iterating over the keys would yield the strings "No1", not the events.
    """

    name = "cenc"
    kind = "poll"
    url = "https://api.wolfx.jp/cenc_eqlist.json"

    def parse_payload(self, data: Any) -> list[Event]:
        if not isinstance(data, dict):
            return []

        events: list[Event] = []
        for row in data.values():
            if not isinstance(row, dict):
                continue
            event_id = row.get("EventID")
            if not event_id:
                continue

            time = _parse_local(row.get("time"), CST)
            if time is None:
                continue

            def number(value) -> float | None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            magnitude = number(row.get("magnitude"))
            lat, lon = number(row.get("latitude")), number(row.get("longitude"))
            place = row.get("placeName") or row.get("location") or "China"

            events.append(
                Event(
                    id=f"cenc:{event_id}",
                    source="cenc",
                    source_id=str(event_id),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=number(row.get("depth")),
                    magnitude=magnitude,
                    mag_type="M",
                    place=place,
                    country="China",
                    country_code="CN",
                    severity=severity_from_magnitude(magnitude),
                    # "reviewed" (checked by an analyst) vs "automatic"
                    alert=row.get("type"),
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url="https://news.ceic.ac.cn/",
                    raw={
                        "type": row.get("type"),
                        "intensity": row.get("intensity"),
                        "relay": "wolfx (unofficial)",
                    },
                )
            )
        return events
