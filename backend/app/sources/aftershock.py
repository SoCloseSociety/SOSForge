"""USGS official aftershock forecasts (OAF).

After a significant mainshock, the USGS publishes an official probabilistic
forecast: "38% chance of a M6+ aftershock within 7 days". That is genuine
anticipation, worldwide, official, and displayed by no consumer tracker --
everything else in this product's other sources reports the past.

Three-step fetch, all verified against the live API:

1. Discovery: `producttype=oaf` on the FDSN event query lists exactly the
   mainshocks that HAVE a forecast right now -- no guessing which quake got
   one.
2. Detail: `.../feed/v1.0/detail/{id}.geojson` ->
   `properties.products.oaf[0].contents["forecast.json"].url`.
3. `forecast.json`: the probability table itself.

A forecast only changes at its own `nextForecastTime` (about once a day in
practice, per the live payloads seen so far): re-fetching it every poll cycle
would be pure waste, so each mainshock's forecast is cached and only
refetched once `nextForecastTime` has passed, until `expireTime` -- at which
point it is dropped rather than kept alive with a stale table.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.models.event import Event, Kind, Severity, utcnow
from app.sources.base import Emit, Source
from app.sources.regional import USER_AGENT

log = logging.getLogger(__name__)

DISCOVERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
DETAIL_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/{event_id}.geojson"

# The forecast bins are cumulative ("probability of AT LEAST this
# magnitude"), always at whole-number magnitudes in the live payloads seen so
# far (3, 4, 5, 6, 7). M6.0 is the same threshold app.models.event's own
# severity_from_magnitude uses to enter SEVERE (where building damage
# starts), so it is the headline number for the title whenever it is present.
HEADLINE_MAGNITUDE = 6.0

# A bin's magnitude is matched to a target threshold only within this
# tolerance -- enough to absorb float noise (6.0 stored as 5.9999999), never
# loose enough to read a M5 bin as if it were M6.
_BIN_MATCH_TOLERANCE = 0.05

WINDOW_PHRASE = {
    "1 Day": "1 day",
    "1 Week": "7 days",
    "1 Month": "1 month",
    "1 Year": "1 year",
}


def _epoch_ms_to_utc(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


@dataclass
class Mainshock:
    """Just enough identity + geometry to place a forecast on the map. The
    forecast itself is never read from here -- it is a separate fetch."""

    id: str
    place: str
    mag: float | None
    lat: float | None
    lon: float | None
    depth_km: float | None
    time: datetime


def parse_discovery(data: Any) -> list[Mainshock]:
    """The `producttype=oaf` query response -> the mainshocks it lists."""
    out: list[Mainshock] = []
    for feature in (data or {}).get("features") or []:
        event_id = feature.get("id")
        props = feature.get("properties") or {}
        time = _epoch_ms_to_utc(props.get("time"))
        if not event_id or time is None:
            continue
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        out.append(
            Mainshock(
                id=str(event_id),
                place=props.get("place") or "unknown location",
                mag=props.get("mag"),
                lat=coords[1] if len(coords) > 1 else None,
                lon=coords[0] if len(coords) > 0 else None,
                depth_km=coords[2] if len(coords) > 2 else None,
                time=time,
            )
        )
    return out


def extract_forecast_url(detail_data: Any) -> str | None:
    """`properties.products.oaf[0].contents["forecast.json"].url`, defensively.

    Several other product types can exist alongside `oaf` on the same
    detail feed (shakemap, losspager, ...); only the first oaf product is
    read, matching what the USGS event page itself shows as "the" forecast.
    """
    products = ((detail_data or {}).get("properties") or {}).get("products") or {}
    oaf_products = products.get("oaf") or []
    if not oaf_products:
        return None
    contents = (oaf_products[0] or {}).get("contents") or {}
    forecast = contents.get("forecast.json") or {}
    url = forecast.get("url")
    return url or None


@dataclass
class Forecast:
    """Our own shape for a parsed `forecast.json`. `bins` inside each window
    keep magnitude/probability/p95minimum/p95maximum only -- the per-window
    `fractileValues` array (100+ integers, the full distribution) is not part
    of "the probability table" this product displays and is dropped."""

    creation_time: datetime
    expire_time: datetime
    next_forecast_time: datetime
    advisory_time_frame: str | None
    model_name: str | None
    observations: list[dict]
    windows: list[dict]


def parse_forecast(data: Any) -> Forecast | None:
    """`forecast.json` -> `Forecast`, or None if it cannot be trusted.

    `creationTime`, `expireTime` and `nextForecastTime` are load-bearing:
    they drive `ongoing` and the refetch schedule. A forecast missing any of
    them, or with no usable window, is not usable -- we return None rather
    than guess a validity window or invent a probability.
    """
    data = data or {}
    creation_time = _epoch_ms_to_utc(data.get("creationTime"))
    expire_time = _epoch_ms_to_utc(data.get("expireTime"))
    next_forecast_time = _epoch_ms_to_utc(data.get("nextForecastTime"))
    if creation_time is None or expire_time is None or next_forecast_time is None:
        return None

    windows: list[dict] = []
    for window in data.get("forecast") or []:
        label = window.get("label")
        bins = [
            {
                "magnitude": b.get("magnitude"),
                "probability": b.get("probability"),
                "p95minimum": b.get("p95minimum"),
                "p95maximum": b.get("p95maximum"),
            }
            for b in window.get("bins") or []
            if b.get("magnitude") is not None and b.get("probability") is not None
        ]
        if label and bins:
            windows.append(
                {
                    "label": label,
                    "time_start": _epoch_ms_to_utc(window.get("timeStart")),
                    "time_end": _epoch_ms_to_utc(window.get("timeEnd")),
                    "bins": bins,
                }
            )
    if not windows:
        return None

    model = data.get("model") or {}
    observations = [o for o in (data.get("observations") or []) if isinstance(o, dict)]

    return Forecast(
        creation_time=creation_time,
        expire_time=expire_time,
        next_forecast_time=next_forecast_time,
        advisory_time_frame=data.get("advisoryTimeFrame"),
        model_name=model.get("name"),
        observations=observations,
        windows=windows,
    )


def select_headline_window(forecast: Forecast) -> dict | None:
    """The window this forecast itself designates as primary
    (`advisoryTimeFrame`), falling back to "1 Week" (the USGS default
    headline horizon), then to whichever window is first."""
    if not forecast.windows:
        return None
    for window in forecast.windows:
        if window["label"] == forecast.advisory_time_frame:
            return window
    for window in forecast.windows:
        if window["label"] == "1 Week":
            return window
    return forecast.windows[0]


def headline_bin(bins: list[dict]) -> dict | None:
    """The bin nearest M6.0 -- the "damaging aftershock" threshold -- among
    whatever bins the forecast actually published. Never invents a bin: a
    forecast whose bins stop at M5 simply gets an M5 headline."""
    if not bins:
        return None
    return min(bins, key=lambda b: abs(b["magnitude"] - HEADLINE_MAGNITUDE))


def _bin_probability(bins: list[dict], magnitude: float) -> float | None:
    for b in bins:
        if abs(b["magnitude"] - magnitude) <= _BIN_MATCH_TOLERANCE:
            return b["probability"]
    return None


def aftershock_severity(bins: list[dict]) -> Severity:
    """Conservative on purpose, same spirit as the rest of the product (a
    tropical storm below category 3 tops out at SEVERE, a green GDACS event
    barely registers): a probability is not a certainty, and a tracker that
    reads every double-digit percentage as EXTREME teaches its readers to
    ignore it.

    M6.0 and M7.0 are the same thresholds `severity_from_magnitude` uses to
    reach SEVERE and EXTREME respectively. The probability of reaching that
    magnitude must be substantial, not merely nonzero, to match the tier:

    - P(M>=7) >= 10%: a one-in-ten chance of a major, likely destructive
      aftershock is itself extreme news, however small the number looks.
    - P(M>=6) >= 30%: close to a one-in-three chance of a damaging
      aftershock (the real M7.7 Ende forecast, 37.8%, lands here).
    - P(M>=5) >= 50%: more likely than not to be felt, minor damage at most.
    - otherwise MINOR: a forecast exists at all only because the mainshock
      was significant enough to earn one, but nothing in it clears a real
      bar.
    """
    p7 = _bin_probability(bins, 7.0)
    if p7 is not None and p7 >= 0.10:
        return Severity.EXTREME
    p6 = _bin_probability(bins, 6.0)
    if p6 is not None and p6 >= 0.30:
        return Severity.SEVERE
    p5 = _bin_probability(bins, 5.0)
    if p5 is not None and p5 >= 0.50:
        return Severity.MODERATE
    return Severity.MINOR


def build_event(
    mainshock: Mainshock, forecast: Forecast, now: datetime | None = None
) -> Event | None:
    """Mainshock + forecast -> one Event, or None if there is nothing honest
    to say (expired, or no usable window/bin)."""
    now = now or utcnow()
    if now >= forecast.expire_time:
        return None

    window = select_headline_window(forecast)
    if window is None:
        return None
    bin_ = headline_bin(window["bins"])
    if bin_ is None:
        return None

    pct = round(bin_["probability"] * 100)
    phrase = WINDOW_PHRASE.get(window["label"], window["label"].lower())
    title = f"Aftershock forecast -- {pct}% chance of M{bin_['magnitude']:.0f}+ within {phrase}"

    return Event(
        id=f"aftershock:{mainshock.id}",
        source="aftershock",
        source_id=mainshock.id,
        kind=Kind.EARTHQUAKE,
        # the forecast's own creation time, not the mainshock's origin time:
        # this event is about the FORECAST, whose freshness is what matters
        time=forecast.creation_time,
        lat=mainshock.lat,
        lon=mainshock.lon,
        depth_km=mainshock.depth_km,
        place=mainshock.place,
        # no `magnitude`: this is not a reading of a quake, it is a
        # probability table about several possible future ones. Borrowing
        # the mainshock's own magnitude into this field would make the card
        # look like a second, separate quake of the same size.
        severity=aftershock_severity(window["bins"]),
        # exempts the event from the ingestion horizon for as long as the
        # forecast itself remains valid -- a 7-day forecast published 3 days
        # ago is still valid, even though the mainshock is not "recent" news
        # anymore. Once expireTime passes we simply stop emitting it (see
        # AftershockSource._cycle) and the store's own staleness sweep
        # removes it a few hours later, same as any other ongoing alert.
        ongoing=True,
        title=title,
        url=f"https://earthquake.usgs.gov/earthquakes/eventpage/{mainshock.id}/oaf/forecast",
        raw={
            "mainshock_magnitude": mainshock.mag,
            "mainshock_time": mainshock.time.isoformat(),
            "model": forecast.model_name,
            "advisory_time_frame": forecast.advisory_time_frame,
            "creation_time": forecast.creation_time.isoformat(),
            "expire_time": forecast.expire_time.isoformat(),
            "next_forecast_time": forecast.next_forecast_time.isoformat(),
            "observations": forecast.observations,
            "forecast": [
                {
                    "label": w["label"],
                    "time_start": w["time_start"].isoformat() if w["time_start"] else None,
                    "time_end": w["time_end"].isoformat() if w["time_end"] else None,
                    "bins": w["bins"],
                }
                for w in forecast.windows
            ],
        },
    )


class AftershockSource(Source):
    """USGS official aftershock forecasts (OAF).

    Not a per-second feed: a forecast is recomputed roughly once a day
    (`nextForecastTime`), so 600 s is already generous. Cost per cycle: one
    discovery request, plus two requests (detail + forecast.json) for each
    mainshock seen for the first time or due for a refresh -- everything
    else that cycle is served from the in-memory cache.
    """

    name = "aftershock"
    kind = "poll"

    def __init__(self, poll_seconds: float = 600.0, lookback_days: float = 7.0):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.lookback_days = lookback_days
        # mainshock id -> (Mainshock, Forecast), independent of the sliding
        # discovery window: once cached, a forecast keeps being served until
        # its own expireTime, even after its mainshock ages out of
        # `lookback_days`
        self._cache: dict[str, tuple[Mainshock, Forecast]] = {}

    def build_discovery_url(self, now: datetime | None = None) -> str:
        now = now or utcnow()
        start = now - timedelta(days=self.lookback_days)
        params = {
            "format": "geojson",
            "producttype": "oaf",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        return f"{DISCOVERY_URL}?{urlencode(params)}"

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    await self._cycle(client, emit)
                    self.health.ok(len(self._cache))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("%s: %s", self.name, exc)
                await asyncio.sleep(self.poll_seconds)

    async def _cycle(
        self, client: httpx.AsyncClient, emit: Emit, now: datetime | None = None
    ) -> None:
        now = now or utcnow()
        # an expired forecast is dropped outright rather than kept alive
        # with a stale table
        self._cache = {k: v for k, v in self._cache.items() if v[1].expire_time > now}

        resp = await client.get(self.build_discovery_url(now))
        resp.raise_for_status()
        mainshocks = parse_discovery(resp.json())

        for mainshock in mainshocks:
            cached = self._cache.get(mainshock.id)
            needs_fetch = cached is None or now >= cached[1].next_forecast_time
            if not needs_fetch:
                continue
            try:
                forecast = await self._fetch_forecast(client, mainshock.id)
            except Exception as exc:
                # a fetch failure for ONE mainshock must not cost us every
                # other one already cached, or the discovery cycle itself
                log.warning("%s: forecast fetch failed for %s: %s", self.name, mainshock.id, exc)
                continue
            if forecast is not None:
                self._cache[mainshock.id] = (mainshock, forecast)

        for cached_mainshock, cached_forecast in self._cache.values():
            event = build_event(cached_mainshock, cached_forecast, now)
            if event is not None:
                await emit(event)

    async def _fetch_forecast(self, client: httpx.AsyncClient, event_id: str) -> Forecast | None:
        detail_resp = await client.get(DETAIL_URL.format(event_id=event_id))
        detail_resp.raise_for_status()
        forecast_url = extract_forecast_url(detail_resp.json())
        if not forecast_url:
            return None
        forecast_resp = await client.get(forecast_url)
        forecast_resp.raise_for_status()
        return parse_forecast(forecast_resp.json())
