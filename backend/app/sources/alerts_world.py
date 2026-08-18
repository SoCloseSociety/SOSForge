"""Official alerts outside the USA.

The hole these two sources close: SOSForge only had weather, flood and storm
alerts for the United States (`nws`). The rest of the world only had GDACS,
which only sees major disasters.

- **Meteoalarm** aggregates the warnings of the European national weather
  services, in CAP, one feed per country.
- **the WMO CAP aggregate** covers the rest (India, China, Indonesia, South
  America...) in a single call.

Neither carries coordinates: areas are described by administrative codes
(NUTS3 in Europe). Events therefore come out without a position, which the
model accepts -- they show up in the feed, not on the map.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, to_utc
from app.sources.base import Emit, Source
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
    time = to_utc(info.get("onset")) or to_utc(info.get("effective"))
    if time is None:
        return None

    return Event(
        id=f"meteoalarm:{identifier}",
        source="meteoalarm",
        source_id=identifier,
        kind=kind,
        time=time,
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

# `s`, `u`, `c` encode CAP severity / urgency / certainty by their rank
# (1 = most severe), 0 when the country did not fill it in.
WMO_SEVERITY = {
    1: Severity.EXTREME,
    2: Severity.SEVERE,
    3: Severity.MODERATE,
    4: Severity.MINOR,
}

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


def parse_wmo(item: dict) -> Event | None:
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

    return Event(
        id=f"wmo:{item_id}",
        source="wmo",
        source_id=str(item_id),
        kind=classify_wmo(event_name),
        time=time,
        place=place[:120] or event_name,
        country_code=country_code,
        severity=severity,
        ongoing=True,
        alert=event_name.lower(),
        title=item.get("headline") or event_name,
        # the aggregated JSON has shown times inconsistent with the source
        # CAP: for any critical time, the CAP is authoritative
        url=f"https://severeweather.wmo.int/v2/cap-alerts/{item.get('url')}"
        if item.get("url")
        else None,
        raw={
            "event": event_name,
            "expires": item.get("expires"),
            "urgency_rank": rank(item.get("u")),
            "certainty_rank": rank(item.get("c")),
            "member": item.get("mid"),
        },
    )


class WmoCapSource(JsonPollSource):
    """Worldwide CAP aggregate of the World Meteorological Organization.

    One megabyte per call: we send `If-Modified-Since` to get a 304 as long
    as the file has not moved, rather than re-downloading 2200 alerts every
    five minutes.
    """

    name = "wmo"
    kind = "poll"
    url = "https://severeweather.wmo.int/json/wmo_all.json"

    def __init__(self, poll_seconds: float = 300.0, max_severity_rank: int = 1):
        super().__init__(poll_seconds)
        self._last_modified: str | None = None
        # `s` is a CAP rank: 1 = Extreme, 2 = Severe. Beyond that we enter
        # everyday weather-bulletin territory, and the aggregate holds 2250 of
        # those per cycle -- enough to fill the buffer by itself and bury
        # everything else.
        self.max_severity_rank = max_severity_rank

    def parse_payload(self, data: Any) -> list[Event]:
        events = []
        for item in (data or {}).get("items") or []:
            try:
                rank = int(item.get("s"))
            except (TypeError, ValueError):
                continue
            if not 1 <= rank <= self.max_severity_rank:
                continue
            event = parse_wmo(item)
            if event:
                events.append(event)
        return events

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
                    if resp.status_code == 304:
                        self.health.ok()
                    else:
                        resp.raise_for_status()
                        self._last_modified = resp.headers.get("last-modified")
                        events = self.parse_payload(resp.json())
                        for event in events:
                            await emit(event)
                        self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("wmo: %s", exc)
                await asyncio.sleep(self.poll_seconds)
