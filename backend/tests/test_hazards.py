"""NHC cyclone tests, on real payloads captured 2026-08-18 (Hurricane Lala,
Central Pacific, storm id `cp012026`, bin `CP2`, advisory 24).

Two live endpoints are involved and both fixtures below are verbatim excerpts:
- `CurrentStorms.json` (position, wind, category) -- `CURRENT_STORM`.
- the ArcGIS MapServer's forecast-points layer for CP2 (id 292, resolved by
  fetching `/MapServer/layers?f=json` and matching the name "CP2 Forecast
  Points") -- `FORECAST_POINTS_CP2` and `LAYERS_RESPONSE`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.models.event import Kind, Severity
from app.sources.hazards import (
    NHC_ARCGIS_ROOT,
    NhcSource,
    _resolve_valid_time,
    cyclone_severity,
    parse_forecast_track,
)

# --------------------------------------------------------------------- fixtures

CURRENT_STORM = {
    "id": "cp012026",
    "binNumber": "CP2",
    "name": "Lala",
    "classification": "HU",
    "intensity": "70",
    "pressure": "985",
    "latitude": "20.4N",
    "longitude": "166.1W",
    "latitudeNumeric": 20.4,
    "longitudeNumeric": -166.1,
    "movementDir": 275,
    "movementSpeed": 10,
    "lastUpdate": "2026-08-18T09:00:00.000Z",
    "publicAdvisory": {
        "advNum": "024",
        "issuance": "2026-08-18T09:00:00.000Z",
        "url": "https://www.nhc.noaa.gov/text/HFOTCPCP2.shtml",
    },
}

# three of the nine points in the real response: tau=0 (current fix),
# tau=12 (a mid-track point, with mslp already gone to the 9999 sentinel),
# tau=120 (the 5-day horizon). Feature order in the source is already
# ascending by tau; kept out of order here to prove parse_forecast_track
# sorts rather than trusting the feed's order.
FORECAST_POINTS_CP2 = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": 705,
            "geometry": {"type": "Point", "coordinates": [-172.10000000039963, 33.300000000100056]},
            "properties": {
                "stormname": "Hurricane Lala",
                "stormtype": "HU",
                "maxwind": 70,
                "ssnum": 1,
                "tau": 120,
                "validtime": "23/0600",
                "idp_filedate": 1787044373000,
                "binnumber": "CP2",
            },
        },
        {
            "type": "Feature",
            "id": 697,
            # geometry carries the real precision; properties.lat/lon (below)
            # are truncated to whole degrees and must NOT be used
            "geometry": {"type": "Point", "coordinates": [-166.09999999980008, 20.399999999800286]},
            "properties": {
                "stormname": "Hurricane Lala",
                "stormtype": "HU",
                "maxwind": 70,
                "mslp": 985,
                "ssnum": 1,
                "lat": 20,
                "lon": -166,
                "tau": 0,
                "validtime": "18/0600",
                "idp_filedate": 1787044373000,
                "binnumber": "CP2",
            },
        },
        {
            "type": "Feature",
            "id": 698,
            "geometry": {"type": "Point", "coordinates": [-167.49999999970015, 20.60000000029993]},
            "properties": {
                "stormname": "Hurricane Lala",
                "stormtype": "HU",
                "maxwind": 80,
                "mslp": 9999,  # sentinel for "not available" at this lead time
                "ssnum": 1,
                "tau": 12,
                "validtime": "18/1800",
                "idp_filedate": 1787044373000,
                "binnumber": "CP2",
            },
        },
    ],
}

# trimmed to the two fields parse uses (id, name); real response also carries
# parentLayerId/subLayerIds/scales which the layer resolver never reads
LAYERS_RESPONSE = {
    "layers": [
        {"id": 264, "name": "CP1"},
        {"id": 266, "name": "CP1 Forecast Points"},
        {"id": 290, "name": "CP2"},
        {"id": 291, "name": "CP2 Forecast Information"},
        {"id": 292, "name": "CP2 Forecast Points"},
        {"id": 293, "name": "CP2 Forecast Track"},
        {"id": 294, "name": "CP2 Forecast Cone"},
        {"id": 295, "name": "CP2 Watch-Warning"},
    ]
}


# ------------------------------------------------------------------- cyclone_severity


def test_cyclone_severity_major_hurricane_is_extreme():
    assert cyclone_severity(100.0, "HU") is Severity.EXTREME  # category 3+


def test_cyclone_severity_hurricane_below_major_is_severe():
    assert cyclone_severity(70.0, "HU") is Severity.SEVERE


def test_cyclone_severity_tropical_storm_is_moderate():
    assert cyclone_severity(45.0, "TS") is Severity.MODERATE


def test_cyclone_severity_depression_is_minor():
    assert cyclone_severity(25.0, "TD") is Severity.MINOR


# --------------------------------------------------------------------- parse_payload


def test_nhc_parse_payload_basic():
    events = NhcSource().parse_payload({"activeStorms": [CURRENT_STORM]})
    assert len(events) == 1
    event = events[0]
    # `id` (season-stable) is the key, never `binNumber` (recycled between storms)
    assert event.id == "nhc:cp012026"
    assert event.source_id == "cp012026"
    assert event.kind is Kind.CYCLONE
    # latitudeNumeric/longitudeNumeric, not the "20.4N" string form
    assert event.lat == 20.4
    assert event.lon == -166.1
    assert event.magnitude == 70.0
    assert event.severity is Severity.SEVERE
    assert event.ongoing is True
    assert event.raw["basin"] == "CP2"
    # no forecast attached yet: parse_payload never makes network calls
    assert "forecast_track" not in event.raw


def test_nhc_parse_payload_skips_storm_without_id():
    assert NhcSource().parse_payload({"activeStorms": [{"name": "no id"}]}) == []


def test_nhc_parse_payload_empty_feed():
    assert NhcSource().parse_payload({"activeStorms": []}) == []
    assert NhcSource().parse_payload({}) == []
    assert NhcSource().parse_payload(None) == []


# ---------------------------------------------------------------- parse_forecast_track


def test_forecast_track_sorted_by_tau():
    track = parse_forecast_track(FORECAST_POINTS_CP2)
    assert [p["tau"] for p in track] == [0, 12, 120]


def test_forecast_track_uses_geometry_not_truncated_properties():
    """TRAP verified on the live feed: properties.lat/lon round a 20.4N fix
    down to 20. Only geometry.coordinates carries the real precision."""
    track = parse_forecast_track(FORECAST_POINTS_CP2)
    tau0 = next(p for p in track if p["tau"] == 0)
    assert tau0["lat"] == pytest.approx(20.399999999800286)
    assert tau0["lon"] == pytest.approx(-166.09999999980008)
    assert tau0["lat"] != 20  # the truncated value from properties.lat


def test_forecast_track_fields():
    track = parse_forecast_track(FORECAST_POINTS_CP2)
    tau0 = next(p for p in track if p["tau"] == 0)
    assert tau0["wind_kt"] == 70.0
    assert tau0["category"] == 1
    # "18/0600" resolved against the package's own idp_filedate (18 Aug)
    assert tau0["valid"] == "2026-08-18T06:00:00+00:00"


def test_forecast_track_empty_layer_returns_empty_list():
    """A layer is empty (features: []) when no storm occupies that bin."""
    assert parse_forecast_track({"type": "FeatureCollection", "features": []}) == []
    assert parse_forecast_track({}) == []
    assert parse_forecast_track(None) == []


def test_forecast_track_skips_feature_without_tau_or_geometry():
    data = {
        "features": [
            {"geometry": {"coordinates": [1.0, 2.0]}, "properties": {}},  # no tau
            {"geometry": {}, "properties": {"tau": 24}},  # no coordinates
        ]
    }
    assert parse_forecast_track(data) == []


# ------------------------------------------------------------------- _resolve_valid_time


def test_resolve_valid_time_same_month():
    anchor = datetime(2026, 8, 18, 9, 12, 53, tzinfo=UTC)
    assert _resolve_valid_time("18/0600", anchor) == datetime(2026, 8, 18, 6, 0, tzinfo=UTC)


def test_resolve_valid_time_rolls_over_month_boundary():
    """A 5-day forecast issued on 2026-08-29 reaches day 2 -- which belongs
    to September, not to the anchor's own month."""
    anchor = datetime(2026, 8, 29, 15, 0, tzinfo=UTC)
    resolved = _resolve_valid_time("02/1200", anchor)
    assert resolved == datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def test_resolve_valid_time_rolls_over_year_boundary():
    anchor = datetime(2026, 12, 30, 6, 0, tzinfo=UTC)
    resolved = _resolve_valid_time("02/0000", anchor)
    assert resolved == datetime(2027, 1, 2, 0, 0, tzinfo=UTC)


