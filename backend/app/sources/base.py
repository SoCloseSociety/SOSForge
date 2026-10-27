"""Contract shared by every ingestion source."""

from __future__ import annotations

import abc
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.event import Event, utcnow

log = logging.getLogger(__name__)

Emit = Callable[[Event], Awaitable[None]]
# (event_id, reason) -> whatever was actually removed. The return value is the
# caller's business; a source only needs the withdrawal to happen.
Retract = Callable[[str, str], Awaitable[Any]]


# Display name of every source, served in `/api/sources` as `label`. The
# frontend kept its own table (`SOURCE_LABEL` in format.ts) and every source
# added after it -- ncs, imo, jma-volcano -- showed in the footer as its raw
# id. The backend that adds a source now names it in the same change. The
# names of the older sources are copied from that table, unchanged.
LABELS: dict[str, str] = {
    "emsc": "EMSC (push)",
    "usgs": "USGS",
    "tsunami": "NOAA tsunami",
    "gdacs": "GDACS",
    "nws": "NWS",
    "volcano": "USGS volcanoes",
    "jma": "JMA",
    "bmkg": "BMKG",
    "geonet": "GeoNet",
    "ingv": "INGV",
    "geofon": "GEOFON",
    "eonet": "NASA EONET",
    "meteoalarm": "Meteoalarm",
    "wmo": "WMO",
    "nhc": "NHC",
    "ash": "Ash SIGMET",
    "afad": "AFAD",
    "jma_eew": "JMA EEW",
    "cenc": "CENC",
    "aftershock": "USGS aftershock",
    "space": "NOAA SWPC",
    "jma-tsunami": "JMA tsunami",
    "ssn": "SSN (Mexico)",
    "nrcan": "NRCan",
    "noa": "NOA (Greece)",
    "phivolcs": "PHIVOLCS",
    "igp": "IGP (Peru)",
    "geonet-volcano": "GeoNet volcanoes",
    "imo": "IMO (Iceland)",
    "imo-volcano": "IMO volcanoes (Iceland)",
    "jma-volcano": "JMA volcanoes",
    "ncs": "NCS (India)",
    "ukea": "Environment Agency (England)",
    "usdm": "US Drought Monitor",
}


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

    Freshness, three fields that must never be confused (lane Q's audit of
    2026-10-27 found the footer saying "Last event 40 s" for BMKG, whose
    newest quake was 29 h old -- a polled source re-lists the same events
    every cycle, and that was being counted as news):

    - `last_event_at`: when this source handed over an event it had NEVER
      handed over before -- new data, first seen. Null until the first such
      event after startup: everything in the first cycle is the baseline.
      The baseline is per FEED: a feed that answers for the first time after
      failing since startup lists its baseline too, not new data (PR #5:
      Greece down in the first cycle, then all of Greece "new" when it came
      back). A multi-feed source that marks health per feed calls
      `ok(partial=True)` for each, so only the cycle's end primes it
      (Meteoalarm's per-country `ok()` primed it after France, and the nine
      other countries' standing warnings were stamped as news at startup).
    - `newest_event_time`: the event time (origin, issue) of the newest event
      the source has delivered -- how recent the source's own data is.
    - `last_delivery_at`: the last poll that returned anything at all, new or
      re-listed. Diagnostics only; it says nothing about freshness.
    """

    # ids remembered per source to tell new from re-listed (oldest dropped)
    MAX_SEEN = 50_000

    def __init__(self, name: str, label: str | None = None):
        self.name = name
        self.label = label or LABELS.get(name) or name
        self.connected = False
        self.last_ok: datetime | None = None
        self.last_error: str | None = None
        self.events_seen = 0
        self.errors = 0
        self.last_event_at: datetime | None = None
        self.newest_event_time: datetime | None = None
        self.last_delivery_at: datetime | None = None
        self._seen: dict[str, None] = {}
        self._primed = False
        # feeds that have answered at least once since startup
        self._answered: set[str] = set()
        # True from a feed's first answer after a failure to the next `ok()`:
        # what is observed meanwhile is that feed's baseline
        self._baseline = False
        # feed name -> its last error, for the feeds failing RIGHT NOW
        self.failed_feeds: dict[str, str] = {}

    def observe(self, event_id: str, event_time: datetime) -> None:
        """Every event the source emits passes here (see `Source.supervise`)."""
        now = utcnow()
        if event_id not in self._seen:
            self._seen[event_id] = None
            if len(self._seen) > self.MAX_SEEN:
                del self._seen[next(iter(self._seen))]
            if self._primed and not self._baseline:
                self.last_event_at = now
        # a time in the future is a source error, not freshness
        if (event_time - now).total_seconds() <= 120 and (
            self.newest_event_time is None or event_time > self.newest_event_time
        ):
            self.newest_event_time = event_time

    def ok(self, n: int = 0, *, partial: bool = False) -> None:
        """A cycle succeeded -- or, with `partial`, one feed of a cycle that
        will call `ok()` again at its end."""
        self.connected = True
        self.last_ok = utcnow()
        self.events_seen += n
        if n:
            self.last_delivery_at = self.last_ok
        # the first successful cycle is the baseline: from now on, an id not
        # seen before is new data
        if not partial:
            self._primed = True
        self._baseline = False
        # A partial success does not erase a partial failure.
        self.last_error = self._failed_summary()

    def fail(self, exc: BaseException) -> None:
        self.connected = False
        self.errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"

    def feed_ok(self, feed: str) -> None:
        """One feed of a multi-feed source answered. Call it BEFORE emitting
        that feed's events: a feed failing since startup that answers for the
        first time lists its baseline, which must not read as new data."""
        if feed in self.failed_feeds and feed not in self._answered:
            self._baseline = True
        self._answered.add(feed)
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
            # additive: the display name; `name` stays the stable id
            "label": self.label,
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
            "newest_event_time": (
                self.newest_event_time.isoformat() if self.newest_event_time else None
            ),
            "last_delivery_at": (
                self.last_delivery_at.isoformat() if self.last_delivery_at else None
            ),
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

    # ------------------------------------------------------------- memory
    #
    # A few kilobytes a source must not lose at a restart: what PHIVOLCS listed
    # last (so a bulletin replaced while the server was down is still
    # withdrawn), which origins INGV has already published under another id.
    # Read and written only by the live loop, never by the parsers, so tests
    # that call parsers touch no disk. Path: next to the journal.

    def memory_path(self) -> Path:
        from app.core.config import settings

        return Path(settings.data_dir) / f"memory-{self.name}.json"

    def load_memory(self) -> dict[str, Any]:
        try:
            data = json.loads(self.memory_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_memory(self, data: dict[str, Any]) -> None:
        path = self.memory_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # a memory is an optimisation, never a dependency
            log.warning("%s: could not save its memory (%s)", self.name, exc)

    def observed(self, emit: Emit) -> Emit:
        """`emit`, plus the freshness bookkeeping of `SourceHealth.observe`."""

        async def wrapped(event: Event) -> None:
            self.health.observe(event.id, event.time)
            await emit(event)

        return wrapped

    async def supervise(self, emit: Emit) -> None:
        """Restarts the source forever, with capped exponential backoff."""
        emit = self.observed(emit)
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
