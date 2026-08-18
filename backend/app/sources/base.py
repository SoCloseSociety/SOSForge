"""Contract shared by every ingestion source."""

from __future__ import annotations

import abc
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.models.event import Event, utcnow

log = logging.getLogger(__name__)

Emit = Callable[[Event], Awaitable[None]]
# (event_id, reason) -> whatever was actually removed. The return value is the
# caller's business; a source only needs the withdrawal to happen.
Retract = Callable[[str, str], Awaitable[Any]]


class SourceHealth:
    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.last_ok: datetime | None = None
        self.last_error: str | None = None
        self.events_seen = 0
        self.errors = 0

    def ok(self, n: int = 0) -> None:
        self.connected = True
        self.last_ok = utcnow()
        self.events_seen += n
        self.last_error = None

    def fail(self, exc: BaseException) -> None:
        self.connected = False
        self.errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "connected": self.connected,
            "last_ok": self.last_ok.isoformat() if self.last_ok else None,
            "last_error": self.last_error,
            "events_seen": self.events_seen,
            "errors": self.errors,
        }


class Source(abc.ABC):
    name: str = "source"
    kind: str = "poll"  # poll | push

    def __init__(self) -> None:
        self.health = SourceHealth(self.name)
        # Ids this source has WITHDRAWN. A parser is synchronous and returns
        # events; withdrawing one needs a second channel, because "returning
        # nothing" is indistinguishable from "nothing new" to a store that can
        # only add. Filled by the parser, drained by the run loop.
        self.retractions: list[str] = []
        self.retract: Retract | None = None

    async def flush_retractions(self) -> None:
        if not self.retractions or self.retract is None:
            self.retractions.clear()
            return
        pending, self.retractions = self.retractions, []
        for event_id in pending:
            await self.retract(event_id, "cancelled")

    @abc.abstractmethod
    async def run(self, emit: Emit) -> None:
        """Infinite loop: reads the source and calls `emit` for each event."""

    async def supervise(self, emit: Emit) -> None:
        """Restarts the source forever, with capped exponential backoff."""
        delay = 1.0
        while True:
            try:
                await self.run(emit)
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health.fail(exc)
                log.warning("source %s failed (%s), retrying in %.0fs", self.name, exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
