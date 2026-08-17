"""Hub de diffusion: fan-out des evenements normalises vers les navigateurs.

Chaque client a sa propre queue bornee. Un client lent est deconnecte plutot que
de faire refluer la pression sur les ingesteurs -- le temps reel prime.
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
        # arme a l'ejection: sans ce signal, la tache d'envoi restait bloquee a
        # jamais sur queue.get() et la websocket restait ouverte cote client,
        # silencieuse -- une connexion "live" qui ne livre plus rien.
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
        log.info("client %s connecte (%d actifs)", client.id, self.client_count)

    async def unregister(self, client: Client) -> None:
        async with self._lock:
            self._clients.discard(client)
        log.info("client %s deconnecte (%d actifs)", client.id, self.client_count)

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, default=str)
        dropped: list[Client] = []
        for client in list(self._clients):
            try:
                client.queue.put_nowait(payload)
                self.sent += 1
            except asyncio.QueueFull:
                log.warning("client %s trop lent, ejecte", client.id)
                dropped.append(client)
        for client in dropped:
            client.evicted.set()
            await self.unregister(client)


hub = Hub()
