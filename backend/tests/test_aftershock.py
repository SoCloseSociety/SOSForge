"""USGS aftershock forecast (OAF) tests, on real payloads captured 2026-08-18
against the live API, for the M7.7 near Ende, Indonesia (`us6000tkt2`) and
the M7.4 near San Jose del Palmar, Colombia (`us6000tjl2`).

Three live endpoints are involved and all fixtures below are verbatim
excerpts (properties not read by the parser are trimmed out, same as the
existing NHC fixtures in test_hazards.py):

- the discovery query (`producttype=oaf`) -- `DISCOVERY_RESPONSE`.
- the mainshock's detail feed, down to the single `oaf` product entry --
  `DETAIL_RESPONSE_TKT2`.
- the forecast itself -- `FORECAST_RESPONSE_TKT2` (per-bin `fractileValues`,
  a 100+ integer distribution not part of the displayed probability table,
  is the only thing dropped from each bin) and `FORECAST_RESPONSE_TJL2`
  (smaller mainshock, used to confirm the M6/M7 bins are still present but
  their probabilities are much lower).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.models.event import Kind, Severity
from app.sources.aftershock import (
    AftershockSource,
    Forecast,
    Mainshock,
    _bin_probability,
    aftershock_severity,
    build_event,
    extract_forecast_url,
    headline_bin,
    parse_discovery,
    parse_forecast,
    select_headline_window,
)

# --------------------------------------------------------------------- fixtures

DISCOVERY_RESPONSE = {
    "type": "FeatureCollection",
    "metadata": {
        "generated": 1787049688000,
        "url": "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&producttype=oaf&starttime=2026-08-10",
        "count": 2,
    },
    "features": [
        {
            "type": "Feature",
            "properties": {
                "mag": 7.7,
                "place": "68 km NNW of Ende, Indonesia",
                "time": 1786744701564,
                "title": "M 7.7 - 68 km NNW of Ende, Indonesia",
            },
            "geometry": {"type": "Point", "coordinates": [121.3517, -8.3101, 10]},
            "id": "us6000tkt2",
        },
        {
            "type": "Feature",
            "properties": {
                "mag": 7.4,
                "place": "5 km S of San José del Palmar, Colombia",
                "time": 1786365268125,
                "title": "M 7.4 - 5 km S of San José del Palmar, Colombia",
            },
            "geometry": {"type": "Point", "coordinates": [-76.2422, 4.8436, 110.285]},
            "id": "us6000tjl2",
        },
    ],
}

# trimmed to properties.products.oaf: the rest of a real detail feed (dyfi,
# losspager, shakemap, moment-tensor...) is never read by extract_forecast_url
DETAIL_RESPONSE_TKT2 = {
    "properties": {
        "products": {
            "oaf": [
                {
                    "id": "urn:usgs-product:us:oaf:us6000tkt2:1786993005429",
                    "type": "oaf",
                    "code": "us6000tkt2",
                    "updateTime": 1786993005429,
                    "status": "UPDATE",
                    "contents": {
                        "forecast.json": {
                            "contentType": "application/json",
                            "lastModified": 1786993005000,
                            "length": 29901,
                            "url": (
                                "https://earthquake.usgs.gov/pdl/products/"
                                "urn:usgs-product:us:oaf:us6000tkt2:1786993005429/"
                                "contents/forecast.json"
                            ),
                        },
                        "contents.xml": {
                            "contentType": "application/xml",
                            "lastModified": 1786993005000,
                            "length": 266,
                            "url": (
                                "https://earthquake.usgs.gov/pdl/products/"
                                "urn:usgs-product:us:oaf:us6000tkt2:1786993005429/"
                                "contents/contents.xml"
                            ),
                        },
                    },
                }
            ]
        }
    }
}

# per-bin fractileValues (100+ integers, the full distribution) trimmed: it
# is never read by parse_forecast and is not part of the displayed table
FORECAST_RESPONSE_TKT2 = {
    "creationTime": 1786992598229,
    "expireTime": 1818566201975,
    "advisoryTimeFrame": "1 Week",
    "nextForecastTime": 1787079631164,
    "model": {"name": "Epidemic-Type aftershock model (Bayesian Combination)"},
    "observations": [
        {"magnitude": 3.0, "count": 69},
        {"magnitude": 5.0, "count": 28},
        {"magnitude": 6.0, "count": 1},
        {"magnitude": 7.0, "count": 0},
    ],
    "forecast": [
        {
            "label": "1 Day",
            "timeStart": 1786993231164,
            "timeEnd": 1787079631164,
            "bins": [
                {"magnitude": 3.0, "probability": 0.9999, "p95minimum": 86, "p95maximum": 277},
                {"magnitude": 4.0, "probability": 0.9999, "p95minimum": 5, "p95maximum": 28},
                {"magnitude": 5.0, "probability": 0.6755, "p95minimum": 0, "p95maximum": 4},
                {"magnitude": 6.0, "probability": 0.1018, "p95minimum": 0, "p95maximum": 1},
                {"magnitude": 7.0, "probability": 0.0087, "p95minimum": 0, "p95maximum": 0},
            ],
        },
        {
            "label": "1 Week",
            "timeStart": 1786993231164,
            "timeEnd": 1787598031164,
            "bins": [
                {"magnitude": 3.0, "probability": 0.9999, "p95minimum": 381, "p95maximum": 1471},
                {"magnitude": 4.0, "probability": 0.9999, "p95minimum": 30, "p95maximum": 132},
                {"magnitude": 5.0, "probability": 0.9872, "p95minimum": 1, "p95maximum": 14},
                {"magnitude": 6.0, "probability": 0.3781, "p95minimum": 0, "p95maximum": 3},
                {"magnitude": 7.0, "probability": 0.0415, "p95minimum": 0, "p95maximum": 1},
            ],
        },
        {
            "label": "1 Month",
            "timeStart": 1786993231164,
            "timeEnd": 1789671631164,
            "bins": [
                {"magnitude": 3.0, "probability": 0.9999, "p95minimum": 783, "p95maximum": 4251},
                {"magnitude": 4.0, "probability": 0.9999, "p95minimum": 65, "p95maximum": 373},
                {"magnitude": 5.0, "probability": 0.9996, "p95minimum": 4, "p95maximum": 35},
                {"magnitude": 6.0, "probability": 0.6454, "p95minimum": 0, "p95maximum": 5},
                {"magnitude": 7.0, "probability": 0.0979, "p95minimum": 0, "p95maximum": 1},
            ],
        },
        {
            "label": "1 Year",
            "timeStart": 1786993231164,
            "timeEnd": 1818615631164,
            "bins": [
                {"magnitude": 3.0, "probability": 0.9999, "p95minimum": 1546, "p95maximum": 22795},
                {"magnitude": 4.0, "probability": 0.9999, "p95minimum": 130, "p95maximum": 1969},
                {"magnitude": 5.0, "probability": 0.9997, "p95minimum": 9, "p95maximum": 178},
                {"magnitude": 6.0, "probability": 0.8942, "p95minimum": 0, "p95maximum": 17},
                {"magnitude": 7.0, "probability": 0.2574, "p95minimum": 0, "p95maximum": 3},
            ],
        },
    ],
}

# same shape, the smaller M7.4 Colombia mainshock: M6/M7 bins are present
# but far less likely -- confirms the parser does not hardcode Ende's numbers
FORECAST_RESPONSE_TJL2 = {
    "creationTime": 1786981969469,
    "expireTime": 1818555573215,
    "advisoryTimeFrame": "1 Week",
    "nextForecastTime": 1787120983005,
    "model": {"name": "Epidemic-Type aftershock model (Bayesian Combination)"},
    "observations": [
        {"magnitude": 3.0, "count": 3},
        {"magnitude": 5.0, "count": 1},
        {"magnitude": 6.0, "count": 0},
        {"magnitude": 7.0, "count": 0},
    ],
    "forecast": [
        {
            "label": "1 Week",
            "timeStart": 1786982400000,
            "timeEnd": 1787587200000,
            "bins": [
                {"magnitude": 3.0, "probability": 0.993, "p95minimum": 12, "p95maximum": 140},
                {"magnitude": 4.0, "probability": 0.5577, "p95minimum": 1, "p95maximum": 18},
                {"magnitude": 5.0, "probability": 0.0842, "p95minimum": 0, "p95maximum": 2},
                {"magnitude": 6.0, "probability": 0.0127, "p95minimum": 0, "p95maximum": 0},
                {"magnitude": 7.0, "probability": 0.0018, "p95minimum": 0, "p95maximum": 0},
            ],
        }
    ],
}


# ------------------------------------------------------------------- parse_discovery


def test_parse_discovery_basic():
    mainshocks = parse_discovery(DISCOVERY_RESPONSE)
    assert len(mainshocks) == 2
    ende = mainshocks[0]
    assert ende.id == "us6000tkt2"
    assert ende.place == "68 km NNW of Ende, Indonesia"
    assert ende.mag == 7.7
    # GeoJSON order is [lon, lat, depth]
    assert ende.lon == 121.3517
    assert ende.lat == -8.3101
    assert ende.depth_km == 10
    assert ende.time == datetime.fromtimestamp(1786744701564 / 1000.0, tz=UTC)


def test_parse_discovery_skips_feature_without_id_or_time():
    data = {
        "features": [
            {"properties": {"place": "no id"}, "geometry": {"coordinates": [1, 2]}},
            {"id": "no-time", "properties": {}, "geometry": {"coordinates": [1, 2]}},
        ]
    }
    assert parse_discovery(data) == []


def test_parse_discovery_empty_feed():
    assert parse_discovery({"features": []}) == []
    assert parse_discovery({}) == []
    assert parse_discovery(None) == []


# --------------------------------------------------------------- extract_forecast_url


def test_extract_forecast_url_basic():
    url = extract_forecast_url(DETAIL_RESPONSE_TKT2)
    assert url == (
        "https://earthquake.usgs.gov/pdl/products/"
        "urn:usgs-product:us:oaf:us6000tkt2:1786993005429/contents/forecast.json"
    )


def test_extract_forecast_url_no_oaf_product():
    assert extract_forecast_url({"properties": {"products": {}}}) is None
    assert extract_forecast_url({"properties": {"products": {"oaf": []}}}) is None
    assert extract_forecast_url({}) is None
    assert extract_forecast_url(None) is None


def test_extract_forecast_url_missing_forecast_json_content():
    data = {"properties": {"products": {"oaf": [{"contents": {"contents.xml": {"url": "x"}}}]}}}
    assert extract_forecast_url(data) is None


# ------------------------------------------------------------------------- parse_forecast


def test_parse_forecast_basic():
    forecast = parse_forecast(FORECAST_RESPONSE_TKT2)
    assert forecast is not None
    assert forecast.creation_time == datetime.fromtimestamp(1786992598229 / 1000.0, tz=UTC)
    assert forecast.expire_time == datetime.fromtimestamp(1818566201975 / 1000.0, tz=UTC)
    assert forecast.next_forecast_time == datetime.fromtimestamp(1787079631164 / 1000.0, tz=UTC)
    assert forecast.advisory_time_frame == "1 Week"
    assert forecast.model_name == "Epidemic-Type aftershock model (Bayesian Combination)"
    assert len(forecast.observations) == 4
    assert [w["label"] for w in forecast.windows] == ["1 Day", "1 Week", "1 Month", "1 Year"]
    # fractileValues is dropped, the four fields the product displays remain
    week = next(w for w in forecast.windows if w["label"] == "1 Week")
    m6_bin = next(b for b in week["bins"] if b["magnitude"] == 6.0)
    assert set(m6_bin) == {"magnitude", "probability", "p95minimum", "p95maximum"}
    assert m6_bin["probability"] == 0.3781


def test_parse_forecast_missing_expire_time_is_none():
    data = {k: v for k, v in FORECAST_RESPONSE_TKT2.items() if k != "expireTime"}
    assert parse_forecast(data) is None


def test_parse_forecast_missing_next_forecast_time_is_none():
    data = {k: v for k, v in FORECAST_RESPONSE_TKT2.items() if k != "nextForecastTime"}
    assert parse_forecast(data) is None


def test_parse_forecast_missing_creation_time_is_none():
    data = {k: v for k, v in FORECAST_RESPONSE_TKT2.items() if k != "creationTime"}
    assert parse_forecast(data) is None


def test_parse_forecast_no_usable_window_is_none():
    data = {**FORECAST_RESPONSE_TKT2, "forecast": [{"label": "1 Week", "bins": []}]}
    assert parse_forecast(data) is None


def test_parse_forecast_empty_or_none():
    assert parse_forecast({}) is None
    assert parse_forecast(None) is None


# --------------------------------------------------------------- select_headline_window


def test_select_headline_window_matches_advisory_time_frame():
    forecast = parse_forecast(FORECAST_RESPONSE_TKT2)
    window = select_headline_window(forecast)
    assert window["label"] == "1 Week"


def test_select_headline_window_falls_back_to_one_week():
    forecast = parse_forecast(FORECAST_RESPONSE_TKT2)
    forecast.advisory_time_frame = "does not exist"
    window = select_headline_window(forecast)
    assert window["label"] == "1 Week"


def test_select_headline_window_falls_back_to_first_window():
    forecast = Forecast(
        creation_time=datetime.now(UTC),
        expire_time=datetime.now(UTC),
        next_forecast_time=datetime.now(UTC),
        advisory_time_frame="nope",
        model_name=None,
        observations=[],
        windows=[{"label": "1 Month", "time_start": None, "time_end": None, "bins": []}],
    )
    assert select_headline_window(forecast)["label"] == "1 Month"


def test_select_headline_window_no_windows_is_none():
    forecast = Forecast(
        creation_time=datetime.now(UTC),
        expire_time=datetime.now(UTC),
        next_forecast_time=datetime.now(UTC),
        advisory_time_frame=None,
        model_name=None,
        observations=[],
        windows=[],
    )
    assert select_headline_window(forecast) is None


# ------------------------------------------------------------------------- headline_bin


def test_headline_bin_picks_magnitude_6():
    forecast = parse_forecast(FORECAST_RESPONSE_TKT2)
    week = select_headline_window(forecast)
    assert headline_bin(week["bins"])["magnitude"] == 6.0


def test_headline_bin_falls_back_to_nearest_available():
    bins = [{"magnitude": 3.0, "probability": 0.9}, {"magnitude": 4.0, "probability": 0.5}]
    assert headline_bin(bins)["magnitude"] == 4.0


def test_headline_bin_empty_is_none():
    assert headline_bin([]) is None


# --------------------------------------------------------------------- aftershock_severity


def test_aftershock_severity_ende_one_week_is_severe():
    """The real M7.7 Ende forecast: P(M>=6) = 37.8%, well past the 30% bar,
    P(M>=7) = 4.15%, well short of the 10% EXTREME bar."""
    forecast = parse_forecast(FORECAST_RESPONSE_TKT2)
    week = select_headline_window(forecast)
    assert aftershock_severity(week["bins"]) is Severity.SEVERE


def test_aftershock_severity_colombia_one_week_is_minor():
    """The real M7.4 Colombia forecast: P(M>=5) = 8.4%, short of every bar."""
    forecast = parse_forecast(FORECAST_RESPONSE_TJL2)
    week = select_headline_window(forecast)
    assert aftershock_severity(week["bins"]) is Severity.MINOR


def test_aftershock_severity_extreme_threshold():
    bins = [{"magnitude": 7.0, "probability": 0.10}]
    assert aftershock_severity(bins) is Severity.EXTREME
    bins = [{"magnitude": 7.0, "probability": 0.0999}]
    assert aftershock_severity(bins) is not Severity.EXTREME


def test_aftershock_severity_moderate_threshold():
    bins = [{"magnitude": 5.0, "probability": 0.50}]
    assert aftershock_severity(bins) is Severity.MODERATE


def test_aftershock_severity_no_matching_bins_is_minor():
    assert aftershock_severity([{"magnitude": 3.0, "probability": 0.99}]) is Severity.MINOR
    assert aftershock_severity([]) is Severity.MINOR


# ------------------------------------------------------------------------- _bin_probability


def test_bin_probability_exact_and_tolerant_match():
    bins = [{"magnitude": 6.0, "probability": 0.3781}]
    assert _bin_probability(bins, 6.0) == 0.3781
    assert _bin_probability(bins, 5.96) == 0.3781  # within tolerance
    assert _bin_probability(bins, 5.0) is None  # not within tolerance


# ----------------------------------------------------------------------------- build_event


def _ende_mainshock() -> Mainshock:
    return parse_discovery(DISCOVERY_RESPONSE)[0]


def test_build_event_matches_the_worked_example():
    """Reproduces the exact case in the task brief: "38% chance of a M6+
    within 7 days" for the real M7.7 near Ende, Indonesia."""
    mainshock = _ende_mainshock()
    forecast = parse_forecast(FORECAST_RESPONSE_TKT2)
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)  # well before expireTime (2027)

    event = build_event(mainshock, forecast, now)

    assert event is not None
    assert event.id == "aftershock:us6000tkt2"
    assert event.source == "aftershock"
    assert event.source_id == "us6000tkt2"
    assert event.kind is Kind.EARTHQUAKE
    assert event.lat == -8.3101
    assert event.lon == 121.3517
    assert event.place == "68 km NNW of Ende, Indonesia"
    assert event.magnitude is None  # never borrows the mainshock's own magnitude
    assert event.ongoing is True
    assert event.severity is Severity.SEVERE
    assert event.title == "Aftershock forecast -- 38% chance of M6+ within 7 days"
    assert event.url == "https://earthquake.usgs.gov/earthquakes/eventpage/us6000tkt2/oaf/forecast"
    assert event.raw["mainshock_magnitude"] == 7.7
    assert event.raw["advisory_time_frame"] == "1 Week"
    assert len(event.raw["forecast"]) == 4
    assert event.raw["observations"] == FORECAST_RESPONSE_TKT2["observations"]


