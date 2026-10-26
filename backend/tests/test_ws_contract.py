"""The websocket contract v2, end to end through the real pipeline and hub.

Without parameters the socket behaves as it always did. With `zones`,
`min_severity`, `kinds` the snapshot and the deltas are narrowed server-side,
the tick still reaches everyone, and an event the client holds that leaves
its scope or is withdrawn still reaches it -- otherwise its notification
stays up forever.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.hub import QUEUE_MAX, Client, Hub, hub
from app.models.event import Severity, utcnow
from app.scope import Scope, Zone

TOKYO = (35.6762, 139.6503)
YOKOHAMA = (35.4437, 139.6380)
SAPPORO = (43.0618, 141.3545)
LOS_ANGELES = (34.0522, -118.2437)
TOKYO_300 = "35.6762,139.6503,300"
LEGACY_SNAPSHOT_KEYS = {"type", "server_time", "events", "stats", "sources"}
# `more` is the only key the chunked snapshot adds to the first message
SNAPSHOT_KEYS = LEGACY_SNAPSHOT_KEYS | {"more"}


def tick() -> dict:
    return {
        "type": "tick",
        "server_time": utcnow().isoformat(),
        "stats": {},
        "sources": [],
        "clients": 1,
    }


@pytest.fixture
def client(v2):
    # no lifespan: no source starts, nothing touches the network
    return TestClient(v2.main.app)


def emit(ws, v2, event) -> None:
    ws.portal.call(v2.pipeline.emit, event)


def broadcast(ws, message: dict) -> None:
    ws.portal.call(hub.broadcast, message)


def whole_snapshot(ws) -> tuple[list[dict], list[dict]]:
    """The snapshot as a chunk-aware client reads it: every event of the
    first message and of the `snapshot_more` that follow, and the list of
    messages as received."""
    first = ws.receive_json()
    assert first["type"] == "snapshot"
    messages, events = [first], list(first["events"])
    remaining = first["more"]
    while remaining:
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == "snapshot_more":
            events.extend(message["events"])
            assert message["remaining"] == remaining - len(message["events"])
            remaining = message["remaining"]
    return events, messages


def drain(ws) -> list[dict]:
    """Everything this client received before a sentinel tick. The tick goes
    through every scope, so a message that wrongly never comes shows up as a
    failed assertion instead of a test blocked forever on receive."""
    broadcast(ws, tick())
    out = []
    while True:
        message = ws.receive_json()
        if message["type"] == "tick":
            return out
        out.append(message)


class TestNoParameters:
    def test_the_snapshot_keeps_its_legacy_shape(self, v2, client, ev):
        asyncio.run(v2.pipeline.emit(ev("a", *TOKYO)))
        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
        assert set(snap) == SNAPSHOT_KEYS
        assert snap["more"] == 0
        assert [e["id"] for e in snap["events"]] == ["test:a"]

    def test_every_message_arrives_unchanged(self, v2, client, ev):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            emit(ws, v2, ev("far", *LOS_ANGELES))
            (message,) = drain(ws)
            assert message["type"] == "event" and message["event"]["id"] == "test:far"
            # `escalated` is the pipeline's own field (audit 6, #4), always
            # present; what this pins is that the scope layer adds nothing.
            assert set(message) == {"type", "event", "primary", "breaking", "escalated"}
            ws.portal.call(v2.pipeline.retract, "test:far", "cancelled")
            assert drain(ws) == [{"type": "purge", "ids": ["test:far"], "reason": "cancelled"}]

    def test_the_shared_payload_is_sent_byte_for_byte(self, v2, client):
        message = {"type": "update", "event": {"id": "x", "lat": 1, "lon": 2}, "escalated": True}
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            broadcast(ws, message)
            assert ws.receive_text() == json.dumps(message, default=str)


class TestSnapshot:
    def test_old_severe_and_ongoing_events_reach_a_fresh_tab(self, v2, client, ev, monkeypatch):
        monkeypatch.setattr(v2.main.settings, "snapshot_size", 2)
        for i in range(4):
            asyncio.run(
                v2.pipeline.emit(ev(f"r{i}", 10, i, minutes_ago=i, severity=Severity.MINOR))
            )
        asyncio.run(
            v2.pipeline.emit(ev("red", 10, 40, minutes_ago=19 * 60, severity=Severity.EXTREME))
        )
        asyncio.run(v2.pipeline.emit(ev("wind", 10, 41, minutes_ago=30 * 60, ongoing=True)))
        with client.websocket_connect("/ws") as ws:
            ids = [e["id"] for e in whole_snapshot(ws)[0]]
        assert ids == ["test:r0", "test:r1", "test:red", "test:wind"]

    def test_a_filtered_snapshot_is_filtered_before_it_is_cut(self, v2, client, ev, monkeypatch):
        monkeypatch.setattr(v2.main.settings, "snapshot_size", 2)
        for i in range(4):
            asyncio.run(v2.pipeline.emit(ev(f"la{i}", *LOS_ANGELES, minutes_ago=i)))
        asyncio.run(
            v2.pipeline.emit(ev("yoko", *YOKOHAMA, minutes_ago=90, severity=Severity.MINOR))
        )
        asyncio.run(v2.pipeline.emit(ev("nowhere", None, None, minutes_ago=1)))
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as ws:
            snap = ws.receive_json()
        assert [e["id"] for e in snap["events"]] == ["test:yoko"]
        assert snap["scope"]["zones"] == [{"lat": 35.6762, "lon": 139.6503, "radius_km": 300.0}]
        # the one event with no position is counted, not silently lost
        assert snap["unplaced"] == {"dropped": 1, "delivered": 0}

    def test_a_snapshot_event_relocated_out_of_the_zone_reaches_the_client(self, v2, client, ev):
        asyncio.run(v2.pipeline.emit(ev("q", *YOKOHAMA)))
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as ws:
            assert [e["id"] for e in ws.receive_json()["events"]] == ["test:q"]
            emit(ws, v2, ev("q", *SAPPORO))  # same id, moved 800 km
            assert drain(ws) == [{"type": "purge", "ids": ["test:q"], "reason": "out_of_scope"}]


class TestFilteredDeltas:
    def test_the_tick_reaches_a_client_whose_zone_is_quiet(self, v2, client, ev):
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as ws:
            ws.receive_json()
            emit(ws, v2, ev("la", *LOS_ANGELES))  # not for Tokyo
            broadcast(ws, tick())
            assert ws.receive_json()["type"] == "tick"  # the event was skipped, the tick was not

    def test_an_event_in_the_zone_passes_untouched(self, v2, client, ev):
        with client.websocket_connect(f"/ws?zones={TOKYO_300}&min_severity=moderate") as ws:
            ws.receive_json()
            emit(ws, v2, ev("y", *YOKOHAMA))
            (message,) = drain(ws)
            assert message["type"] == "event" and message["event"]["id"] == "test:y"

    def test_escalated_is_forwarded_untouched(self, v2, client):
        update = {
            "type": "update",
            "event": {
                "id": "e",
                "lat": TOKYO[0],
                "lon": TOKYO[1],
                "severity": "severe",
                "kind": "storm",
            },
            "escalated": True,
        }
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as ws:
            ws.receive_json()
            broadcast(ws, update)
            assert ws.receive_text() == json.dumps(update, default=str)

    def test_a_revision_moving_an_event_out_reaches_the_client_once(self, v2, client, ev):
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as ws:
            ws.receive_json()
            emit(ws, v2, ev("y", *YOKOHAMA))
            assert [m["type"] for m in drain(ws)] == ["event"]
            emit(ws, v2, ev("y", *SAPPORO))
            assert drain(ws) == [{"type": "purge", "ids": ["test:y"], "reason": "out_of_scope"}]
            # no longer held: a further revision out there is none of its business
            emit(ws, v2, ev("y", *LOS_ANGELES))
            assert drain(ws) == []

    def test_a_downgrade_below_the_floor_is_a_withdrawal(self, v2, client, ev):
        with client.websocket_connect(f"/ws?zones={TOKYO_300}&min_severity=severe") as ws:
            ws.receive_json()
            emit(ws, v2, ev("y", *YOKOHAMA, severity=Severity.SEVERE))
            assert [m["type"] for m in drain(ws)] == ["event"]
            emit(ws, v2, ev("y", *YOKOHAMA, severity=Severity.MINOR))
            assert drain(ws) == [{"type": "purge", "ids": ["test:y"], "reason": "out_of_scope"}]

    def test_a_cancellation_reaches_only_the_clients_that_hold_it(self, v2, client, ev):
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as tokyo:
            tokyo.receive_json()
            with client.websocket_connect("/ws?zones=34.0522,-118.2437,100") as la:
                la.receive_json()
                emit(tokyo, v2, ev("eew", *YOKOHAMA))
                assert [m["type"] for m in drain(tokyo)] == ["event"]
                tokyo.portal.call(v2.pipeline.retract, "test:eew", "cancelled")
                assert drain(tokyo) == [
                    {"type": "purge", "ids": ["test:eew"], "reason": "cancelled"}
                ]
                # Los Angeles never held it: neither the event nor its withdrawal
                assert drain(la) == []

    def test_kinds_filter(self, v2, client, ev):
        from app.models.event import Kind

        with client.websocket_connect("/ws?kinds=earthquake,tsunami") as ws:
            ws.receive_json()
            emit(ws, v2, ev("storm", *TOKYO))
            emit(ws, v2, ev("quake", *LOS_ANGELES, kind=Kind.EARTHQUAKE, magnitude=4.0))
            assert [m["event"]["id"] for m in drain(ws)] == ["test:quake"]


class TestRefusal:
    @pytest.mark.parametrize(
        ("query", "code"),
        [
            ("zones=91,0,10", "bad_zones"),
            ("zones=0,0,5000", "bad_zones"),
            ("zones=" + ";".join(["0,0,10"] * 11), "bad_zones"),
            ("zones=", "bad_zones"),
            ("zone=35,139,300", "unknown_param"),
            ("min_severity=critical", "bad_min_severity"),
            ("kinds=quake", "bad_kinds"),
            ("unplaced=FR", "bad_unplaced"),
            ("zones=0,0,10&zones=1,1,10", "bad_zones"),
        ],
    )
    def test_bad_parameters_are_refused_with_a_reason_then_closed(self, client, query, code):
        with client.websocket_connect(f"/ws?{query}") as ws:
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["code"] == code
            assert message["reason"]
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
        assert closed.value.code == 1008


class TestEviction:
    """The slow-client policy is the same for a filtered client: a full queue
    means eviction, and the tick alone can fill it."""

    async def test_a_filtered_client_is_evicted_when_its_queue_is_full(self):
        local = Hub()
        slow = Client("slow", Scope(zones=(Zone(*TOKYO, 300),)))
        await local.register(slow)
        for _ in range(QUEUE_MAX):
            await local.broadcast(tick())
        assert not slow.evicted.is_set()
        await local.broadcast(tick())
        assert slow.evicted.is_set()
        assert local.client_count == 0

    async def test_messages_it_does_not_want_do_not_fill_its_queue(self):
        local = Hub()
        quiet = Client("quiet", Scope(zones=(Zone(*TOKYO, 300),)))
        await local.register(quiet)
        far = {
            "type": "event",
            "event": {"id": "far", "lat": LOS_ANGELES[0], "lon": LOS_ANGELES[1]},
        }
        for _ in range(QUEUE_MAX + 50):
            await local.broadcast(far)
        assert quiet.queue.qsize() == 0
        assert not quiet.evicted.is_set()

    async def test_a_legacy_client_next_to_it_is_evicted_as_before(self):
        local = Hub()
        legacy = Client("legacy")
        quiet = Client("quiet", Scope(zones=(Zone(*TOKYO, 300),)))
        await local.register(legacy)
        await local.register(quiet)
        far = {
            "type": "event",
            "event": {"id": "far", "lat": LOS_ANGELES[0], "lon": LOS_ANGELES[1]},
        }
        for _ in range(QUEUE_MAX + 1):
            await local.broadcast(far)
        assert legacy.evicted.is_set()
        assert not quiet.evicted.is_set()


class TestChunkedSnapshot:
    """The pinned snapshot was 2.7 times the old one in a single message, and
    a client's 15 s silence watchdog runs while it downloads. The first
    message now carries what every client got before pinning; the rest
    follows in small `snapshot_more` chunks, before any delta."""

    @pytest.fixture
    def pinned_store(self, v2, ev, monkeypatch):
        monkeypatch.setattr(v2.main.settings, "snapshot_size", 2)
        monkeypatch.setattr(v2.main, "SNAPSHOT_CHUNK_EVENTS", 2)
        monkeypatch.setattr(v2.main, "SNAPSHOT_TICK_EVERY", 1000)
        for i in range(3):
            asyncio.run(v2.pipeline.emit(ev(f"r{i}", *YOKOHAMA, minutes_ago=i)))
        for i in range(5):
            asyncio.run(
                v2.pipeline.emit(
                    ev(f"p{i}", *YOKOHAMA, minutes_ago=600 + i, severity=Severity.SEVERE)
                )
            )
        return ["test:r0", "test:r1", "test:p0", "test:p1", "test:p2", "test:p3", "test:p4"]

    def test_chunks_follow_in_composition_order_and_count_down(self, client, pinned_store):
        with client.websocket_connect("/ws") as ws:
            events, messages = whole_snapshot(ws)
        assert [e["id"] for e in events] == pinned_store
        assert messages[0]["more"] == 5
        assert [m["type"] for m in messages] == ["snapshot"] + ["snapshot_more"] * 3
        assert [m["remaining"] for m in messages[1:]] == [3, 1, 0]
        assert [len(m["events"]) for m in messages[1:]] == [2, 2, 1]
        for chunk in messages[1:]:
            assert set(chunk) == {"type", "events", "remaining", "server_time"}

    def test_a_client_that_ignores_chunks_sees_what_it_saw_before(self, v2, client, pinned_store):
        # the pre-chunking, pre-pinning snapshot: the latest snapshot_size primaries
        before = v2.store.recent(limit=v2.main.settings.snapshot_size, primary_only=True)
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
        assert [e["id"] for e in first["events"]] == [e.id for e in before]

    def test_no_delta_overtakes_a_chunk(self, v2, client, ev, pinned_store, monkeypatch):
        """A delta broadcast while the snapshot is being cut is queued, and
        the queue is pumped only after the last chunk."""
        real_split = v2.main.split

        def split_then_broadcast(*args):
            out = real_split(*args)
            asyncio.get_running_loop().create_task(
                v2.pipeline.emit(ev("p4", *YOKOHAMA, minutes_ago=600, severity=Severity.EXTREME))
            )
            return out

        monkeypatch.setattr(v2.main, "split", split_then_broadcast)
        with client.websocket_connect("/ws") as ws:
            _, messages = whole_snapshot(ws)
            assert all(m["type"] in ("snapshot", "snapshot_more") for m in messages)
            (update,) = drain(ws)
        assert update["type"] == "update" and update["event"]["id"] == "test:p4"
        assert update["event"]["severity"] == "extreme"

    def test_keepalive_ticks_between_chunks_never_after_the_last(
        self, client, pinned_store, monkeypatch
    ):
        import app.main as main

        monkeypatch.setattr(main, "SNAPSHOT_TICK_EVERY", 1)
        with client.websocket_connect("/ws") as ws:
            events, messages = whole_snapshot(ws)
        assert [m["type"] for m in messages] == [
            "snapshot",
            "snapshot_more",
            "tick",
            "snapshot_more",
            "tick",
            "snapshot_more",
        ]
        assert [e["id"] for e in events] == pinned_store

    def test_the_byte_budget_cuts_a_chunk_early(self, client, pinned_store, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "SNAPSHOT_CHUNK_EVENTS", 100)
        monkeypatch.setattr(main, "SNAPSHOT_CHUNK_BYTES", 1)  # one event per chunk
        with client.websocket_connect("/ws") as ws:
            _, messages = whole_snapshot(ws)
        assert [len(m["events"]) for m in messages[1:]] == [1] * 5

    def test_every_chunked_id_is_held_from_the_start(self, v2, client, ev, pinned_store):
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as ws:
            events, messages = whole_snapshot(ws)
            assert [e["id"] for e in events] == pinned_store
            assert "scope" in messages[0] and "unplaced" in messages[0]
            assert all("scope" not in m for m in messages[1:])
            # p3 travelled in the LAST chunk; relocating it must still reach us
            emit(ws, v2, ev("p3", *SAPPORO, minutes_ago=603, severity=Severity.SEVERE))
            assert drain(ws) == [{"type": "purge", "ids": ["test:p3"], "reason": "out_of_scope"}]

    def test_a_scoped_snapshot_chunks_only_what_is_in_scope(self, v2, client, ev, pinned_store):
        for i in range(4):
            asyncio.run(
                v2.pipeline.emit(
                    ev(f"la{i}", *LOS_ANGELES, minutes_ago=700 + i, severity=Severity.EXTREME)
                )
            )
        with client.websocket_connect(f"/ws?zones={TOKYO_300}") as ws:
            events, messages = whole_snapshot(ws)
        assert [e["id"] for e in events] == pinned_store
        assert messages[0]["more"] == 5


def test_version_announces_the_chunked_snapshot(client):
    assert "snapshot_chunked" in client.get("/api/version").json()["features"]
