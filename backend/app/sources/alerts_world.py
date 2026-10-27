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
import html
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, severity_for_cap, to_utc
from app.sources import geometry
from app.sources.base import Emit, Source
from app.sources.cap_area import WMO_CAP_BASE, CapAreaCache, polygon_points
from app.sources.cap_text import CapText
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
    "9": Kind.AVALANCHE,
    "10": Kind.STORM,  # rain
    "11": Kind.FLOOD,  # flooding
    "12": Kind.FLOOD,  # rain-flood
}

# `awareness_level` IS a CAP rank: level 1 green (no danger) -> 4 red (major
# danger) is the harmonized European scale, and it maps onto CAP's vocabulary
# term for term. Measured on the 2622 warnings of the ten country feeds on
# 2026-08-26, the producers' own CAP `severity` element agrees with it almost
# everywhere -- level 1 -> Minor 1123/1123, level 2 -> Moderate 888/1030,
# level 3 -> Severe 396/452, level 4 -> Extreme 9/17.
#
# The level is what we read, not the element, and deliberately: the level is the
# ONE scale the ten countries share, which is the whole reason Meteoalarm
# exists. The element is each national producer's own reading, and the 8 red
# warnings it calls merely "Severe" are the disagreement, not the truth.
#
# Where the ranks land on this product's ladder is decided in one place for
# every CAP source -- `severity_for_cap` in `models/event.py` -- so that an
# orange warning and a M6 earthquake stop claiming the same rung.
AWARENESS_LEVEL_RANK = {
    "1": "minor",
    "2": "moderate",
    "3": "severe",
    "4": "extreme",
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


def _meteoalarm_area(
    areas: list[dict],
) -> tuple[tuple[float, float] | None, geometry.BBox | None]:
    """The UK Met Office is the one Meteoalarm producer that draws its
    warnings: `area[].polygon` holds a LIST of CAP rings (`lat,lon` pairs,
    latitude first). Everyone else names an EMMA_ID and nothing else, and
    there is no public table from that code to a shape -- so no position,
    rather than a guessed one.

    Returns the centre (the average of the ring centres) and the extent
    (every vertex of every ring).
    """
    lats: list[float] = []
    lons: list[float] = []
    vertices: list[tuple[float, float]] = []
    for area in areas:
        rings = area.get("polygon")
        if isinstance(rings, str):  # CAP allows a single ring, not only a list
            rings = [rings]
        for ring in rings or []:
            points = polygon_points(ring)
            if points:
                lats.append(sum(p[0] for p in points) / len(points))
                lons.append(sum(p[1] for p in points) / len(points))
                vertices += points
    if not lats:
        return None, None
    return (sum(lats) / len(lats), sum(lons) / len(lons)), geometry.extent(vertices)


def _pick_info(blocks: list[dict]) -> dict | None:
    """A Meteoalarm alert carries the same content twice: local language and
    English. Without this choice, each warning produced two events."""
    if not blocks:
        return None
    for block in blocks:
        if (block.get("language") or "").lower().startswith("en"):
            return block
    return blocks[0]


def _label(value: str | None) -> str | None:
    """A short Meteoalarm label with its HTML entities decoded, once.

    Greece's feed escapes the ampersand inside the JSON string itself:
    `"East Sterea &amp; Evvoia"`, in `headline` and `areaDesc`, in both
    languages (12 fields on 2026-10-26). JSON needs no escaping, so this is
    the producer's own markup leaking into data, and it was printed as is.
    Measured across the ten country feeds, no other field and no other
    country carries an entity, so this is deliberately NOT applied to
    `description` or `instruction`: see `normalize_text` for why decoding a
    free-text emergency message is a risk this product does not take.
    """
    return html.unescape(value) if isinstance(value, str) else value


def meteoalarm_references(warning: dict) -> list[str]:
    """Identifiers a Meteoalarm message replaces. CAP `references` is a
    space-separated list of `sender,identifier,sent` triples, e.g.
    `https://meteo.hr,2.49.0.0.191.0.HR.261021110840_0_002_yellow_HR018,2026-10-21T11:08:40+02:00`."""
    found: list[str] = []
    for token in str((warning.get("alert") or {}).get("references") or "").split():
        parts = token.split(",")
        if len(parts) >= 2 and parts[1].strip():
            found.append(parts[1].strip())
    return found


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

    # `urgency` and `certainty` sit next to `severity` in every Meteoalarm
    # info block (2622/2622 on 2026-08-26) and were never read: an orange
    # warning the forecaster is only half sure of was ranked exactly like one
    # already dropping 180 mm.
    severity = severity_for_cap(
        AWARENESS_LEVEL_RANK.get(level or ""),
        urgency=info.get("urgency"),
        certainty=info.get("certainty"),
        # NOT passing `actionable` here, deliberately. The rule that demotes a
        # text-less alert was measured on the WMO aggregate, where the absence
        # of an instruction is a property of the ISSUER -- Kazakhstan 0/14,
        # India 17/17. Nothing was measured for Meteoalarm, where 98% of
        # warnings carry an instruction and the 2% that do not are far more
        # likely to be a sparse publisher than a statement about conditions.
        # Extending an unmeasured rule to a second population is exactly the
        # mistake lesson 24 is about.
    )
    kind = AWARENESS_TYPE_KIND.get(awareness_type or "", Kind.OTHER)

    # `AllClear` means the warning is LIFTED. Like the "no danger" tsunami
    # bulletins, it is displayed but does not alert.
    lifted = "AllClear" in (info.get("responseType") or [])
    # Already in the payload, at zero extra cost: measured 66% instruction,
    # 94% description, 100% responseType across the ten country feeds. This is
    # the actionable core of an alert -- what to DO -- and it was being thrown
    # away on every one of them.
    # "None" is a real CAP responseType and it means "no action recommended".
    # As a badge it would be noise on 331 of today's 425 warnings -- a label
    # that says nothing, on the one element meant to say what to do.
    # "AllClear" is handled separately: it lifts the alert.
    responses = [r for r in (info.get("responseType") or []) if r and r not in ("AllClear", "None")]
    if lifted:
        severity = Severity.INFO

    areas = info.get("area") or []
    place = ", ".join(_label(a.get("areaDesc")) or "" for a in areas[:3] if a.get("areaDesc"))
    position, extent = _meteoalarm_area(areas)
    lat, lon = position if position else (None, None)
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
        bbox=extent,
        place=place or country.replace("-", " ").title(),
        country=country.replace("-", " "),
        severity=severity,
        # a warning runs until its expiration: it is an ongoing alert
        ongoing=not lifted,
        # ...and THIS is its expiration. Meteoalarm keeps listing a warning
        # for days after it ends: on 2026-10-26, 241 of the 276 orange-and-red
        # warnings it served had already expired (median 30 h, worst 127 h --
        # German storm gusts of the 21st, still shown as ongoing on the 26th).
        # The feed re-lists them every cycle, so the silence sweep never fires;
        # the stated end is the only exit there is.
        expires=to_utc(info.get("expires")),
        alert=("lifted" if lifted else (params.get("awareness_level") or "").split(";")[-1].strip())
        or None,
        title=_label(info.get("headline")) or _label(info.get("event")) or place,
        instruction=info.get("instruction"),
        description=info.get("description"),
        response_type=responses[0] if responses else None,
        url=info.get("web"),
        raw={
            "event": _label(info.get("event")),
            "awareness_level": params.get("awareness_level"),
            "awareness_type": params.get("awareness_type"),
            "urgency": info.get("urgency"),
            "certainty": info.get("certainty"),
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
                last_error: Exception | None = None
                for country in self.countries:
                    try:
                        resp = await client.get(METEOALARM_API.format(country=country))
                        resp.raise_for_status()
                        alive += 1
                        kept = 0
                        for event in self.select(resp.json(), country):
                            kept += 1
                            await emit(event)
                        await self.flush_retractions()
                        seen += kept
                        self.health.feed_ok(country)
                        # mark health at EACH country: ten sequential GETs
                        # take over a minute, and the source looked dead for
                        # its entire first cycle
                        self.health.ok(kept)
                    except Exception as exc:
                        last_error = exc
                        # Per country, and never cleared by the next one: a
                        # dead Spanish feed used to vanish from the footer the
                        # moment Germany answered.
                        self.health.feed_failed(country, exc)
                        log.warning("meteoalarm %s: %s", country, exc)
                # health is already marked country by country above; marking it
                # here again added the same total a second time
                if not alive:
                    log.warning("meteoalarm: no country reachable this cycle")
                    if last_error is not None:
                        self.health.fail(last_error)
                await asyncio.sleep(self.poll_seconds)

    def select(self, data: Any, country: str, now: datetime | None = None) -> list[Event]:
        """The warnings of one country feed this source publishes.

        Below the threshold: not published. Already expired: not published
        either, and WITHDRAWN, so a warning stored before it ended leaves the
        map now rather than whenever something else notices.

        **A warning is replaced under a NEW identifier.** Meteoalarm does not
        update a warning in place: it publishes a message with its own
        identifier whose `references` names the one it replaces, and the
        replaced one is never listed again (1009 references on 2026-10-27,
        0 still listed; France 143 of 161 entries are such Updates, Italy
        120 of 120). Ignoring `references` meant a replaced warning lived on
        until its own `expires` or six hours of silence -- and a LIFT, which
        Meteo-France publishes as an AllClear at level 1 green, was dropped
        by the threshold before anyone read it, so the orange it lifted stayed
        on every client. Every reference is therefore honoured BEFORE the
        level filter: an AllClear withdraws what it references as `lifted`,
        any other replacement as `stale` (its successor is published below
        under its own identifier, at whatever level it now has).

        No stable-key remapping across the chain: the store key of a replaced
        warning is its own identifier, which is what survives a restart.
        """
        now = now or datetime.now(UTC)
        kept: list[Event] = []
        for warning in (data or {}).get("warnings") or []:
            event = parse_meteoalarm(warning, country)
            if event is None:
                continue
            lifts = event.alert == "lifted"
            for replaced in meteoalarm_references(warning):
                if replaced != event.source_id:
                    (self.lifts if lifts else self.expirations).append(f"meteoalarm:{replaced}")
            if _level_of(event) < self.min_level:
                continue
            if event.expires is not None and event.expires <= now:
                self.expirations.append(event.id)
                continue
            kept.append(event)
        return kept


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
WMO_SEVERITY = {1: "minor", 2: "moderate", 3: "severe", 4: "extreme"}

# Same reading, same sample: `u` 1=Past 2=Future 3=Expected 4=Immediate,
# `c` 2=Possible 3=Likely 4=Observed. Kept as text in `raw` because a bare
# rank is unreadable and was already being misread once.
WMO_URGENCY = {1: "past", 2: "future", 3: "expected", 4: "immediate"}
WMO_CERTAINTY = {1: "unlikely", 2: "possible", 3: "likely", 4: "observed"}

# `u` and `c` were decoded, written into `raw`, shown to nobody, and never
# allowed to touch the rank -- so a national "Extreme" was published as this
# product's EXTREME whatever the agency thought of its own forecast. Measured
# on the aggregate of 2026-08-26: 103 alerts at s=4, of which 75 are one
# member's (070, Kazhydromet) routine forest-fire-danger bulletins, and NOT ONE
# of the 103 claims `c=4` (Observed). They were the entire top of this
# product's feed. CAP itself warns that the ranks are the national agency's
# own, and this aggregate mixes 30-odd of them: `severity_for_cap` is where
# that gets read honestly, once, for every CAP source at the same time.

# CAP severity has four tiers; the settings express how many of them to keep,
# counting down from Extreme.
WMO_TIERS = 4

# Same matching rule as in `nws.py`, measured on the real feeds.
# Words in the languages the aggregate actually carries, read off its own
# `event` values (977 items, 2026-10-26): Spanish (Colombia, Mexico, Central
# America), French (West Africa, Canada), Indonesian. Every one is 4+
# letters, so the length floor makes them substrings -- "tormenta" also
# catches "Tormentas", "inundaci" catches "Inundación" and "inundaciones".
WMO_KIND_PATTERNS: list[tuple[tuple[str, ...], Kind]] = [
    (("tsunami",), Kind.TSUNAMI),
    (("volcano", "volcanic", "ash", "ashfall"), Kind.VOLCANO),
    # before the cyclone rule: the NWS marine warning for a NON-tropical low
    # (see nws.py), relayed here as "Hurricane Force Wind Warning"
    (("hurricane force",), Kind.STORM),
    (("cyclone", "hurricane", "typhoon", "tropical"), Kind.CYCLONE),
    (("flood", "flooding", "inundation", "crue", "inundaci"), Kind.FLOOD),
    # "Landslide warning" (Colombia, 15 live, 5 of them in the top tier) and
    # China's "Meteorological risk of geological disaster" -- the rain-driven
    # landslide and debris-flow warning. Both sat in OTHER.
    (("landslide", "geological disaster"), Kind.LANDSLIDE),
    (("avalanche",), Kind.AVALANCHE),
    (("fire", "wildfire", "bushfire"), Kind.WILDFIRE),
    (("earthquake", "seismic"), Kind.EARTHQUAKE),
    (("heat", "hot", "altas temperaturas"), Kind.HEAT),
    (("drought",), Kind.DROUGHT),
    (
        (
            "rain",
            "storm",
            "wind",
            "snow",
            "thunder",
            "gale",
            "blizzard",
            # Spanish
            "lluvia",
            "tormenta",
            "precipitaci",
            "nevada",
            "viento",
            # French
            "orage",
            "pluie",
            "tempête",
            # Indonesian: rain, lightning
            "hujan",
            "petir",
        ),
        Kind.STORM,
    ),
]


def classify_wmo(event_name: str) -> Kind:
    text = (event_name or "").lower()
    words = set(re.findall(r"[a-z]+", text))
    for patterns, kind in WMO_KIND_PATTERNS:
        if _matches(text, words, patterns):
            return kind
    return Kind.OTHER


# A headline that says nothing. Colombia's IDEAM fills `headline` with the
# literal "CAP Alert" on every message (50 of 977 items on 2026-10-26, 9 of
# the 38 top-tier ones), while `event` carries the real name: "Landslide
# warning", "High tide warning". It was the title of every one of them.
PLACEHOLDER_HEADLINES = frozenset({"cap alert"})


def _wmo_title(item: dict, event_name: str) -> str:
    headline = (item.get("headline") or "").strip()
    if not headline or headline.lower() in PLACEHOLDER_HEADLINES:
        return event_name
    return headline


def wmo_cap_path(item: dict) -> str | None:
    """Where the CAP document of this alert lives, relative to
    `severeweather.wmo.int/v2/cap-alerts/`.

    The aggregate names that path **`url` on 1247 items and `capURL` on the
    other 977**, and only `url` was ever read. So 44% of the alerts also lost
    their link back to the authoritative document, on top of their position.
    """
    path = item.get("url") or item.get("capURL")
    return str(path) if path else None


def parse_wmo(
    item: dict,
    position: tuple[float, float] | None = None,
    text: CapText | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> Event | None:
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

    urgency = WMO_URGENCY.get(rank(item.get("u")) or 0)
    certainty = WMO_CERTAINTY.get(rank(item.get("c")) or 0)
    severity = severity_for_cap(
        WMO_SEVERITY.get(rank(item.get("s")) or 0),
        urgency=urgency,
        certainty=certainty,
        # None until the CAP document has been fetched: not knowing is not the
        # same as knowing it says nothing.
        actionable=bool(text.instruction or text.description) if text else None,
    )

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
        bbox=bbox,
        place=place[:120] or event_name,
        country_code=country_code,
        severity=severity,
        ongoing=True,
        # The aggregate's `expires` IS the CAP document's, in UTC: checked on
        # ten issuers from UTC-5 to UTC+8 on 2026-10-26 (NWS, CMA, Chile,
        # MET Norway, BOM, MSC, DWD, InaTEWS, NCM), 10/10 to the second. It
        # was kept in `raw` and never mapped, so an alert outlived its own end
        # until six hours of silence noticed.
        expires=to_utc((item.get("expires") or "").replace(" ", "T")),
        alert=event_name.lower(),
        title=_wmo_title(item, event_name),
        # From the CAP document, which we have already downloaded for its
        # polygon: the same bytes, zero extra requests. This is what the
        # issuing agency told people to DO, and it was being discarded.
        instruction=text.instruction if text else None,
        description=text.description if text else None,
        response_type=text.response_type if text else None,
        # the aggregated JSON has shown times inconsistent with the source
        # CAP: for any critical time, the CAP is authoritative
        url=f"{WMO_CAP_BASE}{cap_path}" if cap_path else None,
        raw={
            "event": event_name,
            "expires": item.get("expires"),
            "urgency": urgency,
            "certainty": certainty,
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

    def build_events(self, items: list[dict], now: datetime | None = None) -> list[Event]:
        now = now or datetime.now(UTC)
        events = []
        for item in items:
            path = wmo_cap_path(item)
            event = parse_wmo(
                item,
                self.areas.known(path) if path else None,
                self.areas.text(path) if path else None,
                bbox=self.areas.extent(path) if path else None,
            )
            if event is None:
                continue
            if event.expires is not None and event.expires <= now:
                # The aggregate drops an alert at its expiry, but between two
                # downloads (a 304 re-emits the last list) it must not be put
                # back on the map past its end.
                self.expirations.append(event.id)
                continue
            events.append(event)
        return events

    def parse_payload(self, data: Any, now: datetime | None = None) -> list[Event]:
        return self.build_events(self.select(data), now=now)

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
                    await self.flush_retractions()
                    self.health.ok(fresh)
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("wmo: %s", exc)
                await asyncio.sleep(self.poll_seconds)
