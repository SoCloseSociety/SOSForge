"""Round 2, lane P -- the per-second and per-connection costs of the core.

Measured on a live run of all 32 sources (tools/ws_load.py, 2026-10-27):

* the heartbeat slept a full second AFTER building and fanning out each
  tick, so every tick came late by what the previous one cost: a period of
  1.0019 s with one socket, 1.0256 s with a thousand -- 92 ticks short an
  hour at that load, the freshness proof slipping exactly when it matters;
* a socket's snapshot cost 6 ms to compose and serialize with a full ring,
  17 ms chunked, all of it on the loop that owes every open tab its tick --
  and every unscoped socket (the web, the agent) paid it again for
  identical bytes. It is now built once per store version and shared.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import time
import tracemalloc
from datetime import UTC, datetime, timedelta

import pytest
from conftest import TOKYO, make_event
from fastapi.testclient import TestClient

from app import main
from app.dedupe import Deduper
from app.models.event import Event, Kind, Severity
from app.scope import compose


class TestTheHeartbeatKeepsItsPeriod:
    def test_the_next_tick_is_on_the_grid_not_after_the_work(self):
        # the previous tick was due at 10.0 and its fan-out ended at 10.3
        assert main.next_tick(10.0, now=10.3, period=1.0) == 11.0

    def test_a_loop_stalled_past_a_period_resumes_from_now(self):
        """No burst of catch-up ticks: one tick says everything five would."""
        assert main.next_tick(10.0, now=14.5, period=1.0) == 14.5

    async def test_the_period_holds_when_each_tick_costs_time(self, monkeypatch):
        period, cost, window = 0.05, 0.02, 1.0
        stamps: list[float] = []

        class SlowHub:
            client_count = 1

            async def broadcast(self, message: dict) -> None:
                stamps.append(time.monotonic())
                await asyncio.sleep(cost)  # a fan-out that takes 40 % of a period

        monkeypatch.setattr(main, "hub", SlowHub())
        monkeypatch.setattr(main.settings, "heartbeat_seconds", period)
        task = asyncio.create_task(main.heartbeat())
        await asyncio.sleep(window)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # sleeping a period after the work gives window / (period + cost) = 14
        assert len(stamps) >= 18, f"{len(stamps)} ticks in {window} s at {period} s"


# ------------------------------------------------------------ shared snapshot


class TestSpliceIsJsonDumps:
    @pytest.mark.parametrize(
        ("head", "value", "tail"),
        [
            ({"type": "snapshot", "server_time": "2026-10-27T10:00:00+00:00"}, [], {}),
            ({"type": "snapshot_more"}, [{"id": "a", "place": '東京 "x"'}], {"remaining": 3}),
            ({"a": 1}, [{"n": None, "f": 1.5}], {"stats": {"x": [1, 2]}, "more": 0}),
        ],
    )
    def test_same_text(self, head, value, tail):
        expected = json.dumps({**head, "events": value, **tail}, default=str)
        assert main.splice(head, "events", json.dumps(value, default=str), tail) == expected


@pytest.fixture
def client(v2):
    main._shared_snapshots.clear()
    # no lifespan: no source starts, nothing touches the network
    return TestClient(v2.main.app)


@pytest.fixture
def compose_calls(monkeypatch) -> list:
    calls: list = []
    real = main.compose

    def counting(*args, **kwargs):
        calls.append(args[2])  # the scope
        return real(*args, **kwargs)

    monkeypatch.setattr(main, "compose", counting)
    return calls


def fill(v2, n: int = 12) -> None:
    for i in range(n):
        severity = Severity.EXTREME if i % 4 == 0 else Severity.MODERATE
        asyncio.run(
            v2.pipeline.emit(make_event(f"s{i}", *TOKYO, minutes_ago=10 + i, severity=severity))
        )


def snapshot_of(ws) -> tuple[list[str], list[dict]]:
    first = ws.receive_json()
    keys = list(first.keys())
    events = list(first["events"])
    remaining = first.get("more", 0)
    while remaining:
        more = ws.receive_json()
        assert list(more.keys()) == ["type", "events", "remaining", "server_time"]
        events.extend(more["events"])
        remaining = more["remaining"]
    return keys, events


def composed(v2, chunked: bool) -> list[dict]:
    events, _ = compose(
        v2.store.recent(limit=main.settings.ring_size, primary_only=True),
        main.settings.snapshot_size,
        None,
        pin=chunked,
    )
    return [e.public() for e in events]


class TestTheSharedSnapshot:
    @pytest.mark.parametrize(("path", "chunked"), [("/ws", False), ("/ws?chunks=1", True)])
    def test_it_is_what_composing_gives(self, v2, client, monkeypatch, path, chunked):
        monkeypatch.setattr(main, "SNAPSHOT_CHUNK_EVENTS", 5)
        fill(v2)
        for _ in range(2):  # the second one is served from the cache
            with client.websocket_connect(path) as ws:
                keys, events = snapshot_of(ws)
            expected = ["type", "server_time", "events", "stats", "sources"]
            assert keys == expected + (["more"] if chunked else [])
            assert events == composed(v2, chunked)

    def test_it_is_built_once_while_nothing_changes(self, v2, client, compose_calls):
        fill(v2)
        for _ in range(3):
            with client.websocket_connect("/ws?chunks=1") as ws:
                snapshot_of(ws)
        assert len(compose_calls) == 1

    def test_a_scoped_socket_never_shares(self, v2, client, compose_calls):
        fill(v2)
        for _ in range(2):
            with client.websocket_connect(f"/ws?zones={TOKYO[0]},{TOKYO[1]},100") as ws:
                ws.receive_json()
        assert len(compose_calls) == 2
        assert all(scope is not None for scope in compose_calls)

    @pytest.mark.parametrize("path", ["/ws", "/ws?chunks=1"])
    def test_every_change_reaches_the_next_socket(self, v2, client, path):
        fill(v2)
        with client.websocket_connect(path) as ws:
            snapshot_of(ws)  # the cache now holds this version

        revised = make_event("s1", *TOKYO, minutes_ago=11, severity=Severity.SEVERE)
        asyncio.run(v2.pipeline.emit(revised))
        asyncio.run(v2.pipeline.retract("test:s2", "cancelled"))
        asyncio.run(v2.pipeline.emit(make_event("new", *TOKYO, minutes_ago=1)))

        with client.websocket_connect(path) as ws:
            _, events = snapshot_of(ws)
        by_id = {e["id"]: e for e in events}
        assert by_id["test:s1"]["severity"] == "severe"
        assert by_id["test:s1"]["revision"] == 1
        assert "test:s2" not in by_id, "a cancelled event was served from the cache"
        assert "test:new" in by_id
        assert events == composed(v2, path.endswith("chunks=1"))

    def test_a_promotion_reaches_the_next_socket(self, v2, client):
        """A promotion rewrites `cluster_id` in place, so only the store's
        version can tell the cache about it."""
        primary = make_event("p", *TOKYO, minutes_ago=3)
        primary.cluster_id = primary.id
        follower = make_event("f", *TOKYO, minutes_ago=3)
        follower.cluster_id = primary.id
        v2.store.upsert(primary)
        v2.store.upsert(follower)
        with client.websocket_connect("/ws") as ws:
            _, events = snapshot_of(ws)
        assert "test:f" not in {e["id"] for e in events}

        v2.store.remove(["test:p"], "cancelled")
        with client.websocket_connect("/ws") as ws:
            _, events = snapshot_of(ws)
        assert "test:f" in {e["id"] for e in events}

    def test_an_open_socket_does_not_keep_its_snapshot(self, v2, client):
        """The endpoint's frame lives as long as the socket. Built there, the
        snapshot (public dicts, serialized chunks) stayed referenced for the
        whole connection: about 0.45 MB per open tab, measured live."""
        fill(v2, 300)
        zoned = f"/ws?zones={TOKYO[0]},{TOKYO[1]},100"  # scoped: its own snapshot
        with contextlib.ExitStack() as stack:
            first = stack.enter_context(client.websocket_connect(zoned))
            first.receive_json()  # warm everything up once
            gc.collect()
            tracemalloc.start()
            before = tracemalloc.get_traced_memory()[0]
            for _ in range(5):
                ws = stack.enter_context(client.websocket_connect(zoned))
                assert len(ws.receive_json()["events"]) == 300
            gc.collect()
            held = (tracemalloc.get_traced_memory()[0] - before) / 5
            tracemalloc.stop()
        # measured: 339 KB before (300 test events), 87 KB after -- the rest
        # is the test client's own per-socket machinery
        assert held < 150_000, f"{held:.0f} bytes held per open socket"

    def test_it_never_outlives_its_ttl(self, v2, client, compose_calls, monkeypatch):
        fill(v2)
        monkeypatch.setattr(main, "SNAPSHOT_CACHE_SECONDS", 0.0)
        for _ in range(2):
            with client.websocket_connect("/ws") as ws:
                snapshot_of(ws)
        assert len(compose_calls) == 2


# ---------------------------------------------------------------- REST bytes


def test_the_legacy_events_body_is_byte_for_byte_what_fastapi_rendered(v2, client, monkeypatch):
    """The legacy `/api/events` now hands FastAPI a JSONResponse instead of a
    dict, skipping `jsonable_encoder` (6.9 ms of 14). Same bytes as the dict
    went out as, for the same store and clock."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    fill(v2, 40)
    frozen = main.utcnow()
    monkeypatch.setattr(main, "utcnow", lambda: frozen)
    events = v2.store.recent(limit=300, primary_only=True)
    as_before = {
        "count": len(events),
        "server_time": frozen.isoformat(),
        "events": [e.public() for e in events],
    }
    response = client.get("/api/events")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "etag" not in response.headers  # the legacy path stays without one
    assert response.content == JSONResponse(jsonable_encoder(as_before)).body


