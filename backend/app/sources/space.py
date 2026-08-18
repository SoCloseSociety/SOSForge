"""NOAA SWPC space weather -- the only hazard here with a genuine multi-day
forecast.

Every other source in this product reports the past: an earthquake already
happened, a fire is already burning. A geomagnetic storm is different --
NOAA's Space Weather Prediction Center runs an operational 1-to-3-day
forecast, and storms disrupt power grids, satellites, aviation routes and
GPS days before they arrive.

Three products, combined:

- `noaa-scales.json`: a small object keyed by STRING day offset. `"-1"` =
  yesterday (observed), `"0"` = today (current, already happening), `"1"`
  through `"3"` = the actual FORECAST days. Values are strings, including
  `G.Scale` ("0".."5"). `R.Scale` (radio blackout) is null on forecast days
  -- only its probabilities are populated -- but `G.Scale` (geomagnetic
  storm) is populated directly even on forecast days, which is why severity
  here is keyed on G alone: it is the one field that does not need a second
  layer of probability interpretation to read.
- `noaa-planetary-k-index-forecast.json`: a flat array of 3-hourly points,
  each tagged `observed` / `estimated` / `predicted`. Only `predicted`
  genuinely anticipates the future -- `estimated` is a same-day nowcast off
  real-time ground magnetometers, closer to "current conditions" than to a
  forecast. Being conservative, it is left out too.
- `alerts.json`: free-text bulletins. The type lives INSIDE the message
  body ("Space Weather Message Code: WARK05"), not in `product_id` (a
  bulletin *series* id, reused across reissues). Four families exist: WAT
  (watch) and WAR (warning) anticipate; ALT (alert) and SUM (summary)
  describe something already measured. Ingesting ALT/SUM as forecasts would
  be exactly the freshness lie this product forbids -- so only WAT/WAR are
  kept. Measured on a real capture (2026-08-18, 80 messages): 35 WAR + 7 WAT
  kept, 30 ALT + 8 SUM dropped.

None of these events have a position: a geomagnetic storm concerns a
latitude band (poleward of N degrees geomagnetic latitude), not a point.
`lat`/`lon` stay `None` -- they show in the feed, not on the map. The
`Event` model has no dedicated space-weather `Kind`; `Kind.SPACE_WEATHER` is used
and the title carries the meaning (see the source-level report for why a
dedicated kind is likely warranted going forward).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, to_utc, utcnow
from app.sources.base import Emit
from app.sources.regional import USER_AGENT, JsonPollSource

log = logging.getLogger(__name__)

# --------------------------------------------------------------------- scales

# NOAA's own wording for each G-scale level (also mirrors R and S).
G_LABEL = {0: "none", 1: "minor", 2: "moderate", 3: "strong", 4: "severe", 5: "extreme"}

# Deliberately conservative, per NOAA's own scale definitions: G1-G2 are
# routine (weak power-grid fluctuations, minor satellite impact). G3
# ("strong") is graded SEVERE rather than EXTREME -- it is still below the
# tier that damages transformers. Only G4-G5, the levels NOAA associates
# with grid voltage control problems and possible transformer damage, are
# graded EXTREME.
G_SEVERITY = {
    0: Severity.INFO,
    1: Severity.MINOR,
    2: Severity.MODERATE,
    3: Severity.SEVERE,
    4: Severity.EXTREME,
    5: Severity.EXTREME,
}


def _severity_from_scale(n: int) -> Severity:
    return G_SEVERITY.get(max(0, min(5, n)), Severity.INFO)


def _kp_to_g(kp: float) -> int:
    """Kp -> G-scale, per NOAA's own definition: G1..G5 = Kp 5..9."""
    if kp < 5:
        return 0
    return min(5, int(kp) - 4)


def _max_severity(a: Severity, b: Severity) -> Severity:
    order = list(Severity)
    return a if order.index(a) >= order.index(b) else b


def _scale_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _scales_timestamp(date_stamp: str | None, time_stamp: str | None) -> datetime | None:
    if not date_stamp or not time_stamp:
        return None
    return to_utc(f"{date_stamp}T{time_stamp}")


