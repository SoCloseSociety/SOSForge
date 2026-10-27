"""JMA volcanic warnings -- www.jma.go.jp/bosai/volcano/data/warning.json

Why it exists here. JMA's own volcano list names 120 volcanoes, and its
five-level alert scale (噴火警戒レベル) is what orders the actual
evacuations. The product's volcano coverage was the US (HANS), New Zealand
(GeoNet) and the ash SIGMETs: a Japanese volcano appeared only once its ash
cloud reached an aviation advisory, and the level that tells residents to
leave never appeared at all -- on a product with a Japanese interface.

Free, no key, the same `bosai` JSON family as the quake and tsunami feeds.
Licence: the JMA site's terms (気象庁ホームページについて) put its content
under the Public Data License 1.0 (公共データ利用規約), reuse allowed with the
credit 「出典：気象庁ホームページ」.

Traps verified against the real payload (captured 2026-10-26):

- **The list is the volcanoes UNDER a warning, plus the ones just lowered.**
  14 entries: twelve at level 2 or its equivalents, and two lowered back to
  normal days ago (Kuchinoerabujima to レベル１, condition 引下げ, municipality
  item 「噴火予報：警報解除」; Beyonesu Rocks to 活火山であることに留意（海底火山）).
  A lowered volcano is withdrawn, not published at its new normal level.
- **The level lives in the TEXT.** Each item carries `code` ("12") and
  `name` ("レベル２（火口周辺規制）"). The numeric codes are a JMA table this
  feed never spells out, and lesson 27 is what happens when a bare rank is
  read from memory -- so the level is read from the wording, exactly like the
  tsunami categories. The digits are FULL-WIDTH (`２`, not `2`): NFKC first.
- **Not every volcano has levels.** Iwo Jima and Nishinoshima carry
  「火口周辺危険」 (danger near the crater), and the submarine ones carry
  「周辺海域警戒」 (warning for the surrounding sea). Mapped by their meaning
  onto the same ladder; an unknown wording is logged and NOT published,
  because guessing its rank is exactly how a warning gets the wrong colour.
- **`reportDatetime` is when the level was last CHANGED or re-issued**, not a
  freshness stamp: Iwo Jima's entry is from 2007-12-01 and still in force.
  That is the honest event time -- the alert is `ongoing`, so the horizon
  does not cut it, and its age says how long the level has stood.
- **No coordinates in the warning list.** They come from
  `bosai/volcano/const/volcano_list.json` (120 volcanoes, `latlon` as two
  STRINGS, latitude first), joined on `eventId == code`. All 14 joined. That
  list is a constant: loaded once, retried each cycle until it loads, and its
  absence costs the position, never the alert.
"""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, to_utc
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
WARNING_URL = "https://www.jma.go.jp/bosai/volcano/data/warning.json"
LIST_URL = "https://www.jma.go.jp/bosai/volcano/const/volcano_list.json"
PAGE_URL = "https://www.jma.go.jp/bosai/map.html#contents=volcano"

# The item that states the level OF THE VOLCANO (the other block names the
# municipalities the warning applies to).
VOLCANO_BLOCK = "噴火警報・予報（対象火山）"

# Ordered, first match wins. "活火山であることに留意" (keep in mind this is an
# active volcano) is the NORMAL state and is tested first, because the level-1
# wording contains it: "レベル１（活火山であることに留意）".
NORMAL = "活火山であることに留意"
LEVELS: list[tuple[str, str, Severity]] = [
    ("レベル5", "alert level 5 (evacuate)", Severity.EXTREME),
    ("レベル4", "alert level 4 (prepare to evacuate)", Severity.EXTREME),
    ("レベル3", "alert level 3 (do not approach the volcano)", Severity.SEVERE),
    ("レベル2", "alert level 2 (do not approach the crater)", Severity.MODERATE),
    # Volcanoes without a level scale, ranked by what the words say.
    ("居住地域", "danger to residential areas", Severity.EXTREME),
    ("入山危険", "danger on the mountain", Severity.SEVERE),
    ("火口周辺危険", "danger near the crater", Severity.MODERATE),
    # Submarine volcanoes: a warning to shipping around them.
    ("周辺海域警戒", "warning for the surrounding sea", Severity.MODERATE),
]


