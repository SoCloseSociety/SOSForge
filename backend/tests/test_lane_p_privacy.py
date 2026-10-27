"""People's places never reach the server log (app/core/privacy.py).

uvicorn logs every request path WITH its query string, and the v2 contract
puts a person's places there: `/ws?zones=lat,lon,radius`,
`/api/events?lat=&lon=&radius_km=`. Checked against a real uvicorn with its
own logging configuration, because that is what writes the production log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
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
                "/api/events?lat=48.8566&lon=2.3522&radius_km=50&since=ab12:3",
                "/api/events?lat=-&lon=-&radius_km=-&since=ab12:3",
            ),
            ("/api/geocode?q=Rue%20de%20Rivoli", "/api/geocode?q=-"),
            # a typo is refused by the API, but it carries a home all the same
            (
                "/api/events?zone=48.8,2.3,50&min_severity=severe",
                "/api/events?zone=-&min_severity=severe",
            ),
            ("/api/events?lat&&lon=", "/api/events?lat&&lon=-"),  # malformed stays readable
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
