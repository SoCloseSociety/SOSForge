"""Read the two numbers a coastal reader actually wants out of a tsunami
bulletin: when the wave gets here, and whether a gauge has seen one.

`tsunami.py` reads the Atom feed, whose `summary` carries the category, the
magnitude and the epicentre. It carries nothing else. Everything below lives
only in the plain-text bulletin the Atom entry links to
(`link rel="alternate"`, `.../WEPA40.txt`) -- not in the CAP document, and not
in the `<TWCID>.json` the CAP `<resource>` advertises. Both of those were
checked against the real files: the CAP has `<instruction>` and nothing
positional, and the JSON has the alert zones but no arrival time and no gauge
reading. So the text bulletin it is.

**The two centres do not write the same bulletin.** This is not a stylistic
difference, it changes what can be trusted:

*PTWC (PHEB)*, verbatim from the 2026-07-28 Kyushu M7.1 threat message:

    ESTIMATED TIMES OF ARRIVAL
    --------------------------
        LOCATION         REGION             COORDINATES    ETA(UTC)
        ------------------------------------------------------------
        NAGASAKI         JAPAN             32.7N 129.7E   0754 07/28

and from the 2025-07-30 Kamchatka M8.8 messages:

    TSUNAMI OBSERVATIONS
    --------------------
        GAUGE LOCATION        LAT   LON     (UTC)     HEIGHT    (MIN)
        -------------------------------------------------------------
        CORONEL CL           37.0S  73.2W    0129   1.03M/ 3.4FT  38

PTWC stamps **UTC** and prints metres. Nothing has to be assumed.

*NTWC (PAAQ)*, verbatim from the same event:

    FORECASTS OF TSUNAMI ACTIVITY
    -----------------------------
    Shemya           1646 AKDT Jul 29

    OBSERVATIONS OF TSUNAMI ACTIVITY - UPDATED
    ------------------------------------------
     Amchitka  Alaska             0056  PDT Jul 30           1.7ft
     Saint Paul  Alaska                                      0.4ft

NTWC stamps a **local** time as a bare abbreviation and prints feet. The
abbreviation is resolved from a closed table of North American and Pacific
zones with fixed offsets (the abbreviation itself encodes summer time, so
there is no DST ambiguity to guess at) and anything outside that table is
REFUSED, not approximated. A tsunami arrival time in the wrong hour is worse
than no arrival time, which is lesson 15 applied to a clock.

**Why the rows are scoped to their section instead of being matched
anywhere.** The earthquake parameters block of an NTWC bulletin contains

     * Origin Time    1525 AKDT Jul 29 2025

which has exactly the shape of an arrival row. Matched loose, that line
becomes "first wave reaches * Origin Time at 15:25" -- a plausible-looking
sentence built out of the earthquake's own origin time. A section header here
is a line at column zero, in capitals, underlined by an unbroken run of
dashes; the column headers inside the tables are indented, or underlined by a
BROKEN run (`----             ----------`), so neither is mistaken for one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

FEET_TO_METRES = 0.3048

# Sanity bounds. The largest tsunami ever measured on a gauge is a few tens of
# metres; anything past this is a misread column, not a wave.
MAX_WAVE_M = 50.0
# An arrival time more than a week from the bulletin is a year inference gone
# wrong, not a forecast: a tsunami crosses the whole Pacific in about a day.
MAX_ETA_DRIFT = timedelta(days=7)

# Time zones NTWC stamps its bulletins with. Fixed offsets, because the
# abbreviation already tells us whether it is summer: AKDT and AKST are two
# different entries, not one zone plus a rule.
#
# AST is Atlantic (-4), which is what NTWC means by it in its Puerto Rico and
# Virgin Islands products. The same three letters mean Arabian (+3) elsewhere
# in the world; this table is scoped to the two US centres and is not a
# general-purpose zone database, which is exactly why it is a closed table.
TZ_OFFSETS: dict[str, int] = {
    "UTC": 0,
    "GMT": 0,
    "AST": -4,
    "ADT": -3,
    "EST": -5,
    "EDT": -4,
    "CST": -6,
    "CDT": -5,
    "MST": -7,
    "MDT": -6,
    "PST": -8,
    "PDT": -7,
    "AKST": -9,
    "AKDT": -8,
    "HST": -10,
    "HDT": -9,
    "SST": -11,
    "CHST": 10,
    "GST": 10,
}

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# A section header: column zero, capitals, underlined by an UNBROKEN dash run.
RE_UNDERLINE = re.compile(r"^-{3,}$")
RE_ETA_SECTION = re.compile(r"ESTIMATED TIMES? OF ARRIVAL|FORECASTS? OF TSUNAMI ACTIVITY")
RE_OBS_SECTION = re.compile(r"TSUNAMI OBSERVATIONS|OBSERVATIONS OF TSUNAMI ACTIVITY")

# PTWC arrival row: name, region, coordinates, `HHMM MM/DD` in UTC.
RE_PTWC_ETA = re.compile(
    r"^\s*(?P<name>\S.*?)\s{2,}"
    r"\d+(?:\.\d+)?[NS]\s+\d+(?:\.\d+)?[EW]\s+"
    r"(?P<hhmm>\d{4})\s+(?P<month>\d{2})/(?P<day>\d{2})\s*$"
)

# PTWC gauge row: the height is printed twice, `1.03M/ 3.4FT`. We read the
# metres, which is the unit the centre measured in.
RE_PTWC_OBS = re.compile(
    r"^\s*(?P<name>\S.*?)\s{2,}"
    r"\d+(?:\.\d+)?[NS]\s+\d+(?:\.\d+)?[EW]\s+"
    r"\d{4}\s+(?P<metres>\d+(?:\.\d+)?)\s*M\s*/\s*\d+(?:\.\d+)?\s*FT"
)

# NTWC arrival row: name, then `HHMM TZ Mon DD` with no year.
RE_NTWC_ETA = re.compile(
    r"^\s*(?P<name>\S.*?)\s{2,}"
    r"(?P<hhmm>\d{4})\s+(?P<tz>[A-Z]{3,4})\s+(?P<month>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s*$"
)

# NTWC gauge row: name, an OPTIONAL `HHMM TZ Mon DD`, then feet. Saint Paul
# reported 0.4ft with no time at all, and dropping that row would be dropping
# an observation because its clock was missing.
RE_NTWC_OBS = re.compile(
    r"^\s*(?P<name>\S.*?)\s{2,}"
    r"(?:(?P<hhmm>\d{4})\s+(?P<tz>[A-Z]{3,4})\s+(?P<month>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+)?"
    r"(?P<feet>\d+(?:\.\d+)?)\s*ft\s*$",
    re.I,
)


@dataclass(frozen=True)
class Arrival:
    """One forecast arrival time at one named place."""

    site: str
    at: datetime


@dataclass(frozen=True)
class Observation:
    """One gauge reading: the maximum height above the normal tide level."""

    site: str
    metres: float
    at: datetime | None = None


@dataclass
class Bulletin:
    """What a bulletin says, beyond what the Atom summary already carried."""

    arrivals: list[Arrival] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)

    @property
    def first_arrival(self) -> Arrival | None:
        """The earliest forecast arrival in the bulletin.

        The earliest one is the coast the wave reaches first, which is the
        urgent end of the table. It is deliberately NOT filtered to the
        future: a bulletin reissued three hours in says the first wave
        arrived at 07:54, and that is still the true answer to "when did this
        start", where "no arrival time" would read as "nothing is coming".
        """
        return min(self.arrivals, key=lambda a: a.at) if self.arrivals else None

    @property
    def largest(self) -> Observation | None:
        """The biggest thing any gauge has actually recorded."""
        return max(self.observations, key=lambda o: o.metres) if self.observations else None

    def __bool__(self) -> bool:
        return bool(self.arrivals or self.observations)


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """(header, body lines) for every underlined all-caps heading.

    Tabs are expanded first: the same product goes out both space-aligned and
    tab-aligned, and every column rule below is written in spaces.
    """
    lines = text.expandtabs(8).splitlines()
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        is_header = (
            bool(stripped)
            and line[:1] not in (" ", "\t")
            and stripped == stripped.upper()
            and index + 1 < len(lines)
            and bool(RE_UNDERLINE.match(lines[index + 1].strip()))
        )
        if is_header:
            current = (stripped, [])
            sections.append(current)
            index += 2
            continue
        if current is not None:
            current[1].append(line)
        index += 1
    return sections


def _resolve_year(
    month: int, day: int, hour: int, minute: int, issued: datetime
) -> datetime | None:
    """A `MM/DD` or `Mon DD` with no year, anchored on the bulletin's own date.

    A bulletin issued on 31 December can forecast an arrival on 01 January, so
    the neighbouring years are tried and the closest to the issue time wins.
    Anything still more than a week away is refused: that is a parse failure
    wearing a plausible date.
    """
    best: datetime | None = None
    for year in (issued.year - 1, issued.year, issued.year + 1):
        try:
            candidate = datetime(year, month, day, hour, minute, tzinfo=UTC)
        except ValueError:
            continue
        if best is None or abs(candidate - issued) < abs(best - issued):
            best = candidate
    if best is None or abs(best - issued) > MAX_ETA_DRIFT:
        return None
    return best


def _local_to_utc(hhmm: str, tz: str, month: str, day: str, issued: datetime) -> datetime | None:
    """`1646 AKDT Jul 29` -> an aware UTC datetime, or nothing.

    An unknown abbreviation returns None. It does NOT fall back to UTC: a
    silent eight-hour error on a tsunami arrival is the exact shape of the
    mistake this product refuses to make.
    """
    offset = TZ_OFFSETS.get(tz.upper())
    number = MONTHS.get(month[:3].upper())
    if offset is None or number is None:
        log.debug("tsunami bulletin: unusable local stamp %r %r", tz, month)
        return None
    local = _resolve_year(
        number, int(day), int(hhmm[:2]), int(hhmm[2:]), issued + timedelta(hours=offset)
    )
    if local is None:
        return None
    return local - timedelta(hours=offset)


def _clean_site(name: str) -> str:
    """`NAGASAKI         JAPAN` -> `NAGASAKI, JAPAN`.

    The columns are separated by runs of spaces; a place and its region are
    two columns, and glueing them with a comma keeps both without pretending
    the whitespace was meaningful.
    """
    parts = [p.strip(" .") for p in re.split(r"\s{2,}", name.strip()) if p.strip(" .")]
    return ", ".join(parts)[:80]


def _arrival_rows(body: list[str], issued: datetime) -> list[Arrival]:
    arrivals: list[Arrival] = []
    for line in body:
        match = RE_PTWC_ETA.match(line)
        if match:
            at = _resolve_year(
                int(match["month"]),
                int(match["day"]),
                int(match["hhmm"][:2]),
                int(match["hhmm"][2:]),
                issued,
            )
            if at:
                arrivals.append(Arrival(_clean_site(match["name"]), at))
            continue
        match = RE_NTWC_ETA.match(line)
        if match:
            at = _local_to_utc(match["hhmm"], match["tz"], match["month"], match["day"], issued)
            if at:
                arrivals.append(Arrival(_clean_site(match["name"]), at))
    return arrivals


def _observation_rows(body: list[str], issued: datetime) -> list[Observation]:
    observations: list[Observation] = []
    for line in body:
        match = RE_PTWC_OBS.match(line)
        if match:
            metres = float(match["metres"])
            if 0 < metres <= MAX_WAVE_M:
                observations.append(Observation(_clean_site(match["name"]), metres))
            continue
        match = RE_NTWC_OBS.match(line)
        if match:
            metres = round(float(match["feet"]) * FEET_TO_METRES, 2)
            if not 0 < metres <= MAX_WAVE_M:
                continue
            at = None
            if match["hhmm"] and match["tz"] and match["month"] and match["day"]:
                at = _local_to_utc(match["hhmm"], match["tz"], match["month"], match["day"], issued)
            observations.append(Observation(_clean_site(match["name"]), metres, at))
    return observations


def parse_bulletin(text: str, issued: datetime) -> Bulletin:
    """Arrivals and gauge readings out of one plain-text bulletin.

    `issued` is the bulletin's own issue time, needed because neither centre
    prints a year in its tables. It comes from the Atom entry's `updated`,
    which `tsunami.py` already parses.

    Never raises. A bulletin with no such tables -- which is every
    "Information" statement, the overwhelming majority of what these feeds
    publish -- returns an empty `Bulletin`, and an empty `Bulletin` is falsy.
    """
    if not text:
        return Bulletin()
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=UTC)

    bulletin = Bulletin()
    for header, body in _sections(text):
        if RE_ETA_SECTION.search(header):
            bulletin.arrivals.extend(_arrival_rows(body, issued))
        elif RE_OBS_SECTION.search(header):
            bulletin.observations.extend(_observation_rows(body, issued))
    return bulletin


# A real bulletin is about four kilobytes. This bound is not about them: it is
# about not streaming an unbounded body into memory because a mirror, a proxy
# or a bad day handed us something else at that URL.
MAX_BULLETIN_BYTES = 200_000

# One entry per bulletin ever seen. A centre issues a few a week outside a
# crisis and a few dozen an hour inside one, so this holds months.
CACHE_MAX = 512


class BulletinCache:
    """Parsed bulletins by URL.

    A bulletin document is immutable: its path carries the centre, the date,
    the event id, the message number and the WMO header
    (`/events/PHEB/2026/07/28/26209004/1/WEPA40/WEPA40.txt`), so a new
    message is a new URL and a URL never changes content. Fetching one twice
    is therefore pure waste, and the Atom feed republishes the same entry on
    every poll -- every 30 seconds, forever, for as long as it is the latest
    bulletin.

    An empty result is cached like any other. "This bulletin has no arrival
    table" is a real, permanent answer, and it is the answer for nearly every
    bulletin these feeds publish: an Information statement says "there is no
    tsunami danger" and prints no table at all.
    """

    def __init__(self, max_entries: int = CACHE_MAX):
        self.max_entries = max_entries
        self._cache: dict[str, Bulletin] = {}

    def known(self, url: str) -> Bulletin | None:
        return self._cache.get(url)

    async def get(self, client, url: str, issued: datetime) -> Bulletin:
        """The parsed bulletin at `url`, fetching it once.

        A failed fetch returns an empty `Bulletin` and is NOT cached: the
        arrival time is worth asking for again on the next cycle, and unlike
        the content, a network failure is not a permanent answer.
        """
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.content[:MAX_BULLETIN_BYTES].decode("utf-8", "replace")
        except Exception as exc:
            log.warning("tsunami bulletin %s: %s", url, exc)
            return Bulletin()
        bulletin = parse_bulletin(text, issued)
        if len(self._cache) >= self.max_entries:
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[url] = bulletin
        return bulletin
