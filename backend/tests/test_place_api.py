"""GET /api/place: the strict query, the client budget, the report budget,
single flight, ETag / 304, the host queue, and what the logs never hold."""

from __future__ import annotations

import asyncio
import gc
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app import place as P
from app.core import privacy
from app.geocode import AddressBudget
from app.place import compose, fetch
from tests.place_helpers import Router, install

COORD = ("9.51", "100.01", "61.2", "-149.9")


@pytest.fixture
def client() -> TestClient:
    install()
    main.place_budget = AddressBudget(burst=50, per_second=50)
    return TestClient(main.app)


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("", "bad_point"),
        ("lat=9.5", "bad_point"),
        ("lat=abc&lon=1", "bad_point"),
        ("lat=nan&lon=1", "bad_point"),
        ("lat=inf&lon=1", "bad_point"),
        ("lat=91&lon=1", "bad_point"),
        ("lat=1&lon=-180.5", "bad_point"),
        ("lat=1&lon=1&radius_km=5", "bad_radius"),
        ("lat=1&lon=1&radius_km=301", "bad_radius"),
        ("lat=1&lon=1&radius_km=x", "bad_radius"),
        ("lat=1&lon=1&lang=de", "bad_lang"),
        ("lat=1&lon=1&zone=1", "unknown_param"),
        ("lat=1&lat=2&lon=1", "unknown_param"),
    ],
)
def test_refusals(client: TestClient, query: str, code: str) -> None:
    r = client.get(f"/api/place?{query}")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == code
    assert set(r.json()["detail"]) == {"code", "reason"}


def test_disabled_answers_404_and_the_version_says_so(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert "place" in client.get("/api/version").json()["features"]
    monkeypatch.setattr(main.settings, "enable_place", False)
    r = client.get("/api/place?lat=1&lon=1")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "place_disabled"
    assert "place" not in client.get("/api/version").json()["features"]


def test_a_report_has_every_block_and_an_etag(client: TestClient) -> None:
    r = client.get("/api/place?lat=-0.001&lon=-160.004&lang=fr")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == {"lat": 0.0, "lon": -160.0, "radius_km": 300, "lang": "fr"}
    assert str(body["query"]["lat"]) == "0.0"  # never -0.0
    assert list(body["blocks"]) == list(P.BLOCK_IDS) and len(P.BLOCK_IDS) == 22
    assert sum(body["counts"].values()) == 22
    assert r.headers["cache-control"] == "no-cache" and r.headers["etag"].startswith('"')
    for block in body["blocks"].values():
        if block["status"] != "ok":
            assert block["data"] is None and block["as_of"] is None
            assert block["error"]["message"]  # localized
    again = client.get(
        "/api/place?lat=-0.001&lon=-160.004&lang=fr", headers={"If-None-Match": r.headers["etag"]}
    )
    assert again.status_code == 304 and again.headers["etag"] == r.headers["etag"]


def test_the_client_budget_is_spent_only_by_new_fetches(client: TestClient) -> None:
    main.place_budget = AddressBudget(burst=1, per_second=1e-6)
    first = client.get("/api/place?lat=10&lon=10")
    assert first.status_code == 200
    # a new place needs new fetches, and the token is gone
    refused = client.get("/api/place?lat=20&lon=20")
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert int(refused.headers["retry-after"]) >= 1
    # the first place again: every block cached or a remembered failure, free
    assert client.get("/api/place?lat=10&lon=10").status_code == 200


async def test_joining_a_fetch_in_flight_spends_no_token() -> None:
    router = Router()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(503)

    router.prefix("https://", slow)  # type: ignore[arg-type]
    install(router)
    spent: list[int] = []

    def take() -> float:
        spent.append(1)
        return 0.0

    def free() -> float:
        spent.append(2)
        return 0.0

    q = P.Query(9.51, 100.01, 300.0, "en")
    a = asyncio.create_task(P.report(q, lambda: ([], []), budget_s=5, take_token=take))
    await asyncio.sleep(0.05)
    b = asyncio.create_task(P.report(q, lambda: ([], []), budget_s=5, take_token=free))
    ra, rb = await asyncio.gather(a, b)
    assert ra.body is not None and rb.body is not None
    assert spent.count(1) == 1 and 2 not in spent
    met = [r for r in router.seen if r.url.host == "api.met.no"]
    assert len(met) == 1  # three blocks, one single-flight fetch


async def test_a_hung_upstream_returns_budget_and_the_rest_ok() -> None:
    router = Router()

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"{}")

    router.prefix("https://api.met.no/", hang)  # type: ignore[arg-type]
    install(router)
    q = P.Query(48.86, 2.35, 300.0, "en")
    result = await P.report(q, lambda: ([], []), budget_s=0.5)
    assert result.body is not None
    body = result.body
    assert body["complete"] is False and body["retry_after_s"] == 5
    for bid in ("forecast_hourly", "forecast_daily", "wind"):
        assert body["blocks"][bid]["error"]["code"] == "budget"
    # sea blocks are decided locally at Paris, without waiting for anyone
    assert body["blocks"]["sea.waves"]["status"] == "not_applicable"


