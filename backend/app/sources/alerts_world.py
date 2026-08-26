"""Official alerts outside the USA.

The hole these two sources close: SOSForge only had weather, flood and storm
alerts for the United States (`nws`). The rest of the world only had GDACS,
which only sees major disasters.

- **Meteoalarm** aggregates the warnings of the European national weather
  services, in CAP, one feed per country.
- **the WMO CAP aggregate** covers the rest (India, China, Indonesia, South
  America...) in a single call.

Positions, measured on the payloads of 2026-08-26:

- **Meteoalarm** publishes no geometry, with one exception: the UK Met Office
  fills `area[].polygon` inside the JSON itself. Everything else -- 28896 area
  blocks out of 28898 -- names an `EMMA_ID` (`NUTS3` for France, `WARNCELLID`
  for Germany) and an administrative area, and Meteoalarm publishes no table
  that turns either into a shape: `api.meteoalarm.org/metadata/v1/regions`
  answers 401, every other candidate endpoint 404, and the only geometry its
  own map has is inside MVT tiles. So those warnings stay unplaced.
- **the WMO aggregate** publishes no geometry either, but it links a CAP
  document that often does: about one Severe-or-Extreme alert in two. That is
  what `cap_area.py` goes and gets.

Resolving the remaining codes would mean sending administrative names to a
third-party gazetteer. Refused: a same-name collision puts a warning in the
wrong province, and lesson 15 is that a wrong position is far worse than a
missing one. An event with no position still shows in the feed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, to_utc
from app.sources.base import Emit, Source
from app.sources.cap_area import WMO_CAP_BASE, CapAreaCache, parse_polygon
from app.sources.nws import _matches
from app.sources.regional import USER_AGENT, JsonPollSource

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ Meteoalarm

METEOALARM_API = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-{country}"

# The most exposed and most populated countries of the covered area. The list
# is deliberately short: it is one GET per country per cycle, and Meteoalarm
# exposes no pan-European feed (`feeds-europe` returns 404).
METEOALARM_COUNTRIES = (
    "france",
    "italy",
    "spain",
    "germany",
    "greece",
    "portugal",
    "united-kingdom",
    "poland",
    "netherlands",
    "croatia",
)

# `awareness_type` is a standard Meteoalarm code, in English, whereas `event`
# is written in the country's language. So it is the one that must drive the type.
AWARENESS_TYPE_KIND = {
    "1": Kind.STORM,  # wind
    "2": Kind.STORM,  # snow, black ice
    "3": Kind.STORM,  # thunderstorms
    "4": Kind.OTHER,  # fog
    "5": Kind.HEAT,  # high temperature
    "6": Kind.STORM,  # low temperature
    "7": Kind.FLOOD,  # coastal event
    "8": Kind.WILDFIRE,  # forest fire
    "9": Kind.OTHER,  # avalanches
    "10": Kind.STORM,  # rain
    "11": Kind.FLOOD,  # flooding
    "12": Kind.FLOOD,  # rain-flood
}

# level 1 green (no danger) -> 4 red (major danger)
AWARENESS_LEVEL_SEVERITY = {
    "1": Severity.INFO,
    "2": Severity.MODERATE,
    "3": Severity.SEVERE,
    "4": Severity.EXTREME,
}


def _parameters(info: dict) -> dict[str, str]:
    return {
        p.get("valueName"): p.get("value")
        for p in info.get("parameter") or []
        if p.get("valueName")
    }


def _level_of(event: Event) -> int:
    """Meteoalarm level (1 green -> 4 red) re-read from the raw payload."""
    raw = (event.raw.get("awareness_level") or "").split(";")[0].strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _meteoalarm_position(areas: list[dict]) -> tuple[float | None, float | None]:
    """The UK Met Office is the one Meteoalarm producer that draws its
    warnings: `area[].polygon` holds a LIST of CAP rings (`lat,lon` pairs,
    latitude first). Everyone else names an EMMA_ID and nothing else, and
    there is no public table from that code to a shape -- so no position,
    rather than a guessed one."""
    lats: list[float] = []
    lons: list[float] = []
    for area in areas:
        rings = area.get("polygon")
        if isinstance(rings, str):  # CAP allows a single ring, not only a list
            rings = [rings]
        for ring in rings or []:
            point = parse_polygon(ring)
            if point:
                lats.append(point[0])
                lons.append(point[1])
    if not lats:
        return None, None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _pick_info(blocks: list[dict]) -> dict | None:
    """A Meteoalarm alert carries the same content twice: local language and
    English. Without this choice, each warning produced two events."""
    if not blocks:
        return None
    for block in blocks:
        if (block.get("language") or "").lower().startswith("en"):
            return block
    return blocks[0]


def parse_meteoalarm(warning: dict, country: str) -> Event | None:
    alert = warning.get("alert") or {}
    identifier = alert.get("identifier")
    info = _pick_info(alert.get("info") or [])
    if not identifier or not info:
        return None

    params = _parameters(info)
    level, awareness_type = None, None
    if params.get("awareness_level"):
        # compound format: "1; green; Minor"
        level = params["awareness_level"].split(";")[0].strip()
    if params.get("awareness_type"):
        awareness_type = params["awareness_type"].split(";")[0].strip()

    severity = AWARENESS_LEVEL_SEVERITY.get(level or "", Severity.INFO)
    kind = AWARENESS_TYPE_KIND.get(awareness_type or "", Kind.OTHER)

    # `AllClear` means the warning is LIFTED. Like the "no danger" tsunami
    # bulletins, it is displayed but does not alert.
    lifted = "AllClear" in (info.get("responseType") or [])
    if lifted:
        severity = Severity.INFO

    areas = info.get("area") or []
    place = ", ".join(a.get("areaDesc", "") for a in areas[:3] if a.get("areaDesc"))
    lat, lon = _meteoalarm_position(areas)
    time = to_utc(info.get("onset")) or to_utc(info.get("effective"))
    if time is None:
        return None

    return Event(
        id=f"meteoalarm:{identifier}",
        source="meteoalarm",
        source_id=identifier,
        kind=kind,
        time=time,
        lat=lat,
        lon=lon,
        place=place or country.replace("-", " ").title(),
        country=country.replace("-", " "),
        severity=severity,
        # a warning runs until its expiration: it is an ongoing alert
        ongoing=not lifted,
        alert=("lifted" if lifted else (params.get("awareness_level") or "").split(";")[-1].strip())
        or None,
        title=info.get("headline") or info.get("event") or place,
        url=info.get("web"),
        raw={
            "event": info.get("event"),
            "awareness_level": params.get("awareness_level"),
            "awareness_type": params.get("awareness_type"),
            "expires": info.get("expires"),
            "areas": len(areas),
        },
    )


class MeteoalarmSource(Source):
    """One GET per country per cycle, sequential: their server does not have
    to endure ten simultaneous requests every five minutes.

    **Severity threshold is mandatory.** Without it, ten European countries
    return over 2000 warnings per cycle -- essentially yellow "possible
    thunderstorms" -- which evict quakes and tsunamis from the buffer.
    SOSForge shows events, not the weather report: we only keep orange and
    red, that is, a real danger to people.
    """

    name = "meteoalarm"
    kind = "poll"

    def __init__(
        self,
        poll_seconds: float = 300.0,
        countries: tuple[str, ...] | None = None,
        min_level: int = 3,
    ):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.countries = countries or METEOALARM_COUNTRIES
        self.min_level = min_level

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=25.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                seen, alive = 0, 0
                for country in self.countries:
                    try:
                        resp = await client.get(METEOALARM_API.format(country=country))
                        resp.raise_for_status()
                        alive += 1
                        kept = 0
                        for warning in (resp.json() or {}).get("warnings") or []:
                            event = parse_meteoalarm(warning, country)
                            if event and _level_of(event) >= self.min_level:
                                kept += 1
                                await emit(event)
                        seen += kept
                        # mark health at EACH country: ten sequential GETs
                        # take over a minute, and the source looked dead for
                        # its entire first cycle
                        self.health.ok(kept)
                    except Exception as exc:
                        self.health.fail(exc)
                        log.warning("meteoalarm %s: %s", country, exc)
                # health is already marked country by country above; marking it
                # here again added the same total a second time
                if not alive:
                    log.warning("meteoalarm: no country reachable this cycle")
                await asyncio.sleep(self.poll_seconds)


# ------------------------------------------------------------------------- WMO

# `s`, `u`, `c` encode CAP severity / urgency / certainty as a rank that grows
# WITH the severity, and 0 when the producer left the field Unknown.
#
# This was read the other way round -- "1 = most severe" -- and the mistake was
# not academic. Cross-checked against the `<severity>` element of 100 CAP
# documents sampled across every rank of the 2026-08-26 aggregate:
#
#     s=0 Unknown (19/20)  s=1 Minor (20/20)  s=2 Moderate (20/20)
#     s=3 Severe  (20/20)  s=4 Extreme (20/20)
#
# So the source was keeping the 246 *Minor* alerts, publishing them as EXTREME,
# and dropping the 91 Extreme and 353 Severe ones. See `WmoCapSource` for how
# the kept set is expressed now.
WMO_SEVERITY = {
    1: Severity.MINOR,
    2: Severity.MODERATE,
    3: Severity.SEVERE,
    4: Severity.EXTREME,
}

# Same reading, same sample: `u` 1=Past 2=Future 3=Expected 4=Immediate,
# `c` 2=Possible 3=Likely 4=Observed. Kept as text in `raw` because a bare
# rank is unreadable and was already being misread once.
WMO_URGENCY = {1: "past", 2: "future", 3: "expected", 4: "immediate"}
WMO_CERTAINTY = {1: "unlikely", 2: "possible", 3: "likely", 4: "observed"}

# CAP severity has four tiers; the settings express how many of them to keep,
# counting down from Extreme.
WMO_TIERS = 4

# Same matching rule as in `nws.py`, measured on the real feeds.
WMO_KIND_PATTERNS: list[tuple[tuple[str, ...], Kind]] = [
    (("tsunami",), Kind.TSUNAMI),
    (("volcano", "volcanic", "ash", "ashfall"), Kind.VOLCANO),
    (("cyclone", "hurricane", "typhoon", "tropical"), Kind.CYCLONE),
    (("flood", "flooding", "inundation", "crue"), Kind.FLOOD),
    (("fire", "wildfire", "bushfire"), Kind.WILDFIRE),
    (("earthquake", "seismic"), Kind.EARTHQUAKE),
    (("heat", "hot"), Kind.HEAT),
    (("drought",), Kind.DROUGHT),
    (("rain", "storm", "wind", "snow", "thunder", "gale", "blizzard"), Kind.STORM),
]


def classify_wmo(event_name: str) -> Kind:
    text = (event_name or "").lower()
    words = set(re.findall(r"[a-z]+", text))
    for patterns, kind in WMO_KIND_PATTERNS:
        if _matches(text, words, patterns):
            return kind
    return Kind.OTHER


def wmo_cap_path(item: dict) -> str | None:
    """Where the CAP document of this alert lives, relative to
    `severeweather.wmo.int/v2/cap-alerts/`.

    The aggregate names that path **`url` on 1247 items and `capURL` on the
    other 977**, and only `url` was ever read. So 44% of the alerts also lost
    their link back to the authoritative document, on top of their position.
    """
    path = item.get("url") or item.get("capURL")
    return str(path) if path else None


def parse_wmo(item: dict, position: tuple[float, float] | None = None) -> Event | None:
    item_id = item.get("id")
    if not item_id:
        return None

    # `sent` and `effective` have no timezone and are in UTC
    time = to_utc((item.get("sent") or "").replace(" ", "T")) or to_utc(
        (item.get("effective") or "").replace(" ", "T")
    )
    if time is None:
        return None

    def rank(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    severity = WMO_SEVERITY.get(rank(item.get("s")) or 0, Severity.INFO)

    # the identifier is prefixed with the ISO2 country code ("IN-...",
    # "CN-..."): it is the feed's only country indication, and enough to show
    # a flag
    prefix = str(item_id).split("-", 1)[0]
    country_code = prefix.upper() if len(prefix) == 2 and prefix.isalpha() else None
    event_name = item.get("event") or "alert"
    place = item.get("areaDesc") or ""
    cap_path = wmo_cap_path(item)
    lat, lon = position if position else (None, None)

    return Event(
        id=f"wmo:{item_id}",
        source="wmo",
        source_id=str(item_id),
        kind=classify_wmo(event_name),
        time=time,
        # The aggregate carries no coordinates whatsoever. What there is comes
        # from the CAP document, fetched and cached by `cap_area.py`.
        lat=lat,
        lon=lon,
        place=place[:120] or event_name,
        country_code=country_code,
        severity=severity,
        ongoing=True,
        alert=event_name.lower(),
        title=item.get("headline") or event_name,
        # the aggregated JSON has shown times inconsistent with the source
        # CAP: for any critical time, the CAP is authoritative
        url=f"{WMO_CAP_BASE}{cap_path}" if cap_path else None,
        raw={
            "event": event_name,
            "expires": item.get("expires"),
            "urgency": WMO_URGENCY.get(rank(item.get("u")) or 0),
            "certainty": WMO_CERTAINTY.get(rank(item.get("c")) or 0),
            "member": item.get("mid"),
        },
    )


class WmoCapSource(JsonPollSource):
    """Worldwide CAP aggregate of the World Meteorological Organization.

    One megabyte per call: we send `If-Modified-Since` to get a 304 as long
    as the file has not moved, rather than re-downloading 2200 alerts every
    five minutes. We still re-emit on a 304 -- the alerts are `ongoing`, and
    the sweep removes an ongoing event a source has stopped mentioning
    (lesson 17), so silence for an unchanged file would eventually erase them.
    Re-emitting is also how a position learned on a later cycle reaches open
    tabs: it changes the fingerprint, so the store publishes a revision.
    """

    name = "wmo"
    kind = "poll"
    url = "https://severeweather.wmo.int/json/wmo_all.json"

    def __init__(
        self,
        poll_seconds: float = 300.0,
        max_severity_rank: int = 1,
        cap_cache: Path | None = None,
    ):
        super().__init__(poll_seconds)
        self._last_modified: str | None = None
        self._items: list[dict] = []
        # How many CAP severity TIERS to keep, counting down from Extreme:
        # 1 = Extreme only, 2 = Severe and above. Beyond that we enter everyday
        # weather-bulletin territory, and the aggregate holds 1500 Moderate
        # alerts per cycle -- enough to fill the buffer by itself and bury
        # everything else.
        #
        # The setting is read as a number of tiers and NOT as the value of `s`,
        # because `s` grows with the severity (measured, see WMO_SEVERITY) while
        # this setting was written believing the opposite. Reading it as tiers
        # keeps `wmo_max_severity_rank = 1` meaning what its author meant --
        # "the top tier only" -- instead of the bottom one.
        self.max_severity_rank = max_severity_rank
        self.min_rank = max(1, WMO_TIERS + 1 - max(1, max_severity_rank))
        self.areas = CapAreaCache(cap_cache)

    def select(self, data: Any) -> list[dict]:
        """The raw items this source publishes, severity filter applied."""
        kept = []
        for item in (data or {}).get("items") or []:
            try:
                rank = int(item.get("s"))
            except (TypeError, ValueError):
                continue
            if rank >= self.min_rank:
                kept.append(item)
        return kept

    def build_events(self, items: list[dict]) -> list[Event]:
        events = []
        for item in items:
            path = wmo_cap_path(item)
            event = parse_wmo(item, self.areas.known(path) if path else None)
            if event:
                events.append(event)
        return events

    def parse_payload(self, data: Any) -> list[Event]:
        return self.build_events(self.select(data))

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=60.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    conditional = dict(headers)
                    if self._last_modified:
                        conditional["If-Modified-Since"] = self._last_modified

                    resp = await client.get(self.url, headers=conditional)
                    fresh = 0
                    if resp.status_code != 304:
                        resp.raise_for_status()
                        self._last_modified = resp.headers.get("last-modified")
                        self._items = self.select(resp.json())
                        fresh = len(self._items)

                    # Learn a few CAP documents per cycle, starting with the
                    # alerts still unplaced. The aggregate republishes the whole
                    # list every cycle, so an alert that misses its turn is
                    # placed on the next one instead of costing the agency a
                    # burst of a thousand requests.
                    wanted = [
                        path
                        for item in self._items
                        if (path := wmo_cap_path(item)) and not self.areas.has(path)
                    ]
                    if wanted:
                        await self.areas.resolve(client, wanted)

                    for event in self.build_events(self._items):
                        await emit(event)
                    self.health.ok(fresh)
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("wmo: %s", exc)
                await asyncio.sleep(self.poll_seconds)
