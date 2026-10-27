"""People's places never reach the server log (app/core/privacy.py).

uvicorn logs every request path WITH its query string, and the v2 contract
puts a person's places there: `/ws?zones=lat,lon,radius`,
`/api/events?lat=&lon=&radius_km=`. Checked against a real uvicorn with its
own logging configuration, because that is what writes the production log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
import uvicorn
import websockets

from app.core import privacy
from app.core.privacy import redact_query

PARIS = ("48.8566", "2.3522")


class TestRedactQuery:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/ws", "/ws"),
            ("/ws?chunks=1", "/ws?chunks=1"),
            (
                "/ws?zones=48.8566,2.3522,50;35.67,139.65,300&unplaced=FR,JP&chunks=1",
                "/ws?zones=-&unplaced=-&chunks=1",
            ),
            (
                "/api/events?lat=48.8566&lon=2.3522&radius_km=50&since=ab12cd34:3",
                "/api/events?lat=-&lon=-&radius_km=-&since=ab12cd34:3",
            ),
            ("/api/geocode?q=Rue%20de%20Rivoli", "/api/geocode?q=-"),
            # a typo is refused by the API, but it carries a home all the same
            (
                "/api/events?zone=48.8,2.3,50&min_severity=severe",
                "/api/events?zone=-&min_severity=severe",
            ),
            ("/api/events?lat&&lon=", "/api/events?-&&lon=-"),  # malformed stays readable
            # the final review's cases (PR #5): refused by the API, logged all the same
            ("/ws?zones48.8566,2.3522,25", "/ws?-"),
            ("/ws?48.8566,2.3522,25", "/ws?-"),
            ("/ws?kinds=earthquake;zones=48.8566,2.3522,25", "/ws?kinds=-"),
            ("/ws?zones%3D48.85,2.35,25", "/ws?-"),
            # a safe name keeps only a value of its own shape
            (
                "/ws?chunks=1&tick=lite&kinds=earthquake,tsunami",
                "/ws?chunks=1&tick=lite&kinds=earthquake,tsunami",
            ),
            (
                "/api/events?limit=500&min_magnitude=4.5&since=ab12cd34:17",
                "/api/events?limit=500&min_magnitude=4.5&since=ab12cd34:17",
            ),
            (
                "/api/events?limit=48.8566,2.3522&min_severity=48.85",
                "/api/events?limit=-&min_severity=-",
            ),
            ("/ws?chunks", "/ws?chunks"),  # a bare safe name is a flag, kept
            # the recheck at 0cc11f6: the NAME carried the place
            ("/ws?48.8566,2.3522,25=", "/ws?-"),
            ("/ws?zones%3D48.8566%2C2.3522%2C25=1", "/ws?-"),
            ("/api/events?48.8566=2.3522", "/api/events?-"),
            # ... and free text or a coordinate inside a safe name's alphabet
            ("/api/guidance?lang=Montmartre", "/api/guidance?lang=-"),
            ("/api/events?min_severity=Rue_de_Rivoli,Paris", "/api/events?min_severity=-"),
            ("/api/events?hours=48.857&limit=2.352", "/api/events?hours=-&limit=-"),
            ("/ws?tick=full&chunks=2", "/ws?tick=-&chunks=-"),
            # the real values stay readable, a negative magnitude included
            (
                "/api/events?min_magnitude=-1&hours=24.0&limit=500&primary_only=false",
                "/api/events?min_magnitude=-1&hours=24.0&limit=500&primary_only=false",
            ),
            (
                "/ws?kinds=earthquake,space_weather&min_severity=severe&tick=lite",
                "/ws?kinds=earthquake,space_weather&min_severity=severe&tick=lite",
            ),
            ("/api/guidance?lang=fr", "/api/guidance?lang=fr"),
            ("/api/events?%6Cat=48.8", "/api/events?%6Cat=-"),  # an encoded name is still `lat`
        ],
    )
    def test_values_go_names_stay(self, path, expected):
        assert redact_query(path) == expected

    def test_install_is_idempotent(self):
        privacy.install()
        privacy.install()
        for name in privacy.LOGGERS:
            filters = logging.getLogger(name).filters
            assert sum(isinstance(f, privacy.RedactPlaces) for f in filters) == 1


@pytest.fixture
def logged_server() -> Iterator[tuple[str, list[str]]]:
    from app import main

    config = uvicorn.Config(main.app, host="127.0.0.1", port=0, lifespan="off", log_level="info")
    srv = uvicorn.Server(config)
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    capture = Capture()
    for name in privacy.LOGGERS:
        logging.getLogger(name).addHandler(capture)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    port = srv.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"127.0.0.1:{port}", lines
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        for name in privacy.LOGGERS:
            logging.getLogger(name).removeHandler(capture)


def test_the_real_server_logs_no_place(logged_server):
    host, lines = logged_server
    url = f"http://{host}/api/events?lat={PARIS[0]}&lon={PARIS[1]}&radius_km=50"
    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.status == 200
    with urllib.request.urlopen(f"http://{host}/api/version?q=Montmartre", timeout=10):
        pass

    async def socket() -> None:
        zoned = f"ws://{host}/ws?zones={PARIS[0]},{PARIS[1]},50&unplaced=FR"
        async with websockets.connect(zoned) as ws:
            assert json.loads(await ws.recv())["type"] == "snapshot"

    asyncio.run(socket())
    time.sleep(0.2)  # the access line is written after the response

    text = "\n".join(lines)
    assert '"GET /api/events?lat=-&lon=-&radius_km=- HTTP/1.1" 200' in text, text
    assert '"WebSocket /ws?zones=-&unplaced=-" [accepted]' in text, text
    for secret in (*PARIS, "Montmartre", "unplaced=FR"):
        assert secret not in text, f"{secret!r} reached the log"


def test_the_real_server_logs_no_place_in_a_name_or_a_safe_value(logged_server):
    """The recheck's requests, through uvicorn's own logging: a place in a
    parameter NAME, free text in a safe name, a coordinate in two numbers."""
    host, lines = logged_server
    for path in (
        "/api/events?48.8566=2.3522",
        "/api/guidance?lang=Montmartre",
        "/api/events?min_severity=Rue_de_Rivoli,Paris",
        "/api/events?hours=48.857&limit=2.352",
        "/api/events?min_magnitude=-1&limit=500",
    ):
        # a 422 is logged all the same, which is the point
        with contextlib.suppress(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://{host}{path}", timeout=10).close()

    async def sockets() -> None:
        for query in ("48.8566,2.3522,25=", "zones%3D48.8566%2C2.3522%2C25=1"):
            async with websockets.connect(f"ws://{host}/ws?{query}") as ws:
                assert json.loads(await ws.recv())["type"] == "error"

    asyncio.run(sockets())
    time.sleep(0.2)
    text = "\n".join(lines)
    for secret in ("48.8566", "2.3522", "48.857", "Montmartre", "Rivoli"):
        assert secret not in text, f"{secret!r} reached the log:\n{text}"
    assert "min_magnitude=-1&limit=500" in text, "a real value must stay readable"
    assert '"WebSocket /ws?-" [accepted]' in text, text


# ------------------------------------------------- the app's own log lines
#
# Final review of PR #5: the uvicorn lines were clean, but the place still
# reached the log through three other doors.


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.name}: {record.getMessage()}")


@pytest.fixture
def root_log() -> Iterator[Capture]:
    """Everything any logger writes, as the root handler would print it."""
    capture = Capture()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    privacy.install()
    try:
        yield capture
    finally:
        root.removeHandler(capture)
        root.setLevel(old_level)


SEARCH = "12 rue de Rivoli, Paris"


def nominatim(status: int):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json=[{"display_name": SEARCH, "lat": "48.86", "lon": "2.34"}])

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("status", [200, 503])
async def test_a_geocode_search_leaves_no_place_in_the_log(root_log, monkeypatch, status):
    """httpx logs every request URL at INFO, and an HTTPStatusError quotes
    it: both held the searched address next to the access line's client."""
    from app import geocode

    monkeypatch.setattr(geocode, "_transport", nominatim(status))
    monkeypatch.setattr(geocode, "_MIN_INTERVAL", 0.0)
    geocode._cache.clear()
    await geocode.search(SEARCH)
    text = "\n".join(root_log.lines)
    assert "nominatim.openstreetmap.org/search?q=-" in text, text  # the line is still there
    for secret in ("Rivoli", "rue", "Paris"):
        assert secret not in text, f"{secret!r} reached the log: {text}"


def test_a_refused_socket_logs_its_code_not_the_rejected_zone(root_log, v2):
    from fastapi.testclient import TestClient

    client = TestClient(v2.main.app)
    for query in ("zones=48.8566,2.3522", "kinds=earthquake;zones=48.8566,2.3522,25"):
        with client.websocket_connect(f"/ws?{query}") as ws:
            assert ws.receive_json()["type"] == "error"
    text = "\n".join(root_log.lines)
    assert "websocket refused: bad_zones" in text and "websocket refused: bad_kinds" in text
    assert "48.8566" not in text and "2.3522" not in text, text