def test_resolve_valid_time_garbage_is_none():
    anchor = datetime(2026, 8, 18, tzinfo=UTC)
    assert _resolve_valid_time(None, anchor) is None
    assert _resolve_valid_time("not a validtime", anchor) is None
    assert _resolve_valid_time("18/0600", None) is None


# --------------------------------------------------------- forecast attachment (async)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_attach_forecast_tracks_end_to_end():
    """Layers resolved once, forecast points fetched for the storm's bin,
    and the track lands under the SAME event's raw dict (never a new event)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/layers"):
            return httpx.Response(200, json=LAYERS_RESPONSE)
        if request.url.path.endswith("/292/query"):
            return httpx.Response(200, json=FORECAST_POINTS_CP2)
        return httpx.Response(404, json={})

    source = NhcSource()
    events = source.parse_payload({"activeStorms": [CURRENT_STORM]})
    async with _mock_client(handler) as client:
        await source._attach_forecast_tracks(client, events, {"activeStorms": [CURRENT_STORM]})

    assert len(events) == 1  # still one event: the forecast did not fork it
    track = events[0].raw["forecast_track"]
    assert [p["tau"] for p in track] == [0, 12, 120]
    assert any(u.endswith("/layers?f=json") or "/layers" in u for u in calls)
    assert any("/292/query" in u for u in calls)


@pytest.mark.asyncio
async def test_attach_forecast_tracks_layer_cache_is_reused():
    """Resolving layers costs one request per poll cycle at most: a second
    call with the same bin must not re-fetch the layer directory."""
    layer_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal layer_requests
        if request.url.path.endswith("/layers"):
            layer_requests += 1
            return httpx.Response(200, json=LAYERS_RESPONSE)
        if request.url.path.endswith("/292/query"):
            return httpx.Response(200, json=FORECAST_POINTS_CP2)
        return httpx.Response(404, json={})

    source = NhcSource()
    payload = {"activeStorms": [CURRENT_STORM]}
    async with _mock_client(handler) as client:
        events1 = source.parse_payload(payload)
        await source._attach_forecast_tracks(client, events1, payload)
        events2 = source.parse_payload(payload)
        await source._attach_forecast_tracks(client, events2, payload)

    assert layer_requests == 1
    assert "forecast_track" in events2[0].raw


@pytest.mark.asyncio
async def test_attach_forecast_tracks_failure_does_not_raise():
    """A supplementary detail must never make the main data fail: a network
    error while fetching the forecast must not propagate."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    source = NhcSource()
    events = source.parse_payload({"activeStorms": [CURRENT_STORM]})
    async with _mock_client(handler) as client:
        await source._attach_forecast_tracks(client, events, {"activeStorms": [CURRENT_STORM]})

    assert len(events) == 1
    assert "forecast_track" not in events[0].raw
    assert events[0].lat == 20.4  # the storm's current position is intact


@pytest.mark.asyncio
async def test_attach_forecast_tracks_run_still_emits_on_forecast_failure():
    """End-to-end through run(): CurrentStorms.json succeeds, the ArcGIS
    calls fail outright -- the storm event must still reach `emit`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == NhcSource.url:
            return httpx.Response(200, json={"activeStorms": [CURRENT_STORM]})
        raise httpx.ConnectError("connection refused", request=request)

    import asyncio

    source = NhcSource(poll_seconds=0.01)
    emitted = []

    async def emit(event):
        emitted.append(event)

    import httpx as httpx_module

    real_client_cls = httpx_module.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    import app.sources.hazards as hazards_module

    original = hazards_module.httpx.AsyncClient
    hazards_module.httpx.AsyncClient = fake_client
    try:
        task = asyncio.create_task(source.run(emit))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        hazards_module.httpx.AsyncClient = original

    assert len(emitted) >= 1
    assert emitted[0].id == "nhc:cp012026"
    assert "forecast_track" not in emitted[0].raw


def test_forecast_layer_root_matches_the_real_service():
    assert NHC_ARCGIS_ROOT == (
        "https://mapservices.weather.noaa.gov/tropical/rest/services/"
        "tropical/NHC_tropical_weather/MapServer"
    )