# --------------------------------------------------------------------- alerts

MESSAGE_CODE_RE = re.compile(r"Space Weather Message Code:\s*(\w+)")
SERIAL_RE = re.compile(r"Serial Number:\s*(\d+)")
SCALE_RE = re.compile(r"Noaa Scale:\s*([GS])(\d)", re.IGNORECASE)

# A day-list entry inside a WATCH bulletin, e.g. "Aug 18:  None (Below G1)"
# or "Aug 19:  G1 (Minor)". The single capture group is the G digit, empty
# when the branch matched "None".
DAY_LEVEL_RE = re.compile(r"[A-Za-z]{3}\s+\d{1,2}\s*:\s*(?:G(\d)|None)")
# Same day tokens, but capturing the calendar date itself (month, day) --
# used to derive an implied end-of-validity for watches that carry no
# explicit "Valid To".
DAY_TOKEN_RE = re.compile(r"([A-Za-z]{3})\s+(\d{1,2})\s*:")

KINDEX_RE = re.compile(r"Geomagnetic K-index of (\d+)")
VALID_UNTIL_RE = re.compile(r"(?:Valid To|Now Valid Until):\s*(\d{4} \w{3} \d{1,2} \d{4}) UTC")


def _classify_alert(message: str) -> tuple[Severity, str] | None:
    """Severity + human basis for a WATCH/WARNING message, or `None` if the
    bulletin does not actually anticipate anything (a CANCEL WATCH whose
    every listed day reads "None (Below G1)")."""
    scale_match = SCALE_RE.search(message)
    if scale_match:
        letter, digit = scale_match.group(1).upper(), int(scale_match.group(2))
        storm = "geomagnetic storm" if letter == "G" else "radiation storm"
        return _severity_from_scale(digit), f"{letter}{digit} ({G_LABEL[digit]}) {storm}"

    day_matches = DAY_LEVEL_RE.findall(message)
    if day_matches:
        day_values = [int(g) for g in day_matches if g]
        if not day_values:
            return None
        peak = max(day_values)
        return _severity_from_scale(peak), f"G{peak} ({G_LABEL[peak]}) geomagnetic storm"

    k_match = KINDEX_RE.search(message)
    if k_match:
        kp = int(k_match.group(1))
        return _severity_from_scale(_kp_to_g(kp)), f"K-index {kp} geomagnetic activity"

    # No gradable scale at all (e.g. a Sudden Impulse warning, which reports
    # a shock detected at L1 with no numeric scale of its own). Still a
    # genuine anticipatory bulletin: kept, conservatively, at the lowest
    # graded severity rather than silently dropped or over-stated.
    return Severity.MINOR, "geomagnetic disturbance"


def _parse_valid_until(message: str) -> datetime | None:
    match = VALID_UNTIL_RE.search(message)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y %b %d %H%M").replace(tzinfo=UTC)
    except ValueError:
        return None


def _implied_valid_until(message: str, issue_time: datetime) -> datetime | None:
    """Fallback for watches with no explicit "Valid To": the last calendar
    day named in the day-list, valid through its end. A 3-day watch never
    trails its issue time by more than a handful of days, so a parsed month
    earlier than the issue month means the window crossed a year boundary
    (a watch issued in December reaching into January)."""
    tokens = DAY_TOKEN_RE.findall(message)
    if not tokens:
        return None
    month_str, day_str = tokens[-1]
    try:
        month = datetime.strptime(month_str, "%b").month
        day = int(day_str)
    except ValueError:
        return None
    year = issue_time.year
    if month < issue_time.month:
        year += 1
    try:
        return datetime(year, month, day, 23, 59, 59, tzinfo=UTC)
    except ValueError:
        return None