def classify(name: str | None) -> tuple[str, Severity] | None:
    """One volcano-level wording -> (English label, severity), or None for the
    normal state. Raises LookupError on a wording this module does not know."""
    text = unicodedata.normalize("NFKC", name or "").strip()
    if not text or NORMAL in text:
        return None
    for token, label, severity in LEVELS:
        if token in text:
            return label, severity
    raise LookupError(text)


def _coordinate(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class JmaVolcanoSource(Source):
    name = "jma-volcano"
    kind = "poll"

    def __init__(self, poll_seconds: float = 300.0, url: str = WARNING_URL):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url
        # volcano code -> (lat, lon, English name)
        self.volcanoes: dict[str, tuple[float | None, float | None, str | None]] = {}

    def load_list(self, data: Any) -> None:
        for row in data or []:
            code = row.get("code") if isinstance(row, dict) else None
            if not code:
                continue
            latlon = row.get("latlon") or []
            lat = _coordinate(latlon[0]) if len(latlon) > 0 else None
            lon = _coordinate(latlon[1]) if len(latlon) > 1 else None
            if lat is not None and not -90 <= lat <= 90:
                lat = None
            if lon is not None and not -180 <= lon <= 180:
                lon = None
            self.volcanoes[str(code)] = (lat, lon, row.get("name_en") or row.get("name_jp"))

    def parse_payload(self, data: Any) -> list[Event]:
        events: list[Event] = []
        for entry in data or []:
            if not isinstance(entry, dict):
                continue
            event = self._parse_entry(entry)
            if event is not None:
                events.append(event)
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    def _parse_entry(self, entry: dict) -> Event | None:
        code = entry.get("eventId")
        if not code:
            return None
        event_id = f"jma-volcano:{code}"
        item = None
        municipalities: list[str] = []
        for block in entry.get("volcanoInfos") or []:
            items = block.get("items") or []
            if block.get("type") == VOLCANO_BLOCK and items:
                item = items[0]
            else:
                for other in items:
                    municipalities += [a.get("name") for a in other.get("areas") or []]
        if item is None:
            return None

        try:
            graded = classify(item.get("name"))
        except LookupError as exc:
            log.warning("jma-volcano: unknown level wording %r for %s, not published", exc, code)
            return None
        if graded is None:
            # Back to normal: withdrawn, as a lift.
            self.lifts.append(event_id)
            return None
        label, severity = graded

        time = to_utc(entry.get("reportDatetime"))
        if time is None:
            return None
        lat, lon, name = self.volcanoes.get(str(code), (None, None, None))
        volcano_name = name or next(
            (a.get("name") for a in item.get("areas") or [] if a.get("name")), str(code)
        )
        return Event(
            id=event_id,
            source="jma-volcano",
            source_id=str(code),
            kind=Kind.VOLCANO,
            time=time,
            lat=lat,
            lon=lon,
            place=volcano_name,
            # The warning applies to Japanese municipalities, named in the
            # same entry: this is the issuing country stating its own
            # territory, not a guess from a position.
            country="Japan",
            country_code="JP",
            severity=severity,
            ongoing=True,
            alert=label,
            title=f"{volcano_name} -- JMA volcanic warning, {label}",
            url=PAGE_URL,
            raw={
                "level": item.get("name"),
                "code": item.get("code"),
                "previous_code": item.get("lastCode"),
                "condition": item.get("condition"),
                "municipalities": [m for m in municipalities if m],
            },
        )

    async def _load_list(self, client: httpx.AsyncClient) -> None:
        if self.volcanoes:
            return
        try:
            resp = await client.get(LIST_URL)
            resp.raise_for_status()
            self.load_list(resp.json())
            self.health.feed_ok("volcano-list")
        except Exception as exc:
            # Degraded: the alerts still go out, without a position.
            self.health.feed_failed("volcano-list", exc)
            log.warning("jma-volcano: volcano list unavailable (%s)", exc)

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                await self._load_list(client)
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    events = self.parse_payload(resp.json())
                    for event in events:
                        await emit(event)
                    await self.flush_retractions()
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)
