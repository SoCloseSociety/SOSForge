"""Broadcast hub: fans normalized events out to the browsers.

Each client gets its own bounded queue. A slow client is disconnected rather
than letting backpressure reach the ingesters -- real time comes first.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_MAX = 500


class Client:
    __slots__ = ("queue", "id", "filters", "evicted")

    def __init__(self, client_id: str):
        self.id = client_id
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_MAX)
        self.filters: dict[str, Any] = {}
        # set on eviction: without this signal, the send task stayed blocked
        # forever on queue.get() and the websocket stayed open on the client
        # side, silent -- a "live" connection that no longer delivers anything.
        self.evicted = asyncio.Event()


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

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, default=str)
        dropped: list[Client] = []
        for client in list(self._clients):
            try:
                client.queue.put_nowait(payload)
                self.sent += 1
            except asyncio.QueueFull:
                log.warning("client %s too slow, evicted", client.id)
                dropped.append(client)
        for client in dropped:
            client.evicted.set()
            await self.unregister(client)


hub = Hub()
