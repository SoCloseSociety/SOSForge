"""FDSN `format=text` event services -- NRCan (Canada) and NOA (Greece).

Why a shared client. The brief asked whether one well-built FDSN client could
add several countries at once. Measured against the FDSN datacenter registry
(`https://www.fdsn.org/ws/datacenters/1/query`, 32 datacenters, 15 of which
publish an `fdsnws-event` service): the answer is **half yes**. The wire
format is shared, but every server disagrees about which representations and
which parameters it supports, so "one client, many countries" only works if
the client is built around what is actually common -- the pipe-separated text
table -- and not around the parts of the spec that turn out to be optional in
practice.

What the survey actually found, endpoint by endpoint:

- **NRCan (Canada)** answers `format=text` and rejects `format=json` AND
  `format=geojson` with a 422 (`{"errors":[{"value":"geojson","msg":"Invalid
  value","param":"format"}]}`). A previous audit recorded this service as a
  404; it is not, it was being asked for a representation it does not serve.
- **NOA (Greece)** answers `format=text` too, but only if `starttime` is
  present: without it, `?limit=5` alone is refused by Apache with a bare
  `400 Bad Request` before the service is ever reached. That failure looks
  exactly like a dead endpoint, which is very likely why it was written off.
- GEOFON is already in this product through its eqinfo GeoJSON precisely
  because its FDSN refuses `format=json`. INGV serves GeoJSON, so it stays on
  the JSON path. Both confirm the same lesson.

So this module speaks the one dialect all of them agree on, and it parses the
table **by its header** rather than by column position. That is not
defensive-programming theatre:

    NRCan  #EventID|Time|Latitude|Longitude|Depth/km|MagType|Magnitude|EventLocationName
    NOA    #EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|
           ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType

Eight columns against fourteen, for the same "standard" format. A parser keyed
on index would read NRCan's magnitude out of its MagType column.

Traps verified against the real payloads (captured 2026-08-26):

- **Both feeds are UTC, and only one of them says so.** NRCan stamps a `Z`;
  NOA publishes `2026-08-26T10:01:58.141569` with no offset at all, which is
  the shape that silently costs six hours elsewhere in this product. Proven,
  not assumed: matching every event of both feeds against USGS + EMSC at
  every offset from -8 h to +8 h peaks at **exactly 0 h** for both (NOA 16
  matches at 0 h, 1 at every other offset tried; NRCan 3 at 0 h, 0 elsewhere).
  Greece is UTC+3 in summer, so a local-time feed would have peaked at -3.
- **NRCan is a reviewed catalogue on a delay, NOA is live.** NOA's newest
  event was 55 minutes old when this was written; NRCan's was 19.6 h old,
  longer than the largest gap between events in the whole preceding month.
  That is a publication lag, not a quiet day, and it decides the window size
  each source needs. See `NrcanSource` for the measurement.
- **NOA truncates with HTTP 200.** Ask for 60 days and it returns 112 rows and
  then appends `Error 413: Request Entity Too Large ... exceeds the configured
  maximum number of objects (5000)` to the body. `raise_for_status()` never
  fires. A client that did not look would ingest a silently partial list
  forever and report itself healthy -- the exact failure this product exists
  not to have. We detect the marker and fail the source instead.
- **NRCan's `EventLocationName` is bilingual**, English and French joined by a
  single `/`: `253 km SSW of Port Hardy, BC/253 km SSO de Port Hardy, BC`.
  Measured over 309 events: every single one has exactly one slash.
- **NRCan relays events it did not cause.** Over 30 days, 12 events sit in
  `AK`, `WA` and `OH` -- Alaska, Washington, Ohio. Stamping the feed's home
  country would have put a Canadian flag on an earthquake near Cleveland
  (lesson 16, the JMA distant-quake trap, in a second feed). We stamp Canada
  only on a Canadian province code and let the pipeline resolve the rest --
  which it does, `resolve("138 km NW of Juneau, AK")` returns `US`.
- **NRCan publishes non-tectonic events in the same table**, and its 8-column
  header has no `EventType` to say so: the type is only in the location text.
  Over 30 days, 49 of 309 rows (16%) are `Blast`, `Suspected blast` or
  `Suspected industry-related event`. A quarry blast is not an earthquake, so
  it does not go out as one. But an induced M3.6 near Fox Creek genuinely
  shakes houses, so it is not dropped either: explosions become `OTHER`,
  induced seismicity stays an earthquake and carries a badge.
- **NRCan magnitudes go negative** (`-0.07`, 12 km SW of La Malbaie, QC) and
  `MagType` carries values like `Mw'`, `MLy`, `ML1`. Both are real.
- **NOA publishes eleven significant digits** of magnitude and depth
  (`1.008569645`, `8.346537272`). No magnitude is meaningful past one decimal;
  shipping the raw string would have made the UI look like it was inventing
  precision.
- **NOA's catalogue is not only Greek**: `Albania`, `Turkey`,
  `Greece-Albania Border Region`, `NW Balkan Region`. Same discipline as
  above -- and a border region is stamped by nobody, because the epicentre
  can honestly be on either side of it.

Coverage measured on 2026-08-26 (same window, same box, matched with this
product's own dedup thresholds of 90 s and 250 km):

- **NRCan**, 30 days over Canada: 309 events, of which **226 (73%) are in
  neither USGS nor EMSC**, including an M4.43 offshore Port Hardy that no
  global catalogue carries. Excluding blasts and induced events entirely, it
  is still 179 of 260 (69%).
- **NOA**, 3.5 days over the Greek box: 214 events, of which **198 (93%) are
  in neither USGS nor EMSC**. The overlap is low for a real reason: what EMSC
  does carry in that box is overwhelmingly western Turkiye relayed from the
  Turkish networks, while mainland Greek seismicity from M0.2 to M3.5 is
  simply not in the global catalogues.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.models.event import Event, Kind, severity_from_magnitude, to_utc
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"

# NOA appends this to a 200 response when the result set is too large. The
# number in the message is the server's object budget, not a row count, so we
# match on the stable prefix only.
TRUNCATION_MARKER = "Error 413"


class FdsnTruncated(RuntimeError):
    """The server answered 200 but said, inside the body, that it gave up.

    Its own class so the run loop cannot confuse it with a parse error: a
    truncated batch is a source that must go RED, not a batch to salvage.
    """


def parse_fdsn_text(text: str) -> list[dict[str, str]]:
    """Rows of an FDSN `format=text` table, keyed by the header names.

    Keyed by header, never by position: NRCan serves 8 columns and NOA 14 for
    the same declared format (see the module docstring).
    """
    if TRUNCATION_MARKER in text:
        raise FdsnTruncated("the service truncated the result set and said so in a 200 body")

    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            # Only the first comment line is the header; anything else is a
            # remark and must not silently redefine the columns.
            if header is None:
                header = [name.strip() for name in line.lstrip("#").split("|")]
            continue
        if header is None:
            continue
        cells = line.split("|")
        if len(cells) != len(header):
            # A row that does not match the header cannot be read safely, and
            # guessing which column slipped is how a magnitude ends up in the
            # depth field.
            log.warning("fdsn: row with %d cells for %d columns, skipped", len(cells), len(header))
            continue
        rows.append(dict(zip(header, cells, strict=True)))
    return rows


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def _rounded(value: float | None, digits: int = 1) -> float | None:
    """No magnitude and no depth is meaningful past one decimal. NOA publishes
    eleven digits of both; passing them on would be inventing precision."""
    return None if value is None else round(value, digits)


class FdsnTextSource(Source):
    """Poll an FDSN event service in `format=text`, normalize, emit.

    The window is expressed in HOURS and re-computed at every cycle. A fixed
    `limit` is deliberately not used: NOA refuses the request outright without
    a `starttime`, and NRCan's `limit` would silently hide the newest events
    the day the feed gets busy.
    """

    base_url: str = ""
    # Wide enough to survive a restart and a slow catch-up, small enough to
    # stay far under NOA's truncation point (measured: it starts truncating
    # somewhere between 950 and 1185 rows; 24 h of NOA is about 250).
    window_hours: float = 24.0
    # Extra query parameters this service needs or tolerates.
    extra_params: dict[str, str] = {}

    def __init__(
        self,
        poll_seconds: float = 60.0,
        url: str | None = None,
        window_hours: float | None = None,
    ):
        super().__init__()
        self.poll_seconds = poll_seconds
        if url:
            self.base_url = url
        if window_hours is not None:
            self.window_hours = window_hours

    def build_url(self) -> str:
        start = datetime.now(UTC) - timedelta(hours=self.window_hours)
        params = {
            "format": "text",
            # Seconds resolution and no offset: what both services document,
            # and both read it as UTC.
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "orderby": "time",
            **self.extra_params,
        }
        return f"{self.base_url}?{urlencode(params)}"

    def build_event(self, row: dict[str, str]) -> Event | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse_payload(self, text: str) -> list[Event]:
        events: list[Event] = []
        for row in parse_fdsn_text(text):
            try:
                event = self.build_event(row)
            except Exception as exc:
                # One malformed row must not cost us the rest of the batch.
                log.warning("%s: unreadable row (%s)", self.name, exc)
                continue
            if event is not None:
                events.append(event)
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/plain"}
        # follow_redirects: NRCan 301s from its bare host to the `www.` one.
        # We point at `www.` directly, but a future move must not go dark.
        async with httpx.AsyncClient(
            timeout=45.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    resp = await client.get(self.build_url())
                    resp.raise_for_status()
                    events = self.parse_payload(resp.text)
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)


def _common_fields(row: dict[str, str]) -> tuple[str, datetime, float | None, float | None] | None:
    """The four things without which an FDSN row is not an event."""
    event_id = (row.get("EventID") or "").strip()
    if not event_id:
        return None
    time = to_utc(row.get("Time"))
    if time is None:
        return None

    lat, lon = _number(row.get("Latitude")), _number(row.get("Longitude"))
    # Lesson 15: a wrong position is far worse than a missing one, and the
    # model now RAISES on an out-of-range coordinate -- unguarded, one bad row
    # would take the whole batch down with it.
    if lat is not None and not -90 <= lat <= 90:
        lat = None
    if lon is not None and not -180 <= lon <= 180:
        lon = None
    return event_id, time, lat, lon


# --------------------------------------------------------------------- NRCan

# The ten provinces and three territories, as NRCan abbreviates them. None of
# these collides with a US postal code, which is what makes the test safe: the
# same feed carries AK, WA and OH events, and those must NOT become Canadian.
# An entry missing from this table fails safe -- the event simply gets no
# country here and the pipeline resolves it from the place text -- so the cost
# of an omission is a missing flag, never a wrong one.
CANADIAN_PROVINCES = frozenset(
    {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
)

# Prefixes NRCan puts in front of the location text to say the event is not
# tectonic. Its 8-column table has no EventType column, so this text is the
# ONLY place the information exists. Measured over 30 days: 49 of 309 rows.
NRCAN_EXPLOSION_PREFIXES = ("blast", "suspected blast")
NRCAN_INDUCED_PREFIXES = ("suspected industry-related event",)

# NRCan marks a felt event by appending ", felt" (French: ", ressenti").
RE_NRCAN_FELT = re.compile(r",\s*felt\s*$", re.IGNORECASE)

# There is no per-event permalink. `index-en.php?tpl_region=canada&id=<id>`
# answers 200 and looks like one, but the id is echoed into exactly one place
# -- the French language-switch href -- and the page rendered is the generic
# one. Shipping it would be the dead SSN permalink all over again, so we link
# to the recent-earthquakes page, which is real (verified 200).
NRCAN_PAGE_URL = "https://www.earthquakescanada.nrcan.gc.ca/recent/index-en.php"


class NrcanSource(FdsnTextSource):
    """Natural Resources Canada -- the national Canadian catalogue.

    Why it exists here. Canada had no national coverage in this product, and
    the global catalogues are thin over it: measured on 2026-08-26 over 30
    days on the Canadian box, **226 of NRCan's 309 events (73%) are in neither
    USGS nor EMSC**, including an M4.43 offshore Port Hardy. Cascadia is not a
    hypothetical, the St. Lawrence valley is a populated seismic zone, and the
    induced seismicity of the Alberta/BC gas fields is felt by people who
    currently see nothing at all.

    **This one is a reviewed catalogue, not a live feed, and the window is
    sized for that.** Measured on 2026-08-26 at 10:56 UTC: the newest
    published event was 19.6 h old, while the median gap between consecutive
    events over the previous 30 days was 1.57 h and the largest gap in the
    whole month was 14.3 h. A silence longer than any real gap in the month is
    not a quiet day, it is a publication delay -- confirmed by the service
    answering `204 No Content` for everything after 00:00 that day. NRCan
    publishes analyst-reviewed solutions and there is no faster free feed:
    `/api/fdsnws/event/1/query` is the same service behind a second path and
    returns byte-identical rows.

    The consequence that matters: the window filters on EVENT time while the
    lag delays PUBLICATION, so a window near the size of the lag would only
    ever show the thin band between the two. At 24 h this source would have
    displayed about four hours' worth of events and silently dropped the rest.
    72 h leaves two and a half days of margin over the measured lag, costs
    about 31 rows a cycle, and matches the ingestion horizon that would discard
    anything older anyway.

    Nothing here lies about freshness: every event carries its true origin
    time, so the UI ages them honestly and none of them is ever "breaking".
    """

    name = "nrcan"
    kind = "poll"
    # The `www.` host on purpose: the bare host 301s on every single request.
    base_url = "https://www.earthquakescanada.nrcan.gc.ca/fdsnws/event/1/query"
    window_hours = 72.0

    def build_event(self, row: dict[str, str]) -> Event | None:
        common = _common_fields(row)
        if common is None:
            return None
        event_id, time, lat, lon = common

        # Bilingual label: English before the single slash, French after.
        raw_place = (row.get("EventLocationName") or "").strip()
        place = raw_place.split("/", 1)[0].strip()

        felt = bool(RE_NRCAN_FELT.search(place))
        if felt:
            place = RE_NRCAN_FELT.sub("", place).strip()

        lowered = place.lower()
        explosion = lowered.startswith(NRCAN_EXPLOSION_PREFIXES)
        induced = lowered.startswith(NRCAN_INDUCED_PREFIXES)

        magnitude = _rounded(_number(row.get("Magnitude")))
        # An explosion is not an earthquake. It is not dropped either -- a
        # blast near Halifax that people felt is a thing they will look for --
        # but it does not go out wearing the wrong kind. Induced seismicity is
        # real ground shaking measured on a magnitude scale, so it stays an
        # earthquake and says so in a badge instead.
        event_kind = Kind.OTHER if explosion else Kind.EARTHQUAKE

        badge = "felt" if felt else ("induced" if induced else None)
        return Event(
            id=f"nrcan:{event_id}",
            source="nrcan",
            source_id=event_id,
            kind=event_kind,
            time=time,
            lat=lat,
            lon=lon,
            depth_km=_rounded(_number(row.get("Depth/km"))),
            magnitude=magnitude,
            mag_type=(row.get("MagType") or "").strip() or None,
            place=place,
            country=_canadian_country(place),
            severity=severity_from_magnitude(magnitude),
            alert=badge,
            title=f"M {magnitude} -- {place}" if magnitude is not None else place,
            url=NRCAN_PAGE_URL,
            raw={
                "location_bilingual": raw_place,
                "felt": felt,
                "explosion": explosion,
                "induced": induced,
            },
        )


def _canadian_country(place: str) -> str | None:
    """Canada only when the label ends in a Canadian province code.

    NRCan relays US events (AK, WA, OH over the measured month). Those are
    left to the pipeline, which reads them correctly from the same text.
    """
    tail = place.rsplit(",", 1)[-1].strip().upper()
    return "Canada" if tail in CANADIAN_PROVINCES else None


# ----------------------------------------------------------------------- NOA


# Same story as NRCan: NOA publishes no per-event page under a guessable
# path (`/HL/database/event-info/<id>` answers 404), so we link to the
# real-time catalogue page, which is real (verified 200).
NOA_PAGE_URL = "https://bbnet.gein.noa.gr/HL/seismicity/catalogues/real-time-catalogue"


class NoaSource(FdsnTextSource):
    """National Observatory of Athens -- the Greek national catalogue.

    Why it exists here. Greece is the most seismically active country in
    Europe and had no national source in this product. Measured on 2026-08-26
    over 3.5 days on the Greek box, **198 of NOA's 214 events (93%) are in
    neither USGS nor EMSC**: the global catalogues do carry that box, but what
    they carry is western Turkiye relayed from the Turkish networks, while
    mainland Greek seismicity from M0.2 to M3.5 is not in them at all.

    `starttime` is not optional here. Without it the request never reaches the
    service: Apache answers a bare `400 Bad Request`, which reads exactly like
    a dead endpoint.
    """

    name = "noa"
    kind = "poll"
    base_url = "https://eida.gein.noa.gr/fdsnws/event/1/query"

    def build_event(self, row: dict[str, str]) -> Event | None:
        common = _common_fields(row)
        if common is None:
            return None
        event_id, time, lat, lon = common

        place = (row.get("EventLocationName") or "").strip() or "Greece"
        magnitude = _rounded(_number(row.get("Magnitude")))
        event_type = (row.get("EventType") or "").strip().lower()

        return Event(
            id=f"noa:{event_id}",
            source="noa",
            source_id=event_id,
            # NOA fills EventType, and everything measured over 60 days was
            # `earthquake`. Anything else is not asserted to be one.
            kind=Kind.EARTHQUAKE if event_type in ("", "earthquake") else Kind.OTHER,
            time=time,
            lat=lat,
            lon=lon,
            depth_km=_rounded(_number(row.get("Depth/km"))),
            magnitude=magnitude,
            mag_type=(row.get("MagType") or "").strip() or None,
            place=place,
            country=_greek_country(place),
            severity=severity_from_magnitude(magnitude),
            title=f"M {magnitude} -- {place}" if magnitude is not None else place,
            url=NOA_PAGE_URL,
            raw={"analyst": (row.get("Author") or "").strip() or None, "event_type": event_type},
        )


def _greek_country(place: str) -> str | None:
    """Greece when the label names Greece and nothing else.

    `Greece`, `Southern Greece`, `Crete, Greece` and `Dodecanese Islands,
    Greece` are Greece. `Greece-Albania Border Region` is nobody's: the
    epicentre is honestly on either side of it, and lesson 12 says say nothing
    rather than something wrong. `Albania`, `Turkey` and the open seas fall
    through to the pipeline, which already reads those correctly.
    """
    lowered = place.lower()
    if "greece" in lowered and "border" not in lowered:
        return "Greece"
    return None