def test_build_event_colombia_is_less_urgent():
    mainshock = parse_discovery(DISCOVERY_RESPONSE)[1]
    forecast = parse_forecast(FORECAST_RESPONSE_TJL2)
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    event = build_event(mainshock, forecast, now)

    assert event is not None
    assert event.severity is Severity.MINOR
    # headline is always the bin nearest M6.0, and 6.0 is present exactly:
    # a low 1% here (vs Ende's 38%) is the whole point of this fixture
    assert event.title == "Aftershock forecast -- 1% chance of M6+ within 7 days"


def test_build_event_expired_forecast_is_none():
    mainshock = _ende_mainshock()
    forecast = parse_forecast(FORECAST_RESPONSE_TKT2)
    after_expiry = datetime.fromtimestamp(
        FORECAST_RESPONSE_TKT2["expireTime"] / 1000.0, tz=UTC
    ) + timedelta(seconds=1)

    assert build_event(mainshock, forecast, after_expiry) is None


def test_build_event_no_windows_is_none():
    mainshock = _ende_mainshock()
    forecast = Forecast(
        creation_time=datetime.now(UTC),
        expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        next_forecast_time=datetime.now(UTC),
        advisory_time_frame=None,
        model_name=None,
        observations=[],
        windows=[],
    )
    assert build_event(mainshock, forecast) is None


