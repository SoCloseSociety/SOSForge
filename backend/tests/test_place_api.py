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


# ------------------------------------------- stale copies and warming blocks


async def test_a_stale_global_copy_makes_the_report_incomplete_and_spends_nothing() -> None:
    """Measured live: the first view after an idle spell showed VTSM 1400Z
    as "now" with `complete: true` while AWC already had 1500Z."""
    from app.place import weather
    from tests.place_helpers import at, ctx_for, read_bytes

    router = install()
    gz = read_bytes("weather", "awc_metars_cache_global.csv.gz")
    release = asyncio.Event()
    calls = {"n": 0}

    async def answer(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:
            await release.wait()  # the refresh stays in flight
        return httpx.Response(200, content=gz)

    router.handler(weather.METAR_URL, answer)  # type: ignore[arg-type]
    now = at("2026-10-27T11:09:26Z")
    builders = [(weather.SPEC_NOW, weather.build_now)]
    blocks, complete = await compose.run(ctx_for(9.512, 100.013, now), builders, 5)
    assert complete and blocks["weather_now"].status == "ok"
    entry = weather._metar_cache.peek("global")
    assert entry is not None
    entry.fresh_until = 0.0  # the 5-minute TTL has run out
    empty = ctx_for(9.512, 100.013, now, take=lambda: 7.0)  # this client's budget is empty
    blocks, complete = await compose.run(empty, builders, 5)
    # the previous copy, at once, and the report says "ask again"
    assert blocks["weather_now"].status == "ok" and complete is False
    assert empty.claimed is None  # the refresh is the product's: no token, no 429
    body = compose.body(empty, blocks, complete)
    assert body["retry_after_s"] == compose.RETRY_AFTER_S
    release.set()
    await asyncio.sleep(0.05)
    blocks, complete = await compose.run(ctx_for(9.512, 100.013, now), builders, 5)
    assert complete is True and calls["n"] == 2


async def test_a_block_that_finished_warming_makes_the_report_incomplete(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.place import cyclones
    from tests.place_helpers import at, ctx_for

    router = install()
    release = asyncio.Event()

    async def held(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(503)

    router.handler(cyclones.URL, held)  # type: ignore[arg-type]
    cyclones.configure(tmp_path)
    ctx = ctx_for(9.512, 100.013, at("2026-10-27T15:05:00Z"))
    blocks, complete = await compose.run(ctx, [(cyclones.SPEC, cyclones.build)], 2)
    block = blocks["history.cyclones"]
    assert block.status == "unavailable" and block.error is not None
    assert block.error.code == "warming"
    assert complete is False  # the panel comes back for it
    release.set()
    task = cyclones._state.task
    if task is not None:
        await task


# ------------------------------------------------- the route's two headers


def test_a_304_carries_the_server_clock_of_its_own_answer(client: TestClient) -> None:
    import time

    first = client.get("/api/place?lat=-0.001&lon=-160.004")
    assert first.status_code == 200
    assert first.headers["server-time"] == first.json()["server_time"]
    time.sleep(1.1)  # whole seconds on the wire
    again = client.get(
        "/api/place?lat=-0.001&lon=-160.004", headers={"If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304
    # the body a client kept says the OLD time; the header says now
    assert again.headers["server-time"] > first.json()["server_time"]


def test_ipv6_addresses_are_kept_by_their_64() -> None:
    from app.geocode import address_key

    assert address_key("2001:db8:aa:bb:1:2:3:4") == "2001:db8:aa:bb::/64"
    assert address_key("2001:db8:aa:bb:ffff::9") == "2001:db8:aa:bb::/64"
    assert address_key("2001:db8:aa:bc::1") == "2001:db8:aa:bc::/64"
    assert address_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert address_key("203.0.113.7") == "203.0.113.7"
    assert address_key("testclient") == "testclient" and address_key(None) is None


def test_rotating_inside_one_64_shares_one_place_budget() -> None:
    install()
    main.place_budget = AddressBudget(burst=1, per_second=1e-6)
    one = TestClient(main.app, client=("2001:db8:aa:bb::1", 50000))
    two = TestClient(main.app, client=("2001:db8:aa:bb:9:9:9:9", 50000))
    other = TestClient(main.app, client=("2001:db8:aa:bc::1", 50000))
    assert one.get("/api/place?lat=10&lon=10").status_code == 200
    assert two.get("/api/place?lat=20&lon=20").status_code == 429  # same subscriber
    assert other.get("/api/place?lat=30&lon=30").status_code == 200  # another /64


# ------------------------------------------------ the decode pool, busy


async def test_a_busy_decode_pool_never_holds_a_report_past_its_budget() -> None:
    """Measured before: 4 busy decode threads held a fully cached report
    2.96 s on a 0.5 s budget (place_of queued behind them)."""
    import time

    install()
    q = P.Query(48.86, 2.35, 300.0, "en")
    await P.report(q, lambda: ([], []), budget_s=2)  # every block cached or remembered
    hogs = [asyncio.ensure_future(fetch.in_thread(time.sleep, 3)) for _ in range(4)]
    await asyncio.sleep(0.05)
    started = time.monotonic()
    result = await P.report(q, lambda: ([], []), budget_s=0.5)
    took = time.monotonic() - started
    await asyncio.gather(*hogs)
    assert result.body is not None and took < 2.0, took


async def test_an_om_read_does_not_wait_for_the_decode_pool() -> None:
    import time

    from app.place import om
    from tests.place_helpers import FIXTURES, at, ctx_for

    router = install()
    router.cassette(FIXTURES / "air" / "openmeteo_s3_cams_global_kosamui")
    ctx = ctx_for(9.512, 100.013, at("2026-10-27T10:40:29Z"))
    hour = ctx.now.replace(minute=0, second=0, microsecond=0)
    hogs = [asyncio.ensure_future(fetch.in_thread(time.sleep, 3)) for _ in range(4)]
    await asyncio.sleep(0.05)
    started = time.monotonic()
    series = await om.series(ctx, "cams_global", ["pm2_5"], hour, hour)
    took = time.monotonic() - started
    await asyncio.gather(*hogs)
    assert round(series.values["pm2_5"][0] or -1, 1) == 6.8
    assert took < 2.0, took  # the readers have their own threads
