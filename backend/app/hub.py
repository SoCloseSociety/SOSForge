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