# ------------------------------------------------------------------- AftershockSource


def test_build_discovery_url_uses_lookback_window():
    source = AftershockSource(lookback_days=7.0)
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    url = source.build_discovery_url(now)
    assert url.startswith("https://earthquake.usgs.gov/fdsnws/event/1/query?")
    assert "producttype=oaf" in url
    assert "format=geojson" in url
    assert "starttime=2026-08-11T12%3A00%3A00" in url


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _route(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "producttype=oaf" in url:
        return httpx.Response(200, json=DISCOVERY_RESPONSE)
    if url == DETAIL_URL_TKT2:
        return httpx.Response(200, json=DETAIL_RESPONSE_TKT2)
    if url == DETAIL_URL_TJL2:
        return httpx.Response(200, json=DETAIL_RESPONSE_TJL2)
    if "us6000tkt2" in url and "forecast.json" in url:
        return httpx.Response(200, json=FORECAST_RESPONSE_TKT2)
    if "us6000tjl2" in url and "forecast.json" in url:
        return httpx.Response(200, json=FORECAST_RESPONSE_TJL2)
    return httpx.Response(404, json={})


DETAIL_URL_TKT2 = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/us6000tkt2.geojson"
DETAIL_URL_TJL2 = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/us6000tjl2.geojson"

# minimal detail response for the Colombia mainshock, same shape as TKT2
DETAIL_RESPONSE_TJL2 = {
    "properties": {
        "products": {
            "oaf": [
                {
                    "contents": {
                        "forecast.json": {
                            "url": (
                                "https://earthquake.usgs.gov/pdl/products/"
                                "urn:usgs-product:us:oaf:us6000tjl2:1786982494056/"
                                "contents/forecast.json"
                            )
                        }
                    }
                }
            ]
        }
    }
}


@pytest.mark.asyncio
async def test_cycle_end_to_end_emits_both_forecasts():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _route(request)

    source = AftershockSource()
    emitted = []

    async def emit(event):
        emitted.append(event)

    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)  # after both creationTimes, well before expiry
    async with _mock_client(handler) as client:
        await source._cycle(client, emit, now)

    assert len(emitted) == 2
    assert {e.source_id for e in emitted} == {"us6000tkt2", "us6000tjl2"}
    # discovery (1) + detail+forecast for each of the two new mainshocks (4)
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_cycle_second_run_does_not_refetch_before_next_forecast_time():
    """The whole point of the cache: nextForecastTime for both fixtures is
    about a day out, so a second cycle a moment later must cost exactly the
    one discovery request."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _route(request)

    source = AftershockSource()
    emitted = []

    async def emit(event):
        emitted.append(event)

    # both fixtures' nextForecastTime is 2026-08-18T19:00 and 2026-08-19T06:29:
    # both "now"s below stay well before either
    now1 = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    now2 = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
    async with _mock_client(handler) as client:
        await source._cycle(client, emit, now1)
        first_call_count = len(calls)
        await source._cycle(client, emit, now2)

    assert len(emitted) == 4  # two events, twice
    assert len(calls) == first_call_count + 1  # only the second discovery call


@pytest.mark.asyncio
async def test_cycle_forecast_fetch_failure_does_not_break_the_cycle():
    """One mainshock's detail/forecast fetch failing must not cost us the
    other mainshock, which reproduces NHC's own supplementary-fetch pattern."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "producttype=oaf" in url:
            return httpx.Response(200, json=DISCOVERY_RESPONSE)
        if url == DETAIL_URL_TKT2:
            raise httpx.ConnectError("connection refused", request=request)
        return _route(request)

    source = AftershockSource()
    emitted = []

    async def emit(event):
        emitted.append(event)

    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    async with _mock_client(handler) as client:
        await source._cycle(client, emit, now)

    assert len(emitted) == 1
    assert emitted[0].source_id == "us6000tjl2"


@pytest.mark.asyncio
async def test_cycle_drops_expired_forecast_from_cache():
    """Once expireTime has passed, a cached forecast is dropped even if the
    mainshock is still (re)discovered -- it must not keep serving a stale
    table just because the discovery query still lists the mainshock."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _route(request)

    source = AftershockSource()
    emitted = []

    async def emit(event):
        emitted.append(event)

    async with _mock_client(handler) as client:
        await source._cycle(client, emit)
    assert len(source._cache) == 2

    # force both cached forecasts into the past, but keep next_forecast_time
    # in the future so the cache-drop path is exercised, not a refetch
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    for mainshock_id, (mainshock, forecast) in list(source._cache.items()):
        forecast.expire_time = datetime(2000, 1, 1, tzinfo=UTC)
        forecast.next_forecast_time = far_future
        source._cache[mainshock_id] = (mainshock, forecast)

    # discovery no longer lists either mainshock (fallen out of the window)
    def handler_no_discovery(request: httpx.Request) -> httpx.Response:
        if "producttype=oaf" in str(request.url):
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        return _route(request)

    emitted.clear()
    async with _mock_client(handler_no_discovery) as client:
        await source._cycle(client, emit)

    assert source._cache == {}
    assert emitted == []
