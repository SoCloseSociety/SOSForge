"""`/ws?tick=lite`: the tick a metered phone can afford.

Lane A measured the app's background watch at ~4.7 MB an hour off Wi-Fi:
the tick carries every source's health every second, and OkHttp's websocket
offers no permessage-deflate. A lite client gets the same tick every second,
without the per-source list, and the list itself in a `sources` message when
a source's state changes (and every 300 s). Without the parameter nothing
changes, byte for byte.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.hub import Client, Hub
from app.sources.base import SourceHealth


class Stub:
    def __init__(self, name: str) -> None:
        self.name = name
        self.health = SourceHealth(name)
        self.health.ok(3)


def drain(client: Client) -> list[dict]:
    out = []
    while not client.queue.empty():
        out.append(json.loads(client.queue.get_nowait()))
    return out


@pytest.fixture
def world(monkeypatch, v2):
    stubs = [Stub("usgs"), Stub("emsc")]
    hub = Hub()
    monkeypatch.setattr(main, "sources", stubs)
    monkeypatch.setattr(main, "hub", hub)
    monkeypatch.setattr(main.settings, "heartbeat_seconds", 0.05)
    return stubs, hub


async def run_heartbeat(seconds: float) -> None:
    task = asyncio.create_task(main.heartbeat())
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class TestTheLiteTick:
    async def test_full_clients_are_unchanged_and_lite_ones_get_the_light_tick(self, world):
        stubs, hub = world
        full, lite = Client("full"), Client("lite", lite=True)
        await hub.register(full)
        await hub.register(lite)

        task = asyncio.create_task(main.heartbeat())
        await asyncio.sleep(0.4)
        stubs[1].health.fail(RuntimeError("feed down"))  # mid-run
        await asyncio.sleep(0.4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        got_full, got_lite = drain(full), drain(lite)
        # the full client: ticks exactly as before, sources inside, no new type
        assert {m["type"] for m in got_full} == {"tick"}
        for tick in got_full:
            assert list(tick) == ["type", "server_time", "stats", "sources", "clients"]
            assert "by_source" in tick["stats"]
        # the lite client: as many ticks, none carrying the sources
        ticks = [m for m in got_lite if m["type"] == "tick"]
        assert len(ticks) == len(got_full) >= 10
        for tick in ticks:
            assert list(tick) == ["type", "server_time", "stats", "clients"]
            assert "by_source" not in tick["stats"]
            assert "total_buffered" in tick["stats"]
        # ... and the list when it changed: once at the start, once more for
        # the source that went down, never per tick
        updates = [m for m in got_lite if m["type"] == "sources"]
        assert len(updates) == 2, [m["type"] for m in got_lite]
        assert [s["connected"] for s in updates[0]["sources"]] == [True, True]
        assert got_lite[0]["type"] == "sources"
        assert [s["connected"] for s in updates[-1]["sources"]] == [True, False]
        assert updates[-1]["sources"][1]["last_error"] == "RuntimeError: feed down"

    async def test_a_lite_tick_is_a_small_fraction_of_the_full_one(self, world):
        stubs, hub = world
        stubs.extend(Stub(f"src{i}") for i in range(30))  # production has 32
        full, lite = Client("full"), Client("lite", lite=True)
        await hub.register(full)
        await hub.register(lite)
        await run_heartbeat(0.2)
        full_tick = next(m for m in drain(full) if m["type"] == "tick")
        lite_tick = next(m for m in drain(lite) if m["type"] == "tick")
        assert len(json.dumps(lite_tick)) * 10 < len(json.dumps(full_tick))


class TestTheRefreshRule:
    def test_no_change_no_message_until_the_refresh(self):
        states = [Stub("usgs").health.snapshot()]
        watch = main.SourcesWatch()
        assert watch.due(states, now=0.0)
        assert not watch.due(states, now=1.0)
        assert not watch.due(states, now=main.SOURCES_REFRESH_SECONDS - 1)
        assert watch.due(states, now=main.SOURCES_REFRESH_SECONDS + 0.1)

    def test_counters_moving_are_not_a_change(self):
        stub = Stub("usgs")
        watch = main.SourcesWatch()
        assert watch.due([stub.health.snapshot()], now=0.0)
        stub.health.ok(5)  # last_ok and events_seen move on every poll
        assert not watch.due([stub.health.snapshot()], now=1.0)
        stub.health.feed_failed("germany", RuntimeError("x"))
        stub.health.ok(1)  # degraded now, with an error
        assert watch.due([stub.health.snapshot()], now=2.0)


class TestTheParameter:
    @pytest.fixture
    def client(self, v2):
        return TestClient(v2.main.app)

    def test_a_lite_socket_opens_with_the_full_list(self, client):
        with client.websocket_connect("/ws?zones=35.6,139.6,300&chunks=1&tick=lite") as ws:
            first = ws.receive_json()
        assert first["type"] == "snapshot"
        assert "sources" in first and "stats" in first

    @pytest.mark.parametrize("value", ["full", "1", ""])
    def test_any_other_value_is_refused(self, client, value):
        with client.websocket_connect(f"/ws?tick={value}") as ws:
            message = ws.receive_json()
            assert message["type"] == "error" and message["code"] == "bad_tick"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
        assert closed.value.code == 1008

    def test_the_feature_is_announced(self, client):
        assert "tick_lite" in client.get("/api/version").json()["features"]
