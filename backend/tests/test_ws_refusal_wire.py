"""Websocket refusals, as a REAL client receives them from a REAL server.

Starlette's TestClient reports whatever close code the app passed to
`ws.close()`, even when the app closes BEFORE accepting. A real server cannot:
the ASGI spec turns a close before accept into an HTTP 403 handshake
rejection, and the code never reaches the wire. So the quota test written
against TestClient asserted 1013 for months while every real client got a
403 -- and a 403 tells a client nothing about backing off, so the web and the
app retried as hard as on any other failure (found by lane N's drills,
2026-10-27).

These tests run uvicorn on a free port with the stock websocket stack and
connect with the `websockets` client a browser-equivalent would use.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus


@pytest.fixture
def server(monkeypatch) -> Iterator[str]:
    """The app under uvicorn's own websocket implementation, no lifespan (so
    no source starts and nothing touches the network), one socket allowed
    per address."""
    from app import main
    from app.hub import ConnectionQuota

    monkeypatch.setattr(main, "ws_quota", ConnectionQuota(per_ip=1))
    config = uvicorn.Config(main.app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    port = srv.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}/ws"
    finally:
        srv.should_exit = True
        thread.join(timeout=10)


async def _close_code(ws) -> tuple[int | None, str]:
    """Reads until the server closes; returns the close code and reason."""
    try:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=5)
    except ConnectionClosed as closed:
        frame = closed.rcvd
        return (frame.code if frame else None), (frame.reason if frame else "")
    raise AssertionError("the server never closed the socket")  # pragma: no cover


async def test_a_client_over_its_quota_sees_close_1013_not_a_403(server):
    async with websockets.connect(server) as first:
        assert json.loads(await first.recv())["type"] == "snapshot"
        try:
            async with websockets.connect(server) as second:
                code, reason = await _close_code(second)
        except InvalidStatus as rejected:  # pragma: no cover -- the defect
            pytest.fail(
                f"refused at the handshake with HTTP {rejected.response.status_code}: "
                "the 1013 never reached the client"
            )
    assert code == 1013, "1013 is what tells a client to back off instead of retrying hard"
    assert reason, "a refusal without a reason cannot be told apart from a crash"


async def test_the_slot_comes_back_when_the_first_socket_closes(server):
    async with websockets.connect(server) as first:
        assert json.loads(await first.recv())["type"] == "snapshot"
    # the refusal path must not leak a slot, and a closed socket must free one
    for _ in range(50):
        try:
            async with websockets.connect(server) as again:
                assert json.loads(await again.recv())["type"] == "snapshot"
                return
        except ConnectionClosed:
            await asyncio.sleep(0.05)  # the server has not unregistered yet
    pytest.fail("the slot never came back")


async def test_bad_parameters_reach_the_client_as_an_error_then_close_1008(server):
    try:
        async with websockets.connect(server + "?zone=35,139,300") as ws:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            code, reason = await _close_code(ws)
    except InvalidStatus as rejected:  # pragma: no cover
        pytest.fail(f"refused at the handshake with HTTP {rejected.response.status_code}")
    assert message["type"] == "error"
    assert message["code"] == "unknown_param"
    assert code == 1008
    assert reason == "unknown_param"
