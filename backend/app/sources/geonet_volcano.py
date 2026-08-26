"""GeoNet volcanic alert levels (New Zealand) -- api.geonet.org.nz/volcano/val

Why this source exists. The product's volcano filter silently meant "United
States only": its single volcano source is the USGS HANS feed. A filter that
shows zero events reads as "everything is calm", not as "we do not look there".
Whakaari/White Island killed twenty-two people in 2019, and it is at alert
level 2 as this is written.

Traps verified against the real payload (captured 2026-08-26):

- **VAL carries no timestamp of any kind.** No field in the payload, no
  `Last-Modified` header (`cache-control: max-age=10` and nothing else). It is
  a statement of the CURRENT STATE, not an event. Dating it "now" would make a
  level that has stood since June flash as breaking on every cold start --
  lesson 4, exactly. So the date comes from the Volcanic Activity Bulletins in
  the GeoNet news feed, and only when that bulletin still agrees with the
  current level (see `_dated`).
- **The `Accept` version header matters, and version 1 is gone.** `version=2`
  is what the service answers with by default; asking for `version=1`
  explicitly returns **HTTP 400**. We send version 2 rather than rely on the
  default staying put.
- **The key is the VOLCANO, not the bulletin.** `volcanoID` ("whiteisland",
  "ruapehu"). This is the HANS trap the product already paid for once, where
  notices stacked up as separate markers.
- **The bulletin titles carry zero-width spaces.** Two of the 37 real bulletins
  read `​Whakaari/White Island​` with U+200B around the name. A naive substring
  match on the visible text misses them.
- **The bulletin names the volcano only in free text**, and not by the name VAL
  uses: VAL says "White Island", three bulletins say only "Whakaari". Hence the
  alias table -- built from the 37 bulletins actually read, not guessed.

Measured on the live feed: eight pages of the news feed hold 37 bulletins, and
they cover exactly two volcanoes, Ruapehu and Whakaari/White Island -- the same
two that VAL currently reports above level 0. Page one alone dated both.

Removal. GeoNet publishes no expiry, and none can be invented: a volcanic alert
level ends when GeoNet lowers it. The exit is the same one HANS relies on --
we stop publishing a volcano once it drops below `min_level`, and the store's
stale sweep removes what has gone silent (a VOLCANO is not an EARTHQUAKE, so
silence reaches it).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import httpx

from app.models.event import Event, Kind, Severity, to_utc, utcnow
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

URL = "https://api.geonet.org.nz/volcano/val"
NEWS_URL = "https://api.geonet.org.nz/news/geonet"
USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
ACCEPT = "application/vnd.geo+json;version=2"
# The news endpoint speaks plain JSON and answers **HTTP 400** to the geo+json
# content type VAL requires. Two endpoints on the same host, two Accept
# headers: the client-wide one cannot serve both.
NEWS_ACCEPT = "application/json;version=2"

# New Zealand's Volcanic Alert Level scale, as GeoNet defines it:
# 0 no unrest, 1 minor unrest, 2 moderate to heightened unrest,
# 3 minor eruption, 4 moderate eruption, 5 major eruption.
# The jump that matters is 2 -> 3: unrest becomes eruption.
LEVEL_SEVERITY = {
    0: Severity.INFO,
    1: Severity.MINOR,
    2: Severity.MODERATE,
    3: Severity.SEVERE,
    4: Severity.EXTREME,
    5: Severity.EXTREME,
}

# Names a bulletin may use for a volcano, when they differ from `volcanoTitle`.
# Read off the 37 real bulletins; anything not listed falls back to the title
# itself, which is what the other ten volcanoes would need.
VOLCANO_ALIASES: dict[str, tuple[str, ...]] = {
    "whiteisland": ("whakaari", "white island"),
    "taranakiegmont": ("taranaki", "egmont"),
    "aucklandvolcanicfield": ("auckland volcanic field",),
    "tongariro": ("tongariro",),
    "ruapehu": ("ruapehu",),
}

# U+200B..U+200D and the BOM: invisible, and present in real bulletin titles.
RE_INVISIBLE = re.compile(r"[​‌‍﻿]")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", RE_INVISIBLE.sub("", text or "")).strip().lower()


def aliases_for(volcano_id: str, title: str) -> tuple[str, ...]:
    known = VOLCANO_ALIASES.get(volcano_id)
    if known:
        return known
    normalized = normalize(title)
    return (normalized,) if normalized else ()


class GeonetVolcanoSource(Source):
    """Current volcanic alert level for every New Zealand volcano."""

    name = "geonet-volcano"
    kind = "poll"

    def __init__(
        self,
        poll_seconds: float = 300.0,
        url: str = URL,
        news_url: str = NEWS_URL,
        min_level: int = 1,
        news_pages: int = 3,
        news_refresh_seconds: float = 3600.0,
    ):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.url = url
        self.news_url = news_url
        # Level 0 is literally "No volcanic unrest": ten of the twelve New
        # Zealand volcanoes sit there permanently. Publishing them would put
        # ten "nothing is happening" markers on an emergency map, which is the
        # GDACS lesson (an aggregating source arrives with a relevance rule).
        # Configurable, because "show me every volcano you watch" is a
        # legitimate thing to want.
        self.min_level = min_level
        self.news_pages = news_pages
        self.news_refresh_seconds = news_refresh_seconds
        # volcano_id -> (published, level, title, link), newest bulletin only
        self._bulletins: dict[str, tuple[datetime, int, str, str | None]] = {}
        self._bulletins_at: datetime | None = None
        # Fallback dating: when we first saw a volcano at a given level.
        self._first_seen: dict[tuple[str, int], datetime] = {}
        # volcano_id -> volcanoTitle, learned from the last VAL payload, so a
        # volcano that is not in the alias table can still be matched by the
        # name GeoNet itself gives it.
        self._titles: dict[str, str] = {}

    def _alias_map(self) -> dict[str, tuple[str, ...]]:
        by_id = {vid: aliases_for(vid, title) for vid, title in self._titles.items()}
        by_id.update(VOLCANO_ALIASES)
        return {vid: names for vid, names in by_id.items() if names}

    # ---------------------------------------------------------------- dating

    async def _load_bulletins(self, client: httpx.AsyncClient) -> None:
        """Newest Volcanic Activity Bulletin per volcano, from the news feed.

        Bulletins are published weekly to monthly, so this is refreshed on its
        own slow clock. It is best-effort by design: a failure here costs the
        date, never the alert level, and must not mark the source unhealthy.
        """
        now = utcnow()
        if (
            self._bulletins_at is not None
            and (now - self._bulletins_at).total_seconds() < self.news_refresh_seconds
        ):
            return

        found: dict[str, tuple[datetime, int, str, str | None]] = {}
        alias_map = self._alias_map()
        try:
            for page in range(1, self.news_pages + 1):
                resp = await client.get(
                    self.news_url,
                    params={"page": page},
                    headers={"Accept": NEWS_ACCEPT},
                )
                resp.raise_for_status()
                for entry in (resp.json() or {}).get("feed") or []:
                    if entry.get("type") != "vab":
                        continue
                    published = to_utc(entry.get("published"))
                    level = entry.get("val")
                    title = entry.get("title") or ""
                    if published is None or not isinstance(level, int):
                        continue
                    text = normalize(title)
                    for volcano_id, names in alias_map.items():
                        if not any(name in text for name in names):
                            continue
                        previous = found.get(volcano_id)
                        if previous is None or published > previous[0]:
                            found[volcano_id] = (published, level, title, entry.get("link"))
        except Exception as exc:
            log.warning("geonet-volcano: bulletins unavailable (%s), levels undated", exc)
            if self._bulletins:
                return

        self._bulletins = found or self._bulletins
        self._bulletins_at = now

    def _dated(self, volcano_id: str, level: int) -> tuple[datetime, str | None, str | None]:
        """When was this level reported, and by which bulletin.

        The bulletin is only allowed to date the level if it still AGREES with
        it. A volcano that has just jumped from 1 to 3 has no bulletin yet, and
        borrowing the previous one would date a fresh escalation two months
        ago -- it would never read as breaking, which is the one case where
        that matters most. Then the honest answer is "we saw it change now".
        """
        bulletin = self._bulletins.get(volcano_id)
        if bulletin is not None and bulletin[1] == level:
            return bulletin[0], bulletin[2], bulletin[3]
        return self._first_seen.setdefault((volcano_id, level), utcnow()), None, None

    # --------------------------------------------------------------- parsing

    def parse_payload(self, data: dict) -> list[Event]:
        events: list[Event] = []
        for feature in (data or {}).get("features") or []:
            props = feature.get("properties") or {}
            volcano_id = props.get("volcanoID")
            level = props.get("level")
            if not volcano_id or not isinstance(level, int):
                continue
            if level < self.min_level:
                continue

            title = props.get("volcanoTitle") or volcano_id
            self._titles[str(volcano_id)] = str(title)
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None
            colour = (props.get("acc") or "").strip()
            time, headline, link = self._dated(volcano_id, level)

            events.append(
                Event(
                    # The VOLCANO is the key, never the bulletin.
                    id=f"geonet-volcano:{volcano_id}",
                    source="geonet-volcano",
                    source_id=str(volcano_id),
                    kind=Kind.VOLCANO,
                    time=time,
                    lat=lat,
                    lon=lon,
                    place=title,
                    # Every volcano GeoNet reports is New Zealand's, the
                    # Kermadec Islands included.
                    country="New Zealand",
                    severity=LEVEL_SEVERITY.get(level, Severity.INFO),
                    # A standing alert level. No expiry exists to quote, so the
                    # stale sweep is what removes it once GeoNet stops
                    # reporting the volcano above `min_level`.
                    ongoing=True,
                    # Aviation colour code, same badge the HANS source uses.
                    alert=colour.lower() or None,
                    title=headline or f"{title} -- volcanic alert level {level}",
                    url=link or f"https://www.geonet.org.nz/volcano/{volcano_id}",
                    raw={
                        "level": level,
                        "aviation_colour_code": colour,
                        "activity": props.get("activity"),
                        "hazards": props.get("hazards"),
                        # Says plainly where the date came from: a real
                        # bulletin, or our own first sighting of this level.
                        "dated_by": "bulletin" if headline else "first_seen",
                    },
                )
            )
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    await self._load_bulletins(client)
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    events = self.parse_payload(resp.json())
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)
