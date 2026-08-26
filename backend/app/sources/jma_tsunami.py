"""JMA tsunami advisories -- www.jma.go.jp/bosai/tsunami/data/list.json

Why this source exists. The product ships a Japanese UI and Japan is the most
tsunami-exposed population it serves, yet its only tsunami sources were two US
centres (NTWC/PTWC) covering the Pacific and the US coasts. A JMA 津波注意報
issued for the Ariake Sea never appeared at all. This closes that hole with the
authority that actually orders the evacuation in Japan.

Free, no key, machine-readable.

Traps verified against the real payload (captured 2026-08-26):

- **`kind[].code` is an AREA code, NOT a category code.** The list gives
  `[{"code": "712", "kind": "津波注意報"}, {"code": "730", "kind": "津波予報
  （若干の海面変動）"}]`. Cross-read against the detail bulletin
  (`VTSE41`, same event) that is unambiguous: 712 is `Area.Code` for
  "ARIAKE SEA AND YATSUSHIRO SEA" and 730 is "WESTERN PART OF NAGASAKI PREF.",
  while the CATEGORY codes in the same bulletin are 62 (Tsunami Advisory) and
  71 (Tsunami Forecast). Reading `code` as a severity would rank an advisory
  for Nagasaki above a major warning. **The category lives only in the `kind`
  text.**
- **One event, several bulletins.** The three rows in the live list all carry
  `eid` `20260728162718`: the advisory, the arrival-time information, and the
  lift. Keying on the bulletin would stack markers exactly like the HANS
  volcano notices did. The key is the EVENT.
- **`ser` changes type between rows**: `0` (int) in two of them, `"1"` (str) in
  the third. Never compared numerically here.
- **A bulletin can carry no category at all** (`"kind": []` -- the high-tide /
  arrival-time information). It says nothing about the advisory level, so it
  cannot be the row we read the level from.
- The list keeps an event long after it is over: on 2026-08-26 the only entry
  was the Kumamoto M7.1 of 2026-07-28, lifted the same evening. So silence is
  never what removes a JMA tsunami advisory. The lift is (see below).
- `cod` is ISO 6709 with the JMA conventions already documented for the quake
  feed: depth in METRES and NEGATIVE, and a degrees-minutes variant that must
  be rejected rather than parsed. `parse_iso6709` is reused, not reimplemented.

Removal. JMA publishes no expiry, and it does not stop listing the event when
the advisory ends -- it publishes a **解除** (lift) bulletin. So the exit is the
retraction channel: once every area of the latest bulletin is lifted, the event
is withdrawn. `expires` is kept as a second, explicit ceiling, because an alert
that can never expire is a bug: it is OUR ceiling, not JMA's word.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, to_utc
from app.sources.base import Emit, Source
from app.sources.regional import parse_iso6709

log = logging.getLogger(__name__)

URL = "https://www.jma.go.jp/bosai/tsunami/data/list.json"
USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"

# A lifted category. Checked FIRST, because "津波注意報解除" also contains
# "津波注意報": the same ordering trap as "大津波警報" below.
LIFTED = "解除"

# Ordered, first match wins -- the nws.py rule, for the same reason:
# "大津波警報" (major tsunami warning) CONTAINS "津波警報" (tsunami warning) as a
# substring, so the major one has to be tested first or every major warning is
# demoted to a plain warning.
#
# Matching on the Japanese wording rather than on a code table is deliberate:
# the wording is compositional and directly observable in the payload, whereas
# the numeric category codes appear nowhere in `list.json` and guessing them
# from memory is exactly what this product forbids. The suffixed variants JMA
# uses (e.g. "津波警報（継続）") fall out of the substring test for free.
#
# The English labels are OURS, for display. They are derived from the verified
# Japanese, not parsed from the feed.
CATEGORIES: list[tuple[str, str, Severity]] = [
    ("大津波警報", "major tsunami warning", Severity.EXTREME),
    ("津波警報", "tsunami warning", Severity.SEVERE),
    ("津波注意報", "tsunami advisory", Severity.MODERATE),
    ("津波予報", "tsunami forecast", Severity.INFO),
]

SEVERITY_RANK = list(Severity)


def classify(category: str | None) -> tuple[str, Severity] | None:
    """Reads one `kind[].kind` label. Returns None when the category is a lift
    or is not a tsunami category at all."""
    text = (category or "").strip()
    if not text or LIFTED in text:
        return None
    for token, label, severity in CATEGORIES:
        if token in text:
            return label, severity
    return None


class JmaTsunamiSource(Source):
    name = "jma-tsunami"
    kind = "poll"

    def __init__(
        self,
        poll_seconds: float = 30.0,
        url: str = URL,
        max_advisory_hours: float = 12.0,
    ):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url
        # Ceiling of our own, not JMA's: see the module docstring. Japanese
        # tsunami advisories are lifted in hours, and the lift is what normally
        # removes the event. This only catches the case where we never see it.
        self.max_advisory_hours = max_advisory_hours

    def parse_payload(self, data: Any) -> list[Event]:
        # Group by EVENT, not by bulletin, and keep the most recent bulletin
        # that actually states a tsunami category. `ctt` is the bulletin
        # creation stamp in compact JST: zero-padded and fixed width, so
        # comparing the strings is comparing the instants.
        latest: dict[str, dict] = {}
        for row in data or []:
            eid = row.get("eid")
            if not eid or not (row.get("kind") or []):
                continue
            previous = latest.get(str(eid))
            if previous is None or str(row.get("ctt") or "") > str(previous.get("ctt") or ""):
                latest[str(eid)] = row

        events: list[Event] = []
        for eid, row in latest.items():
            event = self._parse_row(eid, row)
            if event is not None:
                events.append(event)
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    def _parse_row(self, eid: str, row: dict) -> Event | None:
        event_id = f"jma-tsunami:{eid}"

        areas = row.get("kind") or []
        graded = [classify(area.get("kind")) for area in areas]
        live = [g for g in graded if g is not None]
        if not live:
            # Every area of the latest bulletin is lifted (or states no
            # tsunami): the advisory is over. Withdraw it rather than leave it
            # on the map -- the feed will keep listing this event for weeks.
            self.retractions.append(event_id)
            return None

        label, severity = max(live, key=lambda g: SEVERITY_RANK.index(g[1]))

        # The event is the ADVISORY, so it is dated when JMA issued it. Using
        # the quake origin time instead would mis-date a distant-source
        # tsunami by the hours the wave takes to travel, and an advisory
        # issued now would arrive already too old to read as breaking.
        time = to_utc(row.get("rdt"))
        if time is None:
            return None

        lat, lon, depth = parse_iso6709(row.get("cod"))
        try:
            magnitude = float(row["mag"]) if row.get("mag") not in (None, "") else None
        except (TypeError, ValueError):
            magnitude = None

        epicentre = row.get("en_anm") or row.get("anm") or "unknown region"

        return Event(
            id=event_id,
            source="jma-tsunami",
            source_id=str(eid),
            kind=Kind.TSUNAMI,
            time=time,
            lat=lat,
            lon=lon,
            depth_km=depth,
            magnitude=magnitude,
            mag_type="Mj" if magnitude is not None else None,
            # `place` is where the ALERT applies, and a JMA tsunami bulletin
            # always applies to Japanese coasts -- this is the mirror image of
            # the distant-quake trap in the JMA quake feed, not the same case:
            # there JMA RELAYS a foreign event, here it ISSUES a Japanese
            # alert about a source that may be foreign. The epicentre is named
            # in the title so the two can never be confused, and lat/lon are
            # the epicentre because it is the only position the feed gives.
            place="Japan",
            country="Japan",
            severity=severity,
            ongoing=True,
            expires=time + timedelta(hours=self.max_advisory_hours),
            tsunami=True,
            alert=label,
            title=f"JMA {label} for Japan -- epicentre {epicentre}"
            + (f" (M {magnitude})" if magnitude is not None else ""),
            url="https://www.jma.go.jp/bosai/map.html#contents=tsunami",
            raw={
                "report": row.get("ttl"),
                "en_report": row.get("en_ttl"),
                "epicentre": epicentre,
                "origin_time": row.get("at"),
                "bulletin": row.get("ctt"),
                # area CODES, deliberately not names: the list gives no names
                # and inventing them is not an option
                "area_codes": [area.get("code") for area in areas],
                "categories": [area.get("kind") for area in areas],
            },
        )

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
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
