"""Live climate indicators -- `GET /api/climate`.

What this is. The hazard feed answers "what is happening now"; these answer
"what state is the planet in" -- the background every one of those hazards
now happens against. Seven indicators, each read from the agency that
publishes it, each with THE DATASET'S OWN DATE.

The rule this module exists to keep (CLAUDE.md, "the feed must never lie
about its own freshness", and the owner's "rien doit etre fake"):

- every value comes from the dataset at run time. Nothing is hard-coded:
  not a value, not a baseline, not a "last known" default. Baselines are
  computed from the same file (the 1991-2020 columns Copernicus ships, the
  1991-2020 mean of the same calendar day for sea ice, the same day a year
  earlier for CO2);
- `as_of` is the date the DATASET says its last value is for, never the
  time we fetched it. The two are shown apart: `as_of` is the data,
  `state.last_ok` is our poll;
- `stale` is raised when `as_of` is older than the lag that dataset normally
  has. NSIDC's daily files stopped at 2026-10-17 (last modified 10-18) while
  this was written on the 27th: the panel says so instead of presenting a
  ten-day-old extent as today's;
- a failed poll says `available: false` with the real error. If an earlier
  poll succeeded, its value stays, with its own dataset date and the time it
  was fetched -- the same way a failed hazard source keeps the events it
  already delivered. Before the first success there is no value at all.

Traps verified against the real files (captured 2026-10-27):

- **Copernicus Climate Pulse** CSVs carry a `status` column: the last days
  are `PRELIMINARY` and "will likely change (slightly)" once final ERA5 lands
  about 3 days behind real time. Shown as such. Comment lines start with `#`.
- **NOAA GML** daily Mauna Loa is `year,month,day,decimal,ppm` after a long
  `#` header; days with no valid measurement are simply absent, so "the same
  day a year earlier" is looked up within +-3 days, never assumed to exist.
- **NSIDC Sea Ice Index v4** daily CSV: the first two lines are headers
  (names, then units), values are space-padded, the early record is every
  other day, and `Missing` can be non-zero (0.382 Mkm2 on 2026-10-17 in the
  south): carried through as `missing`, because an extent with missing
  coverage is a lower bound.
- **NOAA CPC** weekly Nino indices (`wksst9120.for`) are fixed-width with the
  anomaly GLUED to a negative value: `20.6-0.1` is SST 20.6, anomaly -0.1.
  The ENSO alert status is only in the HTML of the diagnostic discussion,
  with HTML entities (`El Ni&ntilde;o Advisory`), issued monthly.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"

PULSE_PAGE = "https://pulse.climate.copernicus.eu/"
PULSE_AIR = "https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_2t_global.csv"
PULSE_SST = (
    "https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_sst_60S-60N_ocean.csv"
)
CO2_DAILY = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_daily_mlo.csv"
CO2_PAGE = "https://gml.noaa.gov/ccgg/trends/"
NSIDC_NORTH = (
    "https://noaadata.apps.nsidc.org/NOAA/G02135/north/daily/data/N_seaice_extent_daily_v4.0.csv"
)
NSIDC_SOUTH = (
    "https://noaadata.apps.nsidc.org/NOAA/G02135/south/daily/data/S_seaice_extent_daily_v4.0.csv"
)
NSIDC_PAGE = "https://nsidc.org/sea-ice-today"
NINO_WEEKLY = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
ENSO_DISCUSSION = (
    "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
)


class ClimateParseError(ValueError):
    """The file arrived but does not say what it is supposed to say."""


@dataclass
class Reading:
    """One indicator, as the dataset states it."""

    value: float | str
    as_of: date
    unit: str | None = None
    anomaly: float | None = None
    baseline: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    status: str | None = None  # e.g. "preliminary"
    extra: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ parsers


def _rows(text: str) -> list[list[str]]:
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return list(csv.reader(io.StringIO("\n".join(lines))))


def parse_pulse(text: str, unit: str = "°C") -> Reading:
    """Copernicus Climate Pulse daily series: `date,<var>,clim_91-20,ano_91-20,status`."""
    rows = _rows(text)
    if not rows or len(rows[0]) < 5 or rows[0][2] != "clim_91-20" or rows[0][3] != "ano_91-20":
        raise ClimateParseError(f"unexpected Climate Pulse header: {rows[0] if rows else None}")
    data = [r for r in rows[1:] if len(r) >= 5]
    if len(data) < 2:
        raise ClimateParseError("Climate Pulse series has fewer than two rows")
    last, prev = data[-1], data[-2]
    return Reading(
        value=round(float(last[1]), 3),
        unit=unit,
        as_of=date.fromisoformat(last[0]),
        anomaly=round(float(last[3]), 3),
        baseline={
            "kind": "day_1991_2020",
            "label": "1991-2020 average for this day",
            "value": round(float(last[2]), 3),
        },
        previous={
            "as_of": prev[0],
            "value": round(float(prev[1]), 3),
            "anomaly": round(float(prev[3]), 3),
        },
        status=last[4].strip().lower() or None,
    )


def parse_co2_daily(text: str) -> Reading:
    """NOAA GML daily Mauna Loa: `year,month,day,decimal,ppm`."""
    series: dict[date, float] = {}
    for row in _rows(text):
        if len(row) < 5:
            continue
        try:
            day = date(int(row[0]), int(row[1]), int(row[2]))
            ppm = float(row[4])
        except ValueError:
            continue
        if ppm > 0:  # a missing value is written negative in some GML files
            series[day] = ppm
    if len(series) < 2:
        raise ClimateParseError("no daily CO2 values")
    days = sorted(series)
    last, prev = days[-1], days[-2]
    reading = Reading(
        value=round(series[last], 2),
        unit="ppm",
        as_of=last,
        previous={"as_of": prev.isoformat(), "value": round(series[prev], 2)},
    )
    target = last.replace(year=last.year - 1) if not (last.month == 2 and last.day == 29) else None
    if target is not None:
        nearest = min(
            (d for d in series if abs((d - target).days) <= 3),
            key=lambda d: abs((d - target).days),
            default=None,
        )
        if nearest is not None:
            reading.baseline = {
                "kind": "day_year_earlier",
                "label": "same day a year earlier",
                "as_of": nearest.isoformat(),
                "value": round(series[nearest], 2),
            }
            reading.anomaly = round(series[last] - series[nearest], 2)
    return reading


def parse_nsidc(text: str) -> Reading:
    """NSIDC Sea Ice Index v4 daily extent: `Year, Month, Day, Extent, Missing, ...`."""
    series: dict[date, tuple[float, float]] = {}
    lines = text.splitlines()
    if not lines or not lines[0].replace(" ", "").startswith("Year,Month,Day,Extent"):
        raise ClimateParseError(f"unexpected NSIDC header: {lines[0][:60] if lines else None}")
    for row in csv.reader(io.StringIO("\n".join(lines[2:]))):
        if len(row) < 5:
            continue
        try:
            day = date(int(row[0]), int(row[1]), int(row[2]))
            extent, missing = float(row[3]), float(row[4])
        except ValueError:
            continue
        series[day] = (extent, missing)
    if len(series) < 2:
        raise ClimateParseError("no sea ice extent values")
    days = sorted(series)
    last, prev = days[-1], days[-2]
    extent, missing = series[last]
    same_day = [
        series[d][0]
        for d in days
        if 1991 <= d.year <= 2020 and (d.month, d.day) == (last.month, last.day)
    ]
    reading = Reading(
        value=round(extent, 3),
        unit="million km²",
        as_of=last,
        previous={
            "as_of": prev.isoformat(),
            "value": round(series[prev][0], 3),
            "missing_million_km2": round(series[prev][1], 3),
        },
        extra={"missing_million_km2": round(missing, 3)},
    )
    if same_day:
        mean = sum(same_day) / len(same_day)
        reading.baseline = {
            "kind": "day_1991_2020",
            "label": "1991-2020 average for this day",
            "value": round(mean, 3),
            "years": len(same_day),
        }
        reading.anomaly = round(extent - mean, 3)
    return reading


RE_WEEK = re.compile(r"^\s*(\d{2}[A-Z]{3}\d{4})\s+(.*)$")
RE_NUMBER = re.compile(r"-?\d+\.\d")


def parse_nino_weekly(text: str) -> Reading:
    """CPC weekly Nino indices; Nino 3.4 is the third SST/SSTA pair."""
    weeks: list[tuple[date, float, float]] = []
    for line in text.splitlines():
        match = RE_WEEK.match(line)
        if not match:
            continue
        numbers = RE_NUMBER.findall(match.group(2))
        if len(numbers) != 8:
            continue
        week = datetime.strptime(match.group(1).title(), "%d%b%Y").date()
        weeks.append((week, float(numbers[4]), float(numbers[5])))
    if len(weeks) < 2:
        raise ClimateParseError("no weekly Nino 3.4 rows")
    (week, sst, ssta), (pweek, psst, pssta) = weeks[-1], weeks[-2]
    return Reading(
        value=sst,
        unit="°C",
        as_of=week,
        anomaly=ssta,
        baseline={
            "kind": "week_1991_2020",
            "label": "1991-2020 weekly average",
            "value": round(sst - ssta, 1),
        },
        previous={"as_of": pweek.isoformat(), "value": psst, "anomaly": pssta},
        extra={"week": "centred on as_of"},
    )


RE_ISSUED = re.compile(r"issued by\s+CLIMATE PREDICTION CENTER/NCEP/NWS\s+(\d{1,2} \w+ \d{4})")
RE_NEXT = re.compile(r"next ENSO Diagnostics Discussion is scheduled for (\d{1,2} \w+ \d{4})")
# Block-level tags end a paragraph. The page is read as BLOCKS, not as one
# collapsed string: the first version collapsed every whitespace run to one
# space, so the regex looking for the end of the synopsis never found one and
# the "synopsis" ran 2545 characters, through every discussion paragraph and
# CPC's e-mail sign-up boilerplate (PR #5 review). In the page, the synopsis
# and the status each sit in their own block.
RE_BLOCK_TAG = re.compile(r"<\s*/?\s*(?:p|td|tr|br|div|table|li|h[1-6])\b[^>]*>", re.IGNORECASE)
STATUS_LABEL = "ENSO Alert System Status:"
SYNOPSIS_LABEL = "Synopsis:"


def _blocks(page: str) -> list[str]:
    text = RE_BLOCK_TAG.sub("\x00", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    # str.split() also splits on the non-breaking spaces of `&nbsp;`
    return [b for b in (" ".join(chunk.split()) for chunk in text.split("\x00")) if b]


def _after(blocks: list[str], label: str) -> str | None:
    for block in blocks:
        if block.startswith(label):
            rest = block[len(label) :].strip()
            return rest or None
    return None


def parse_enso_discussion(page: str) -> Reading:
    blocks = _blocks(page)
    flat = " ".join(blocks)
    issued = RE_ISSUED.search(flat)
    status = _after(blocks, STATUS_LABEL)
    if not issued or not status:
        raise ClimateParseError("ENSO discussion: issue date or alert status not found")
    upcoming = RE_NEXT.search(flat)
    return Reading(
        value=status,
        as_of=datetime.strptime(issued.group(1), "%d %B %Y").date(),
        extra={
            "synopsis": _after(blocks, SYNOPSIS_LABEL),
            "next_issue": (
                datetime.strptime(upcoming.group(1), "%d %B %Y").date().isoformat()
                if upcoming
                else None
            ),
        },
    )


# ----------------------------------------------------------------- indicators


@dataclass
class Indicator:
    id: str
    label: str
    url: str
    parse: Callable[[str], Reading]
    source: str
    source_url: str
    licence: str
    # how often the dataset really changes, and the age beyond which its
    # latest value is late by its own standards
    poll_seconds: float
    max_age_days: float
    reading: Reading | None = None
    last_ok: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None
    errors: int = 0

    def due(self, now: datetime) -> bool:
        if self.last_attempt is None:
            return True
        wait = self.poll_seconds if self.last_error is None else min(self.poll_seconds, 900.0)
        return (now - self.last_attempt).total_seconds() >= wait

    def public(self, now: datetime) -> dict[str, Any]:
        reading = self.reading
        stale = reading is not None and (now.date() - reading.as_of).days > self.max_age_days
        body: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "available": reading is not None and self.last_error is None,
            "value": reading.value if reading else None,
            "unit": reading.unit if reading else None,
            "as_of": reading.as_of.isoformat() if reading else None,
            "status": reading.status if reading else None,
            "anomaly": reading.anomaly if reading else None,
            "baseline": reading.baseline if reading else None,
            "previous": reading.previous if reading else None,
            "trend": _trend(reading),
            # additive: the value (and its anomaly) is a minimum, not a
            # measurement -- part of the area had no data
            "lower_bound": _lower_bound(reading),
            "stale": stale,
            "max_age_days": self.max_age_days,
            "detail": reading.extra if reading else {},
            "source": {
                "name": self.source,
                "url": self.source_url,
                "data_url": self.url,
                "licence": self.licence,
            },
            "state": {
                "last_ok": self.last_ok.isoformat() if self.last_ok else None,
                "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
                "last_error": self.last_error,
                "errors": self.errors,
                "poll_seconds": self.poll_seconds,
            },
        }
        return body


def _lower_bound(reading: Reading | None) -> bool:
    """An extent measured with part of the area missing is a LOWER BOUND."""
    return bool(reading and (reading.extra.get("missing_million_km2") or 0) > 0)


def _trend(reading: Reading | None) -> str | None:
    """up / down / flat against the previous value the dataset itself gives.

    None when the change is within the data the dataset says it is missing:
    on 2026-10-17 the Antarctic extent went 16.225 -> 16.204 (a 0.021 drop)
    with 0.382 of coverage missing, so "falling" was not something the file
    could say (PR #5 review).
    """
    if reading is None or reading.previous is None or isinstance(reading.value, str):
        return None
    missing = (reading.extra.get("missing_million_km2") or 0) + (
        reading.previous.get("missing_million_km2") or 0
    )
    previous_value = reading.previous.get("value")
    if (
        missing
        and isinstance(previous_value, (int, float))
        and abs(float(reading.value) - float(previous_value)) <= missing
    ):
        return None
    key = "anomaly" if reading.anomaly is not None and "anomaly" in reading.previous else "value"
    now_v = reading.anomaly if key == "anomaly" else reading.value
    then_v = reading.previous.get(key)
    if not isinstance(now_v, (int, float)) or not isinstance(then_v, (int, float)):
        return None
    if abs(now_v - then_v) < 1e-9:
        return "flat"
    return "up" if now_v > then_v else "down"


C3S = "Copernicus Climate Change Service (C3S) / ECMWF, Climate Pulse (ERA5)"
C3S_LICENCE = "CC BY 4.0 -- generated using Copernicus Climate Change Service information"


def build_indicators() -> list[Indicator]:
    six_hours = 6 * 3600.0
    return [
        Indicator(
            "global_air_temperature",
            "Global surface air temperature",
            PULSE_AIR,
            lambda t: parse_pulse(t),
            C3S,
            PULSE_PAGE,
            C3S_LICENCE,
            # updated once a day (~04:15 UTC); every 3 h finds it within hours
            poll_seconds=3 * 3600.0,
            max_age_days=7,
        ),
        Indicator(
            "sea_surface_temperature",
            "Sea surface temperature (60°S-60°N)",
            PULSE_SST,
            lambda t: parse_pulse(t),
            C3S,
            PULSE_PAGE,
            C3S_LICENCE,
            poll_seconds=3 * 3600.0,
            max_age_days=7,
        ),
        Indicator(
            "co2_mauna_loa",
            "CO₂ at Mauna Loa (daily)",
            CO2_DAILY,
            parse_co2_daily,
            "NOAA Global Monitoring Laboratory",
            CO2_PAGE,
            "freely available, credit NOAA GML",
            poll_seconds=six_hours,
            max_age_days=7,
        ),
        Indicator(
            "arctic_sea_ice_extent",
            "Arctic sea ice extent",
            NSIDC_NORTH,
            parse_nsidc,
            "NSIDC Sea Ice Index, version 4",
            NSIDC_PAGE,
            "free use with citation (Fetterer et al., NSIDC G02135)",
            poll_seconds=six_hours,
            max_age_days=4,
        ),
        Indicator(
            "antarctic_sea_ice_extent",
            "Antarctic sea ice extent",
            NSIDC_SOUTH,
            parse_nsidc,
            "NSIDC Sea Ice Index, version 4",
            NSIDC_PAGE,
            "free use with citation (Fetterer et al., NSIDC G02135)",
            poll_seconds=six_hours,
            max_age_days=4,
        ),
        Indicator(
            "nino34_weekly",
            "Niño 3.4 sea surface temperature (weekly)",
            NINO_WEEKLY,
            parse_nino_weekly,
            "NOAA Climate Prediction Center",
            "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/",
            "public domain (US government work)",
            # weekly, published on Mondays
            poll_seconds=12 * 3600.0,
            max_age_days=14,
        ),
        Indicator(
            "enso_status",
            "ENSO alert status",
            ENSO_DISCUSSION,
            parse_enso_discussion,
            "NOAA Climate Prediction Center, ENSO diagnostic discussion",
            ENSO_DISCUSSION,
            "public domain (US government work)",
            # monthly, around the second Thursday
            poll_seconds=12 * 3600.0,
            max_age_days=40,
        ),
    ]


class ClimateMonitor:
    """Polls every indicator at its own cadence, one fetch at a time."""

    def __init__(self, indicators: list[Indicator] | None = None, tick_seconds: float = 60.0):
        self.indicators = indicators if indicators is not None else build_indicators()
        self.tick_seconds = tick_seconds

    async def poll(self, client: httpx.AsyncClient, now: datetime | None = None) -> int:
        """Fetches every indicator that is due. Returns how many it tried."""
        tried = 0
        for indicator in self.indicators:
            moment = now or datetime.now(UTC)
            if not indicator.due(moment):
                continue
            tried += 1
            indicator.last_attempt = moment
            try:
                resp = await client.get(indicator.url)
                resp.raise_for_status()
                indicator.reading = indicator.parse(resp.text)
                indicator.last_ok = datetime.now(UTC)
                indicator.last_error = None
            except Exception as exc:  # noqa: BLE001 -- recorded, shown, retried
                indicator.errors += 1
                indicator.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("climate %s: %s", indicator.id, indicator.last_error)
        return tried

    async def run(self) -> None:
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(
            timeout=90.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    await self.poll(client)
                except Exception as exc:  # noqa: BLE001 -- the task must survive
                    log.warning("climate poll: %s", exc)
                await asyncio.sleep(self.tick_seconds)

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        return {
            "server_time": moment.isoformat(),
            "indicators": [indicator.public(moment) for indicator in self.indicators],
        }


monitor = ClimateMonitor()
