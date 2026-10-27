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
    """What a source can honestly say about itself.

    Two states this class could not express, and both read as healthy:

    - **half dead.** Several sources are built on more than one feed -- two
      tsunami centres, ten Meteoalarm countries, a catalogue that gives the
      volcanoes their positions. `ok()` used to clear `last_error` as soon as
      ONE of them answered, so PTWC down and NTWC up showed green with no
      error at all. A feed that fails is now remembered by name until that
      same feed answers again, and the source reports itself `degraded`.
    - **connected but silent.** The EMSC websocket calls `ok()` on connect
      and its pings keep it open: green for hours while delivering nothing.
      `last_event_at` is the last time the source actually handed over at
      least one event, so the reader can see the silence for themselves.
      For a polled source it is the last cycle that returned anything.
    """

    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.last_ok: datetime | None = None
        self.last_error: str | None = None
        self.events_seen = 0
        self.errors = 0
        self.last_event_at: datetime | None = None
        # feed name -> its last error, for the feeds failing RIGHT NOW
        self.failed_feeds: dict[str, str] = {}

    def ok(self, n: int = 0) -> None:
        self.connected = True
        self.last_ok = utcnow()
        self.events_seen += n
        if n:
            self.last_event_at = self.last_ok
        # A partial success does not erase a partial failure.
        self.last_error = self._failed_summary()

    def fail(self, exc: BaseException) -> None:
        self.connected = False
        self.errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"

    def feed_ok(self, feed: str) -> None:
        """One feed of a multi-feed source answered."""
        self.failed_feeds.pop(feed, None)

    def feed_failed(self, feed: str, exc: BaseException) -> None:
        """One feed of a multi-feed source failed. The source as a whole is
        judged by `ok()` / `fail()` at the end of its cycle."""
        self.errors += 1
        self.failed_feeds[feed] = f"{type(exc).__name__}: {exc}"

    def keep_feeds(self, feeds: set[str]) -> None:
        """Forget the failures of feeds this source no longer polls (last
        month's archive page, last year's list): they can never answer again,
        so they would otherwise read as failing forever."""
        for feed in list(self.failed_feeds):
            if feed not in feeds:
                del self.failed_feeds[feed]

    @property
    def degraded(self) -> bool:
        return self.connected and bool(self.failed_feeds)

    def _failed_summary(self) -> str | None:
        if not self.failed_feeds:
            return None
        return "; ".join(f"{feed}: {error}" for feed, error in sorted(self.failed_feeds.items()))

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "connected": self.connected,
            "last_ok": self.last_ok.isoformat() if self.last_ok else None,
            "last_error": self.last_error,
            "events_seen": self.events_seen,
            "errors": self.errors,
            # additive: older clients ignore them
            "degraded": self.degraded,
            "failed_feeds": [
                {"feed": feed, "error": error} for feed, error in sorted(self.failed_feeds.items())
            ],
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
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
        # Ids whose source-stated END has passed (`expires`). Same channel,
        # different word on the wire: an alert that ran its course is
        # "stale", the sweep's own term for it, not "cancelled".
        self.expirations: list[str] = []
        # Ids whose alert the source has LOWERED back to normal (a volcano
        # returned to level 1). The protocol's own word for it is "lifted".
        self.lifts: list[str] = []
        self.retract: Retract | None = None

    async def flush_retractions(self) -> None:
        batches = (
            ("cancelled", self.retractions),
            ("stale", self.expirations),
            ("lifted", self.lifts),
        )
        self.retractions, self.expirations, self.lifts = [], [], []
        if self.retract is None:
            return
        for reason, pending in batches:
            for event_id in pending:
                await self.retract(event_id, reason)

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