class SpaceWeatherSource(JsonPollSource):
    """NOAA SWPC: geomagnetic storm outlook (scales + Kp forecast) plus
    watch/warning bulletins. See module docstring for the three products and
    why only WAT/WAR alerts are kept.
    """

    name = "space"
    kind = "poll"
    url = "https://services.swpc.noaa.gov/products/noaa-scales.json"
    kp_url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
    alerts_url = "https://services.swpc.noaa.gov/products/alerts.json"

    def __init__(self, poll_seconds: float = 600.0) -> None:
        # these products regenerate on the scale of hours; 600s (10 min) is
        # already more frequent than useful, never a strain on SWPC
        super().__init__(poll_seconds=poll_seconds)

    def parse_payload(self, data: Any) -> list[Event]:
        return self.parse_scales(data)

    # ----------------------------------------------------------- the outlook

    def parse_scales(self, data: Any, now: datetime | None = None) -> list[Event]:
        now = now or utcnow()
        if not isinstance(data, dict):
            return []

        forecast_days: list[dict[str, Any]] = []
        for key in ("1", "2", "3"):
            day = data.get(key)
            if not isinstance(day, dict):
                continue
            g_block = day.get("G") or {}
            r_block = day.get("R") or {}
            s_block = day.get("S") or {}
            forecast_days.append(
                {
                    "date": day.get("DateStamp"),
                    "g_scale": _scale_int(g_block.get("Scale")),
                    "g_text": g_block.get("Text"),
                    "r_minor_prob": r_block.get("MinorProb"),
                    "r_major_prob": r_block.get("MajorProb"),
                    "s_prob": s_block.get("Prob"),
                }
            )

        g_values = [d["g_scale"] for d in forecast_days if d["g_scale"] is not None]
        if not g_values or max(g_values) < 1:
            # Nothing above G0 anywhere in the 3-day window: no storm is
            # anticipated. An event that only ever says "nothing expected"
            # is noise, not a forecast -- the same reasoning as GDACS's
            # green-noise filter (see hazards.py / README).
            return []

        max_g = max(g_values)
        peak = next(d for d in forecast_days if d["g_scale"] == max_g)

        current = data.get("0") or {}
        issued = _scales_timestamp(current.get("DateStamp"), current.get("TimeStamp")) or now
        yesterday = data.get("-1") or {}

        return [
            Event(
                id="space:outlook",
                source="space",
                source_id="outlook",
                kind=Kind.SPACE_WEATHER,
                time=issued,
                lat=None,
                lon=None,
                severity=_severity_from_scale(max_g),
                # A rolling 3-day outlook: it stays "ongoing" for as long as
                # SWPC keeps publishing a forecast above G0. The pipeline's
                # 6h-silence sweep is what retires it once the window has
                # fully rolled past (see app/pipeline.py).
                ongoing=True,
                title=(
                    f"Geomagnetic storm watch: G{max_g} ({G_LABEL[max_g]}) expected {peak['date']}"
                ),
                url="https://www.swpc.noaa.gov/products/3-day-forecast",
                raw={
                    "forecast_days": forecast_days,
                    # day "0" and "-1" are context, never the trigger: "0"
                    # is CURRENT (already happening), "-1" is YESTERDAY
                    # (fully observed) -- neither one anticipates anything.
                    "current": {
                        "date": current.get("DateStamp"),
                        "g_scale": _scale_int((current.get("G") or {}).get("Scale")),
                        "g_text": (current.get("G") or {}).get("Text"),
                    },
                    "observed_yesterday": {
                        "date": yesterday.get("DateStamp"),
                        "g_scale": _scale_int((yesterday.get("G") or {}).get("Scale")),
                    },
                },
            )
        ]

    def parse_kp_forecast(self, data: Any) -> dict[str, Any] | None:
        """Peak of the predicted Kp series -- attached to the outlook event
        as supplementary detail (exact expected time of the peak, which the
        daily G-scale table does not carry).

        `observed` is `"observed"` (measured), `"estimated"` (a same-day
        nowcast off real-time magnetometers -- not a forecast of the
        future) or `"predicted"` (the actual forecast). Only `predicted` is
        used; see the module docstring for why `estimated` is left out too.
        """
        if not isinstance(data, list):
            return None
        predicted = [
            row
            for row in data
            if isinstance(row, dict)
            and row.get("observed") == "predicted"
            and row.get("kp") is not None
        ]
        if not predicted:
            return None
        peak = max(predicted, key=lambda row: row["kp"])
        peak_kp = float(peak["kp"])
        return {
            "predicted_points": len(predicted),
            "peak_kp": peak_kp,
            "peak_time": peak.get("time_tag"),
            "implied_g": _kp_to_g(peak_kp),
        }

    # -------------------------------------------------------------- alerts

    def parse_alerts(self, data: Any, now: datetime | None = None) -> list[Event]:
        now = now or utcnow()
        events: list[Event] = []
        for item in data or []:
            event = self._parse_alert(item, now)
            if event is not None:
                events.append(event)
        return events

    def _parse_alert(self, item: Any, now: datetime) -> Event | None:
        if not isinstance(item, dict):
            return None
        message = item.get("message") or ""
        code_match = MESSAGE_CODE_RE.search(message)
        if not code_match:
            return None
        code = code_match.group(1)

        # See module docstring: WAT/WAR anticipate, ALT/SUM describe
        # something already measured. Only the type prefix decides this --
        # never `product_id`, which only names the bulletin series.
        if not (code.startswith("WAT") or code.startswith("WAR")):
            return None

        classification = _classify_alert(message)
        if classification is None:
            return None
        severity, basis = classification

        issue_time = to_utc(item.get("issue_datetime"))
        if issue_time is None:
            return None

        serial_match = SERIAL_RE.search(message)
        # A bulletin is re-issued (extended, superseded, cancelled) under a
        # new serial number every time its content changes -- the same
        # reasoning as the ash SIGMET composite key (AshSource): the
        # serial, not the product family, is what makes one issuance unique.
        key = serial_match.group(1) if serial_match else issue_time.isoformat()
        product_id = item.get("product_id") or code

        valid_until = _parse_valid_until(message) or _implied_valid_until(message, issue_time)
        ongoing = valid_until is not None and valid_until > now

        kind_label = "watch" if code.startswith("WAT") else "warning"
        return Event(
            id=f"space:alert:{product_id}:{key}",
            source="space",
            source_id=f"{product_id}:{key}",
            kind=Kind.SPACE_WEATHER,
            time=issue_time,
            lat=None,
            lon=None,
            severity=severity,
            ongoing=ongoing,
            alert=code,
            title=f"Geomagnetic {kind_label}: {basis}",
            url="https://www.swpc.noaa.gov/noaa-scales-explanation",
            raw={
                "product_id": product_id,
                "message_code": code,
                "serial_number": serial_match.group(1) if serial_match else None,
                "valid_until": valid_until.isoformat() if valid_until else None,
                "message": message,
            },
        )

    # ------------------------------------------------------------------ run

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    events = await self._poll_once(client)
                    for event in events:
                        await emit(event)
                    self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)

    async def _poll_once(self, client: httpx.AsyncClient) -> list[Event]:
        now = utcnow()
        resp = await client.get(self.build_url())
        resp.raise_for_status()
        events = self.parse_scales(resp.json(), now=now)

        # The Kp forecast and the alert bulletins are supplementary detail
        # layered onto the same outlook: a failure here must never cost us
        # the scales-derived outlook already built above -- same isolation
        # pattern as NhcSource's forecast-track attachment (hazards.py).
        try:
            kp_resp = await client.get(self.kp_url)
            kp_resp.raise_for_status()
            kp_summary = self.parse_kp_forecast(kp_resp.json())
            if events and kp_summary is not None:
                events[0].raw["kp_forecast"] = kp_summary
                kp_severity = _severity_from_scale(kp_summary["implied_g"])
                events[0].severity = _max_severity(events[0].severity, kp_severity)
        except Exception as exc:
            log.warning("%s: kp forecast failed: %s", self.name, exc)

        try:
            alerts_resp = await client.get(self.alerts_url)
            alerts_resp.raise_for_status()
            events.extend(self.parse_alerts(alerts_resp.json(), now=now))
        except Exception as exc:
            log.warning("%s: alerts failed: %s", self.name, exc)

        return events