def test_etag_ignores_server_time_and_fetched_at_but_not_stale() -> None:
    body = {
        "api": 2,
        "server_time": "2026-10-27T11:00:00Z",
        "complete": True,
        "blocks": {"x": {"status": "ok", "stale": False, "fetched_at": "2026-10-27T10:00:00Z"}},
    }
    later = {
        **body,
        "server_time": "2026-10-27T11:05:00Z",
        "blocks": {"x": {**body["blocks"]["x"], "fetched_at": "2026-10-27T11:04:00Z"}},
    }
    flipped = {**body, "blocks": {"x": {**body["blocks"]["x"], "stale": True}}}
    assert compose.etag(body) == compose.etag(later)
    assert compose.etag(body) != compose.etag(flipped)


async def test_a_full_host_queue_answers_busy_at_once() -> None:
    router = Router()
    release = asyncio.Event()

    async def held(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, content=b"{}")

    router.prefix("https://www.ngdc.noaa.gov/", held)  # type: ignore[arg-type]
    install(router)
    url = "https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1/earthquakes"
    tasks = [asyncio.create_task(fetch.get(url)) for _ in range(fetch.QUEUE_MAX + 1)]
    await asyncio.sleep(0.05)  # one in flight (cap 1), sixteen waiting
    with pytest.raises(fetch.FetchError) as raised:
        await fetch.get(url)
    assert raised.value.code == "busy"
    release.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_no_coordinate_or_place_path_in_any_log(caplog: pytest.LogCaptureFixture) -> None:
    privacy.install()
    router = Router()
    router.add("https://api.weather.gov/points/61.22,-149.90", b"{}", status=500)

    async def late(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.4)
        raise httpx.ConnectError("boom https://api.met.no/weatherapi/locationforecast?lat=61.22")

    router.prefix("https://api.met.no/", late)  # type: ignore[arg-type]
    install(router)
    caplog.set_level(logging.DEBUG)
    q = P.Query(61.218, -149.9, 300.0, "en")
    result = await P.report(q, lambda: ([], []), budget_s=0.1)
    assert result.body is not None
    await asyncio.sleep(0.6)  # the shielded MET fetch fails after the response
    gc.collect()
    await asyncio.sleep(0)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "HTTP Request: GET https://api.weather.gov " in text  # logged, host only
    for token in ("/points/", "lat=", "61.22", "-149.9", "never retrieved", "locationforecast"):
        assert token not in text, token
    # and the reason a client reads names the host, never the URL
    wind = result.body["blocks"]["wind"]
    assert "61.2" not in str(wind)


def test_the_httpx_filter_rewrites_url_objects_to_scheme_and_host() -> None:
    privacy.install()
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        'HTTP Request: %s %s "%s %d %s"',
        (
            "GET",
            httpx.URL("https://api.weather.gov/points/61.2180,-149.9000"),
            "HTTP/1.1",
            200,
            "OK",
        ),
        None,
    )
    for f in logging.getLogger("httpx").filters:
        f.filter(record)
    assert record.getMessage() == 'HTTP Request: GET https://api.weather.gov "HTTP/1.1 200 OK"'


def test_place_query_values_stay_out_of_the_access_log() -> None:
    assert (
        "lat" not in privacy.SAFE and "lon" not in privacy.SAFE and "radius_km" not in privacy.SAFE
    )
    assert privacy.redact_query("/api/place?lat=9.51&lon=100.01&radius_km=50&lang=fr") == (
        "/api/place?lat=-&lon=-&radius_km=-&lang=fr"
    )


def test_etag_holds_across_polls_of_unchanged_alerts() -> None:
    """Measured live: two warm reports 2 s apart had different ETags, because
    the live block's as_of is server_time and `last_seen` moves on every poll."""

    def body(as_of: str, last_seen: str, revision: int) -> dict:
        event = {"id": "usgs:x", "revision": revision, "updated_at": "u", "last_seen": last_seen}
        return {
            "server_time": as_of,
            "blocks": {
                "alerts_now": {
                    "status": "ok",
                    "as_of": as_of,
                    "as_of_kind": "live",
                    "stale": False,
                    "fetched_at": as_of,
                    "data": {"events": [event], "total": 1},
                }
            },
        }

    a = compose.etag(body("2026-10-27T14:51:56Z", "2026-10-27T14:51:50Z", 0))
    b = compose.etag(body("2026-10-27T14:51:58Z", "2026-10-27T14:51:57Z", 0))
    c = compose.etag(body("2026-10-27T14:51:58Z", "2026-10-27T14:51:57Z", 1))
    assert a == b and a != c
