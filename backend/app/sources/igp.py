"""IGP -- Instituto Geofisico del Peru.

Why this source exists. Peru had no national coverage in this product, and it
is one of the worst places on earth to be missing it: the Nazca plate is
subducting under eleven million people in Lima, and the last time it let go
under the capital, in 1746, the city was destroyed and Callao was swept away
by the tsunami. The global catalogues do not see the everyday seismicity that
tells people the fault is working.

Measured on 2026-08-26 over 30 days on the Peruvian box, matched with this
product's own dedup thresholds (90 s, 250 km): the IGP published **93 events,
of which 72 (77%) are in neither USGS nor EMSC**. Over the shorter 3.5-day
window that the ingestion horizon actually covers, **USGS returned zero events
for the whole country** and 9 of the IGP's 10 existed in no global catalogue.
The biggest thing found nowhere else in the month was an M4.4 183 km off
Barranca, Lima.

Traps verified against the real payload (captured 2026-08-26, 591 events):

- **The date and the time are in separate fields, and the time field carries a
  fake date.** `hora_utc` is `"1970-01-01T10:02:31.000Z"`: the epoch date is
  padding, only the clock part means anything. The instant is
  `fecha_utc`'s date joined to `hora_utc`'s time. Parsing `hora_utc` as a
  timestamp puts every Peruvian earthquake in 1970; parsing `fecha_utc` alone
  puts it at midnight.
- **There are two such pairs, local and UTC**, and they really do differ:
  `fecha_local`/`hora_local` are UTC-5. Checked on all 591 events, the offset
  is exactly 5 h every single time, and on 147 of them the two DATES differ --
  so picking `fecha_local` with `hora_utc` would have been right 75% of the
  time and silently a day out for the rest. We read the UTC pair only.
- **A year with no earthquakes yet answers 404.** `ajaxb/2027` returns
  `{"error":"No se encontraron sismos para el ano proporcionado."}` with HTTP
  404. The feed is addressed BY YEAR, so on 1 January this source would have
  gone red until Peru's first earthquake of the year. An empty year is not a
  failure, and around the turn of the year we ask for both.
- **The list is the whole year, oldest first.** 591 events and 410 KB in
  August, and the newest event is the LAST element, not the first. A source
  that emitted the head would have been permanently eight months stale.
- **`latitud`, `longitud` and `magnitud` are strings**, `profundidad` is a
  number, and `tipomagnitud` is empty on all 591 rows.
- **`intensidad` is a Modified Mercalli value glued to a place name**
  ("II-III Chupaca", "III San Juan"), present on 491 of 591 events. It is the
  only field in this feed that says what people actually felt, which is the
  question magnitude does not answer.
- **The per-event PDF link does not load.** `reporte_acelerometrico_pdf`
  points at `www.igp.gob.pe/servicios/api-acelerometrica/...`, which times out
  (curl exit 28) while the same host's root answers 200. We keep the path in
  `raw` and ship the catalogue page, which is real -- the same discipline the
  SSN's dead permalink earned.
- The reference text is Spanish and `app.countries.resolve` cannot read it
  (measured: "17 km al S de Chupaca, Chupaca - Junin" returns None). The
  region tail names one of Peru's 24 departments or Callao on all 591 events,
  so that is what we stamp Peru on -- and anything else falls through to the
  pipeline, exactly as the SSN's Mexican-state table does.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.models.event import Event, Kind, severity_for_quake
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

BASE_URL = "https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb"
USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"
# The SPA that publishes this data. There is no per-event page and the
# per-event PDF does not load, so this is the honest link.
PAGE_URL = "https://ultimosismo.igp.gob.pe/ultimo-sismo/sismos-reportados"

# "II-III Chupaca" -> the Mercalli part, range or single value.
RE_INTENSITY = re.compile(r"^\s*([IVX]+)(?:\s*-\s*([IVX]+))?")
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}

# Peru's 24 departments plus the Constitutional Province of Callao, normalized
# (lowercase, accents stripped). Derived from the real feed, not from a map:
# the rule below resolves all 591 events of 2026 to exactly these 25 values.
# An entry missing from this table fails safe -- the event gets no country
# here and the pipeline resolves it from the place text -- so the cost of an
# omission is a missing flag, never a wrong one.
PERU_REGIONS = frozenset(
    {
        "amazonas",
        "ancash",
        "apurimac",
        "arequipa",
        "ayacucho",
        "cajamarca",
        "cusco",
        "huancavelica",
        "huanuco",
        "ica",
        "junin",
        "la libertad",
        "lambayeque",
        "lima",
        "loreto",
        "madre de dios",
        "moquegua",
        "pasco",
        "piura",
        "provincia constitucional del callao",
        "puno",
        "san martin",
        "tacna",
        "tumbes",
        "ucayali",
    }
)


def _strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    ).lower()


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _country(reference: str) -> str | None:
    """Peru only when the reference names a Peruvian region.

    The reference reads "<distance> de <town>, <province> - <department>", or
    "<distance> de Callao, Provincia Constitucional del Callao" which has no
    " - " at all. Taking the text after the last comma and then after the last
    " - " handles both shapes.
    """
    tail = reference.rsplit(",", 1)[-1].strip().rsplit(" - ", 1)[-1].strip()
    return "Peru" if _strip_accents(tail) in PERU_REGIONS else None


def parse_intensity(value: str | None) -> tuple[float, str] | None:
    """Mercalli degree and its label from "II-III Chupaca" -> (3.0, "II-III").

    A range is the span of what observers reported; the IGP publishes it as
    the event's maximum intensity, so the upper bound is the value that
    answers "how hard did this shake". The label is returned separately
    because the raw field glues a town name onto the degree, and a badge
    reading "MMI II-III Chupaca" would be repeating the place back at itself.

    Anything unreadable yields None rather than a guess -- absent is not zero,
    and on this field a zero would claim that nobody felt it.
    """
    if not value:
        return None
    match = RE_INTENSITY.match(value)
    if not match:
        return None
    degrees = [ROMAN[g] for g in match.groups() if g and g in ROMAN]
    if not degrees:
        return None
    return float(max(degrees)), match.group(0).strip()


def parse_time(row: dict[str, Any]) -> datetime | None:
    """The UTC date of `fecha_utc` joined to the UTC clock of `hora_utc`.

    Both fields are full ISO timestamps and only half of each one is real:
    `fecha_utc` is a date at midnight, `hora_utc` is a time on 1970-01-01.
    """
    date_part = str(row.get("fecha_utc") or "")[:10]
    time_part = str(row.get("hora_utc") or "")[11:19]
    if len(date_part) != 10 or len(time_part) != 8:
        return None
    try:
        return datetime.fromisoformat(f"{date_part}T{time_part}").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_row(row: dict[str, Any]) -> Event | None:
    code = str(row.get("codigo") or "").strip()
    if not code:
        return None
    time = parse_time(row)
    if time is None:
        # Without an origin time there is neither a date nor a chronology.
        return None

    lat, lon = _number(row.get("latitud")), _number(row.get("longitud"))
    # Lesson 15: a wrong position is far worse than a missing one, and the
    # model RAISES on an out-of-range coordinate -- unguarded, one bad row
    # would take the whole batch down with it.
    if lat is not None and not -90 <= lat <= 90:
        lat = None
    if lon is not None and not -180 <= lon <= 180:
        lon = None

    magnitude = _number(row.get("magnitud"))
    depth = _number(row.get("profundidad"))
    place = re.sub(r"\s+", " ", str(row.get("referencia") or "")).strip()
    felt = parse_intensity(row.get("intensidad"))
    intensity, intensity_label = felt if felt else (None, None)

    return Event(
        id=f"igp:{code}",
        source="igp",
        source_id=code,
        kind=Kind.EARTHQUAKE,
        time=time,
        lat=lat,
        lon=lon,
        depth_km=depth,
        magnitude=magnitude,
        # `tipomagnitud` is empty on every row measured, so claiming a scale
        # we were not told would be inventing one.
        mag_type=str(row.get("tipomagnitud") or "").strip() or "M",
        place=place,
        country=_country(place),
        # Depth, not magnitude alone: Peru sits on a subduction margin and IGP
        # routinely reports events between 100 and 600 km, where a M6 is a long
        # sway that breaks nothing.
        severity=severity_for_quake(magnitude, depth),
        intensity_mmi=intensity,
        alert=f"MMI {intensity_label}" if intensity_label else None,
        title=f"M {magnitude} -- {place}" if magnitude is not None else place,
        url=PAGE_URL,
        raw={
            "local_time": f"{str(row.get('fecha_local') or '')[:10]} "
            f"{str(row.get('hora_local') or '')[11:19]}",
            "intensidad": row.get("intensidad") or None,
            "idlistasismos": row.get("idlistasismos"),
            "report_pdf": row.get("reporte_acelerometrico_pdf") or None,
        },
    )


def years_to_poll(now: datetime) -> list[int]:
    """Which yearly lists can hold a current event.

    The feed is addressed by year, and an event just before midnight on
    31 December UTC lives in the old list while the clock already says the new
    year. Seven days of overlap costs one extra request a cycle for one week
    a year, and removes an entire class of new-year blind spot.
    """
    if now.month == 1 and now.day <= 7:
        return [now.year, now.year - 1]
    return [now.year]


class IgpSource(Source):
    """Instituto Geofisico del Peru -- the Peruvian national catalogue."""

    name = "igp"
    kind = "poll"

    def __init__(
        self,
        poll_seconds: float = 60.0,
        base_url: str = BASE_URL,
        window_days: float = 7.0,
    ):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.base_url = base_url
        # The response is the WHOLE year: 591 events and 410 KB by August.
        # Emitting all of it every minute would be 591 pipeline round-trips a
        # cycle to re-say what the store already knows, so the source carries
        # its own relevance rule (lesson 6) -- generously wider than the
        # three-day ingestion horizon so no boundary can eat an event.
        self.window_days = window_days

    def parse_payload(self, data: Any, now: datetime | None = None) -> list[Event]:
        rows = data if isinstance(data, list) else []
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.window_days)
        events: list[Event] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                event = parse_row(row)
            except Exception as exc:
                # One malformed entry must not cost us the rest of the year.
                log.warning("igp: unreadable row (%s)", exc)
                continue
            if event is not None and event.time >= cutoff:
                events.append(event)
        # The API serves the year oldest-first; freshness order is our job.
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def fetch_year(self, client: httpx.AsyncClient, year: int) -> list[Event] | None:
        """One yearly list. `None` means the request failed; an empty list
        means the year is genuinely empty, which is not a failure."""
        resp = await client.get(f"{self.base_url}/{year}")
        if resp.status_code == 404:
            # A year with no earthquakes yet answers 404 with an explanatory
            # body. That is the normal state of 1 January, not an outage.
            log.info("igp: no events published yet for %d", year)
            return []
        resp.raise_for_status()
        return self.parse_payload(resp.json())

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=60.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                events: list[Event] = []
                # Lesson 9: a multi-feed source counts its successes. Zero
                # successes is not `ok()`, however many years we asked for.
                successes = 0
                last_error: Exception | None = None
                for year in years_to_poll(datetime.now(UTC)):
                    try:
                        batch = await self.fetch_year(client, year)
                    except Exception as exc:
                        last_error = exc
                        log.warning("%s: %s (%d)", self.name, exc, year)
                        continue
                    if batch is not None:
                        successes += 1
                        events.extend(batch)

                if successes:
                    events.sort(key=lambda e: e.time, reverse=True)
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                elif last_error is not None:
                    self.health.fail(last_error)
                await asyncio.sleep(self.poll_seconds)
