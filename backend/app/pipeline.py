"""The single path every event follows, whatever its source.

    source -> normalize -> filter -> dedupe -> store -> hub -> browsers

One single entry point: if a source ever dedupes badly or floods, this is
where to look.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.core.config import settings
from app.countries import resolve as resolve_country
from app.dedupe import Deduper
from app.hub import hub
from app.models.event import Event, Kind, Severity, utcnow
from app.store.ring import EventStore, Removal, severity_rank

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store: EventStore, deduper: Deduper):
        self.store = store
        self.deduper = deduper
        self.ingested = 0
        self.dropped = 0
        self.failed = 0
        self.quiet = False  # during backfill: fill up without waking anyone

    async def emit(self, event: Event) -> None:
        """Ingests one event. NEVER raises for that one event's sake.

        Sources loop `for event in batch: await emit(event)`. A single
        exception used to abort the loop, drop every remaining event of the
        cycle, count as a source failure and start the backoff: one malformed
        row silenced a whole source for a minute. Failures are isolated to the
        event that caused them.
        """
        try:
            await self._ingest(event)
        except asyncio.CancelledError:
            # shutdown, not a failure: it must stay instant
            raise
        except Exception as exc:
            self.failed += 1
            log.warning("%s: event %s rejected by the pipeline (%s)", event.source, event.id, exc)

    async def _ingest(self, event: Event) -> None:
        if event.magnitude is not None and event.magnitude < settings.min_magnitude:
            self.dropped += 1
            return

        # An alert whose SOURCE says it is over does not enter, and if an
        # earlier version is stored, this is the news that it ended. The sweep
        # already purged on `expires`, but only the sweep did: a source that
        # keeps listing an expired alert got it re-added as NEW on its next
        # poll. Measured on production: two JMA tsunami forecasts lifted weeks
        # earlier were ingested 3914 times in seven days, purge then re-add,
        # every five minutes. Same rule as the sweep, so nothing it kept is
        # cut here -- and over those seven days no NWS alert arrived expired.
        if event.expires is not None and event.expires <= utcnow():
            self.dropped += 1
            if self.store.get(event.id) is not None:
                await self.retract(event.id, "expired")
            return

        # A timestamp IN THE FUTURE is a source error (misapplied timezone,
        # drifting clock). Unfiltered, it went through everything: the horizon
        # let it pass (negative age), it was announced as "live" permanently,
        # and the date sort pinned it at the top of the feed forever. We
        # tolerate a small lead (clocks are never exactly in sync) and reject
        # beyond that.
        # Decisive exception: a weather warning is PUBLISHED BEFORE it starts --
        # that is its whole point, the advance notice. Its `onset` is therefore
        # legitimately in the future. Only point-in-time events (a quake either
        # happened or it did not) cannot be dated in advance.
        if not event.ongoing and event.age_seconds < -settings.future_tolerance_seconds:
            self.dropped += 1
            log.warning(
                "%s: timestamp %.0f s in the future, event rejected (%s)",
                event.source,
                -event.age_seconds,
                event.id,
            )
            return

        # Ingestion horizon. Several sources serve a catalog, not a feed: the
        # JMA list goes back more than nine months, GDACS keeps its alerts for
        # weeks. Without a horizon, those archives fill the ring buffer and
        # evict the current events -- the exact opposite of the product.
        # Exception: a SEVERE AND ONGOING alert survives the horizon -- a red
        # cyclone does not expire because it has lasted three days. A quake,
        # though, is instantaneous: past the horizon it is history, even at
        # magnitude 8. Without this nuance, an old Japanese quake at shindo 6
        # was still displayed nine months later.
        # "Ongoing" comes first from the source when it says so (EONET
        # publishes status=open, the NHC only lists active storms). High
        # severity remains a fallback for sources that do not say it.
        ongoing = event.ongoing or (
            event.kind is not Kind.EARTHQUAKE
            and event.severity in (Severity.SEVERE, Severity.EXTREME)
        )
        if event.age_seconds > settings.max_event_age_days * 86400 and not ongoing:
            self.dropped += 1
            return

        # The country is resolved HERE and not in each source: six sources out
        # of ten already provide it, USGS only gives a place text, and one
        # single rule beats ten variants.
        # a source that already knows the country code is authoritative: the
        # WMO aggregate carries it in the identifier, no point re-guessing it
        # from a text
        event.country_code = event.country_code or resolve_country(event.country, event.place)

        # read BEFORE the upsert, which raises it
        peak = self.store.peak(event.id)
        self.deduper.assign(event)
        stored, action = self.store.upsert(event)
        if action == "noop":
            return

        self.ingested += 1
        if self.quiet:
            return

        # "new to the store" is not "just happened". GDACS keeps its alerts for
        # days: on the first cycle, a hundred old events come in at once. They
        # must appear on the map, but they must absolutely not blink or trigger
        # the sound as if they had just landed.
        recent = 0 <= stored.age_seconds <= settings.breaking_seconds
        breaking = action == "new" and recent
        # ...but "an update" is not "nothing new" either. A JMA early warning
        # keeps its EventID across serials: the Bonin Islands warning of
        # 2026-10-25 went moderate -> severe -> extreme in 131 s, every step an
        # update, and the jump to M7.5 sounded nowhere. Clients sound on
        # `breaking or escalated`. The bar is the highest rank the event has
        # EVER held, not the previous version: falling back and climbing to a
        # rank already announced is not news (see EventStore._peaks).
        escalated = (
            action == "update"
            and recent
            and peak is not None
            and (severity_rank(stored.severity) > peak[0] or (stored.tsunami and not peak[1]))
        )

        await hub.broadcast(
            {
                "type": "event" if action == "new" else "update",
                "event": stored.public(),
                "primary": self.deduper.is_primary(stored),
                "breaking": breaking,
                "escalated": escalated,
            }
        )
        # A cluster whose representative was just evicted promoted a survivor.
        # Broadcasting it is not cosmetic: every tab was told that survivor was
        # a duplicate, and `primary_only` hides it, so the quake had silently
        # left the feed on every open map.
        await self._flush_promotions()

        if (action == "new" or escalated) and (
            stored.severity.value in ("severe", "extreme") or stored.tsunami
        ):
            log.info(
                "ALERT%s %s %s M%s %s",
                " (escalated)" if escalated else "",
                stored.severity.value.upper(),
                stored.kind.value,
                stored.magnitude,
                stored.place,
            )

    async def _flush_promotions(self) -> None:
        for promoted in self.store.drain_promotions():
            if self.quiet:
                continue
            await hub.broadcast(
                {
                    "type": "update",
                    "event": promoted.public(),
                    "primary": True,
                    # a promotion is bookkeeping, not news: no halo, no sound
                    "breaking": False,
                    "escalated": False,
                }
            )

    def _forget(self, events: list[Event]) -> None:
        """Whatever leaves the store must leave the deduper's window with it,
        or the window keeps adopting new solutions on behalf of a ghost."""
        if events:
            self.deduper.forget({e.id for e in events})

    async def _announce_purge(self, events: list[Event], reason: str) -> None:
        """Tells the open tabs that events are gone.

        The protocol could add and update, never remove. Everything the sweep
        removed stayed on screen on every tab already open -- dead data
        displayed as live, which is the product's cardinal rule broken in the
        one direction nobody thinks to check.
        """
        if not events:
            return
        self._forget(events)
        if not self.quiet:
            await hub.broadcast({"type": "purge", "ids": [e.id for e in events], "reason": reason})
        await self._flush_promotions()

    async def sweep(self, max_silence_hours: float) -> list[Event]:
        """Removes what is over, and says so."""
        removed = self.store.prune_stale(max_silence_hours)
        await self._announce_purge(removed, "stale")
        return removed

    async def retract(self, event_id: str, reason: str) -> list[Event]:
        """A source withdrew an event. The most critical case is a cancelled
        Japanese early warning: the source simply stops publishing it, and a
        store that can only add keeps the withdrawn alert on the map, red."""
        # the reason is journaled with the removal, so a restart keeps it gone
        removed = self.store.remove([event_id], reason)
        await self._announce_purge(removed, reason)
        if removed:
            log.info("retracted %s (%s)", event_id, reason)
        return removed

    async def replay(self, path: Path) -> int:
        """Replays a journal THROUGH the pipeline.

        It used to be written straight into the store, so restored events
        skipped dedup: the deduper's window started empty at every restart,
        and the EMSC solution restored from the journal no longer matched the
        USGS solution arriving two minutes later. Every restart injected a
        fresh crop of duplicates.

        Removals are applied where they sit in the journal. Without them, a
        cancelled early warning came back at every deploy.
        """
        entries = self.store.read_journal(path)
        if not entries:
            return 0
        was_quiet, self.quiet = self.quiet, True
        before = self.ingested
        try:
            with self.store.replaying():
                for entry in entries:
                    if isinstance(entry, Removal):
                        self._forget(self.store.remove([entry.id], entry.reason))
                        await self._flush_promotions()
                    else:
                        await self.emit(entry)
        finally:
            self.quiet = was_quiet
        return self.ingested - before