# ------------------------------------------------------------------- deduper


class TestTheDeduperWindowIsExact:
    """Its entries' times are now integer microseconds (6.3x faster on the
    real journals, 0 of 6678 clusters different). The edge must still be
    exactly where `timedelta` put it: inclusive at the window, microseconds
    counted, no float rounding."""

    @staticmethod
    def pair(gap: timedelta) -> tuple[str | None, str | None]:
        deduper = Deduper(window_seconds=90.0)
        base = datetime(2026, 10, 27, 9, 0, 0, 123457, tzinfo=UTC)
        first = make_event("a", *TOKYO, kind=Kind.EARTHQUAKE)
        first.source, first.time, first.magnitude = "emsc", base, 5.0
        second = make_event("b", *TOKYO, kind=Kind.EARTHQUAKE)
        second.source, second.time, second.magnitude = "usgs", base + gap, 5.1
        deduper.assign(first)
        deduper.assign(second)
        return first.cluster_id, second.cluster_id

    def test_exactly_the_window_apart_is_the_same_quake(self):
        first, second = self.pair(timedelta(seconds=90))
        assert second == first

    def test_one_microsecond_more_is_not(self):
        first, second = self.pair(timedelta(seconds=90, microseconds=1))
        assert second != first

    def test_re_listing_never_fills_the_window(self):
        """Lane Q's mechanism: two agencies re-listing one quake every poll.
        Each re-listed row used to meet the OTHER agency's newer copy first,
        match it and be appended again: after 399 cycles an unrelated quake
        had left the window, and its late twin was shown as a second quake."""

        def quake(eid: str, source: str, dt: float, lat: float) -> Event:
            event = make_event(eid, lat, 165.5556, kind=Kind.EARTHQUAKE)
            event.id, event.source, event.magnitude = eid, source, 5.0
            event.time = datetime(2026, 10, 27, 9, 20, 55, 730000, tzinfo=UTC) + timedelta(
                seconds=dt
            )
            return event

        deduper = Deduper()
        deduper.assign(quake("usgs:us7000ttfe", "usgs", 0, 40.0))
        for _ in range(500):
            deduper.assign(quake("ingv:X", "ingv", 600, -13.9))
            deduper.assign(quake("geofon:Y", "geofon", 601, -13.9))
        ids = [e.id for e in deduper._recent]
        assert len(ids) == len(set(ids)) == 3
        late = quake("emsc:20261027_0000114", "emsc", 0, 40.0)
        deduper.assign(late)
        assert late.cluster_id == "usgs:us7000ttfe", "the late twin was shown as its own quake"

    def test_a_re_listed_row_keeps_its_own_cluster(self):
        deduper = Deduper()
        first = make_event("first", *TOKYO, kind=Kind.EARTHQUAKE)
        first.source = "emsc"
        deduper.assign(first)
        again = first.model_copy()
        again.cluster_id = None
        deduper.assign(again)
        assert again.cluster_id == first.cluster_id == first.id
        assert len(deduper._recent) == 1

    def test_forget_keeps_times_and_events_in_step(self):
        deduper = Deduper(window_seconds=90.0)
        for i in range(5):
            event = make_event(f"e{i}", TOKYO[0] + i * 10, TOKYO[1], kind=Kind.EARTHQUAKE)
            event.source = f"s{i}"  # far apart, distinct sources: five clusters
            deduper.assign(event)
        deduper.forget({"test:e1", "test:e3"})
        assert [e.id for e in deduper._recent] == ["test:e0", "test:e2", "test:e4"]
        assert len(deduper._micros) == 3
