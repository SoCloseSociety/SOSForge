"""Broadcast hub: fans normalized events out to the browsers.

Each client gets its own bounded queue. A slow client is disconnected rather
than letting backpressure reach the ingesters -- real time comes first.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from typing import Any

from app.changelog import ChangeLog
from app.scope import Scope

log = logging.getLogger(__name__)

QUEUE_MAX = 500
# Ids a scoped client is remembered to hold. Bounded so a connection left
# open for a week cannot grow without limit; far above the ring size, so in
# practice nothing a client could still display is ever forgotten.
HELD_MAX = 20_000


class Client:
    __slots__ = ("queue", "id", "filters", "evicted", "scope", "_held", "lite")

    def __init__(self, client_id: str, scope: Scope | None = None, lite: bool = False):
        self.id = client_id
        # `/ws?tick=lite`: this client takes the light tick and the `sources`
        # messages instead of a tick carrying every source's health.
        self.lite = lite
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_MAX)
        self.filters: dict[str, Any] = {}
        # set on eviction: without this signal, the send task stayed blocked
        # forever on queue.get() and the websocket stayed open on the client
        # side, silent -- a "live" connection that no longer delivers anything.
        self.evicted = asyncio.Event()
        # None is the legacy client: it gets every message, unchanged.
        self.scope = scope
        # The ids a scoped client has been SENT. A revision that moves an
        # event out of the zone, or downgrades it below the floor, must still
        # reach the client that holds it -- otherwise its notification stays
        # up forever. Without this memory the hub could not tell "not in
        # scope, never was" from "not in scope any more".
        self._held: OrderedDict[str, None] = OrderedDict()

    def hold(self, event_id: str) -> None:
        self._held[event_id] = None
        self._held.move_to_end(event_id)
        while len(self._held) > HELD_MAX:
            self._held.popitem(last=False)

    def holds(self, event_id: str) -> bool:
        return event_id in self._held

    @property
    def held_count(self) -> int:
        return len(self._held)

    def narrow(self, message: dict) -> dict | None:
        """What a scoped client gets out of one broadcast message.

        The tick ALWAYS passes: it is the freshness proof, and a client whose
        zone is quiet must still see the feed alive. An event or update
        passes when it matches; when it does not match but the client holds
        it, it becomes a purge, so the client drops what it can no longer
        trust. A purge is narrowed to the ids the client actually holds, and
        vanishes when none is left.
        """
        if self.scope is None:
            return message
        kind = message.get("type")
        if kind in ("event", "update"):
            event = message.get("event") or {}
            event_id = str(event.get("id") or "")
            if self.scope.matches_public(event):
                self.hold(event_id)
                return message
            if self.holds(event_id):
                self._held.pop(event_id, None)
                return {"type": "purge", "ids": [event_id], "reason": "out_of_scope"}
            return None
        if kind == "purge":
            ids = [i for i in message.get("ids") or () if self.holds(i)]
            if not ids:
                return None
            for event_id in ids:
                self._held.pop(event_id, None)
            return {**message, "ids": ids}
        return message


class ConnectionQuota:
    """A per-address ceiling on open websockets.

    The hub already bounds what each client can COST once connected (a bounded
    queue, eviction when it falls behind). Nothing bounded how many clients one
    host could be. An idle connection is nearly free to hold open and costs the
    server a fresh snapshot to create and a fan-out slot on every message
    after, so a single machine could quietly multiply the work of the one
    channel the whole product depends on.

    Per address rather than global on purpose: a global cap would let one
    attacker lock everyone else out, which is the denial of service we are
    trying to prevent rather than a defence against it.
    """

    def __init__(self, per_ip: int):
        self.per_ip = per_ip
        self._open: dict[str, int] = {}

    def acquire(self, address: str | None) -> bool:
        # No address means we cannot attribute the connection -- behind a proxy
        # that strips it, that would be EVERY connection, and putting them all
        # in one bucket would let the first few lock out the rest. We let them
        # through: the per-client queue bound still applies.
        if not address:
            return True
        current = self._open.get(address, 0)
        if current >= self.per_ip:
            return False
        self._open[address] = current + 1
        return True

    def release(self, address: str | None) -> None:
        if not address:
            return
        remaining = self._open.get(address, 0) - 1
        if remaining > 0:
            self._open[address] = remaining
        else:
            self._open.pop(address, None)


class Hub:
    def __init__(self) -> None:
        self._clients: set[Client] = set()
        self._lock = asyncio.Lock()
        self.sent = 0
        # Every message fanned out is also recorded here, so a client that
        # polls instead of listening can ask "what changed since".
        self.changelog = ChangeLog()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def register(self, client: Client) -> None:
        async with self._lock:
            self._clients.add(client)
        log.info("client %s connected (%d active)", client.id, self.client_count)

    async def unregister(self, client: Client) -> None:
        async with self._lock:
            self._clients.discard(client)
        log.info("client %s disconnected (%d active)", client.id, self.client_count)

    async def broadcast(self, message: dict | None, lite: dict | None = None) -> None:
        """`message` to every client; with `lite`, the clients that asked for
        the light tick get `lite` instead (serialized once, like `message`).
        A `message` of None reaches only the lite clients: that is how the
        `sources` updates travel, which the others read off every tick."""
        if message is not None:
            # The log serves the 15-minute poll; the fan-out below is the
            # live feed. A defect in the first must never cost the second a
            # message.
            try:
                self.changelog.record(message)
            except Exception as exc:  # noqa: BLE001
                log.warning("changelog: %s", exc)
        payload = json.dumps(message, default=str) if message is not None else None
        lite_payload = json.dumps(lite, default=str) if lite is not None else None
        dropped: list[Client] = []
        for client in list(self._clients):
            if lite_payload is not None and client.lite:
                if not self._offer(client, lite_payload):
                    dropped.append(client)
                continue
            if message is None or payload is None:
                continue
            # A scoped client gets its own view of the message: the shared
            # payload when it passes whole, a narrowed one otherwise, nothing
            # at all when it is not concerned. Everything after this line --
            # the bounded queue, the eviction -- is the same for every client.
            narrowed = client.narrow(message)
            if narrowed is None:
                continue
            out = payload if narrowed is message else json.dumps(narrowed, default=str)
            if not self._offer(client, out):
                dropped.append(client)
        for client in dropped:
            client.evicted.set()
            await self.unregister(client)

    def _offer(self, client: Client, payload: str) -> bool:
        """The bounded queue, the same for every client and every message:
        False means the client is too slow and must be evicted."""
        try:
            client.queue.put_nowait(payload)
            self.sent += 1
            return True
        except asyncio.QueueFull:
            log.warning("client %s too slow, evicted", client.id)
            return False


hub = Hub()
