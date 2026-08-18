"""In-memory store: ring buffer + index by id, with optional JSONL append.

No database in v1: a live tracker needs the last hour, not a warehouse. The
JSONL persistence is enough to replay/audit, and Postgres can be plugged in
behind the same interface if history becomes a need.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models.event import Event, Kind, utcnow

log = logging.getLogger(__name__)


class EventStore:
    def __init__(self, maxlen: int = 5000, data_dir: Path | None = None, persist: bool = True):
        self._ring: deque[Event] = deque(maxlen=maxlen)
        self._by_id: dict[str, Event] = {}
        self._lock = threading.RLock()
        self._persist = persist
        self._replaying = False
        self._data_dir = data_dir
        self.counters: dict[str, int] = {}
        # Events whose cluster lost its representative and that were promoted
        # in its place. The store is synchronous and the hub is not, so the
        # promotion is recorded here and drained by whoever can await.
        self._promotions: list[Event] = []

        if self._persist and self._data_dir:
            self._data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------- writing

    def upsert(self, event: Event) -> tuple[Event, str]:
        """Inserts or updates. Returns (event, action) where action = new|update|noop."""
        with self._lock:
            existing = self._by_id.get(event.id)
            if existing is None:
                # A full ring evicts exactly ONE event on append: the head. We
                # look at it BEFORE appending, because afterwards it is gone
                # and the only way left to find out is to rescan everything --
                # which is what this used to do, on every single insert, for
                # the entire life of the process.
                evicted = self._ring[0] if len(self._ring) == self._ring.maxlen else None
                self._by_id[event.id] = event
                self._ring.append(event)
                if evicted is not None:
                    self._forget(evicted)
                self.counters[event.source] = self.counters.get(event.source, 0) + 1
                self._write_jsonl(event, "new")
                return event, "new"

            if existing.fingerprint() == event.fingerprint():
                # nothing new, but the source just mentioned it again: this is
                # exactly what `last_seen` must record
                # during a replay, `last_seen` must stay the journal's value:
                # refreshing it gave every dead alert six more hours of grace
                # at each restart, and masked the purge entirely
                if not self._replaying:
                    existing.last_seen = utcnow()
                return existing, "noop"

            # revision: keep the object in place (thus in the ring) and update it
            event.received_at = existing.received_at
            event.cluster_id = existing.cluster_id or event.cluster_id
            event.revision = existing.revision + 1
            event.updated_at = utcnow()
            event.last_seen = existing.last_seen if self._replaying else utcnow()
            self._by_id[event.id] = event
            for i, e in enumerate(self._ring):
                if e.id == event.id:
                    self._ring[i] = event
                    break
            self._write_jsonl(event, "update")
            return event, "update"

    def _forget(self, evicted: Event) -> None:
        """One event just left the ring. Drop it from the index, and PROMOTE a
        survivor if it was representing a cluster.

        Without that promotion a whole quake dropped out of the feed:
        `primary_only` hides any event whose `cluster_id` is not its own, so
        when EMSC (arrived first, thus evicted first) left while the USGS
        solution stayed, nobody displayed that quake anymore.

        The full-ring pass only runs in that case -- an evicted event that was
        its own cluster's primary -- instead of on every insert.
        """
        # Guard against an id reused by a live entry: a revision replaces the
        # object in place, so the evicted instance may be a stale copy of an
        # id that is still current.
        if self._by_id.get(evicted.id) is evicted:
            self._by_id.pop(evicted.id, None)
        if evicted.cluster_id == evicted.id:
            self._promote_orphans(evicted.id)

    def _promote_orphans(self, dead_cluster: str) -> None:
        """The oldest survivor of the orphaned cluster becomes its primary."""
        new_primary: str | None = None
        for event in self._ring:
            if event.cluster_id != dead_cluster:
                continue
            if new_primary is None:
                new_primary = event.id
            event.cluster_id = new_primary
            if event.id == new_primary:
                self._promotions.append(event)

    def drain_promotions(self) -> list[Event]:
        """Takes the pending promotions. Whoever drains them must broadcast
        them: a promotion that stays server-side is invisible to every tab
        already told the survivor was a duplicate."""
        with self._lock:
            out, self._promotions = self._promotions, []
        return out

    def remove(self, event_ids: Iterable[str]) -> list[Event]:
        """Explicit removal. Returns what was really removed, so the caller
        never announces the disappearance of something that was not there."""
        wanted = set(event_ids)
        with self._lock:
            gone = [e for e in self._ring if e.id in wanted]
            if not gone:
                return []
            self._drop(gone)
        return gone

    def _drop(self, events: list[Event]) -> None:
        """Removes a batch from the ring and the index. Caller holds the lock."""
        dead = {e.id for e in events}
        kept = [e for e in self._ring if e.id not in dead]
        self._ring.clear()
        self._ring.extend(kept)
        for event in events:
            self._by_id.pop(event.id, None)
        for orphaned in {e.cluster_id for e in events if e.cluster_id == e.id}:
            if orphaned:
                self._promote_orphans(orphaned)

    def _write_jsonl(self, event: Event, action: str) -> None:
        # `_replaying` avoids duplicating the journal on every restart: without
        # this guard, re-reading 400 events immediately rewrote them.
        if self._replaying or not (self._persist and self._data_dir):
            return
        day = utcnow().strftime("%Y-%m-%d")
        path = self._data_dir / f"events-{day}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({"action": action, **event.model_dump(mode="json")}, default=str)
                    + "\n"
                )
        except OSError as exc:  # the disk must never kill the live feed
            log.warning("jsonl persistence failed: %s", exc)

    # ------------------------------------------------------------------- reading

    def get(self, event_id: str) -> Event | None:
        with self._lock:
            return self._by_id.get(event_id)

    def recent(
        self,
        limit: int = 300,
        kind: str | None = None,
        min_magnitude: float | None = None,
        since: datetime | None = None,
        primary_only: bool = False,
    ) -> list[Event]:
        with self._lock:
            items: Iterable[Event] = self._ring
            out: list[Event] = []
            for e in items:
                if kind and e.kind.value != kind:
                    continue
                if min_magnitude is not None and (e.magnitude or 0) < min_magnitude:
                    continue
                if since and e.time < since:
                    continue
                # a cluster is only represented by its primary (cluster_id == id)
                if primary_only and e.cluster_id and e.cluster_id != e.id:
                    continue
                out.append(e)
        # sort by event date, descending: the feed shows what just happened,
        # not what was just polled (a 3-day-old bulletin comes back every
        # polling cycle and would squat the top of the list)
        out.sort(key=lambda e: e.time, reverse=True)
        return out[:limit]

    def prune_stale(self, max_silence_hours: float) -> list[Event]:
        """Removes events that no source has mentioned again for a long time.

        Two ways out. An explicit `expires` in the past is the source telling
        us the alert is over -- that is exact, and it wins. Silence is the
        fallback for the sources that publish no expiry: the ingestion horizon
        exempts alerts that are severe AND ongoing, because a red cyclone does
        not expire in three days, but nothing ever made them leave. A source
        that stops mentioning an alert has implicitly said it is finished.
        """
        now = utcnow()
        cutoff = now - timedelta(hours=max_silence_hours)
        with self._lock:
            # What decides is the NATURE of the event, not the flag. An
            # earthquake is a point in time: its source stops listing it the
            # moment it leaves the publication window, which says nothing
            # about the quake -- purging on silence would cut the history to a
            # few hours while the UI offers 24. Everything else here is an
            # interval (a warning, an eruption, a fire, a geomagnetic storm),
            # and a source that stops publishing an interval has said it ended.
            #
            # Keying on `ongoing` alone was measurably not enough: the running
            # instance held 210 severe thunderstorm warnings, none of them
            # still in `/alerts/active`, all of them ingested before that flag
            # was set on NWS -- over for hours, displayed as current.
            #
            # `ongoing` earthquakes stay in scope: swarm and aftershock entries
            # are quakes by kind and intervals by nature, and they say so.
            stale = [
                e
                for e in self._ring
                if (e.expires is not None and e.expires < now)
                or (e.last_seen < cutoff and (e.ongoing or e.kind is not Kind.EARTHQUAKE))
            ]
            if not stale:
                return []
            self._drop(stale)
        return stale

    def stats(self) -> dict:
        now = utcnow()
        with self._lock:
            events = list(self._ring)
        last_hour = [e for e in events if e.time > now - timedelta(hours=1)]
        quakes = [e for e in last_hour if e.kind.value == "earthquake"]
        mags = [e.magnitude for e in quakes if e.magnitude is not None]
        return {
            "total_buffered": len(events),
            "last_hour": len(last_hour),
            "earthquakes_last_hour": len(quakes),
            "max_magnitude_last_hour": max(mags) if mags else None,
            "tsunami_active": sum(1 for e in last_hour if e.tsunami),
            "by_source": dict(self.counters),
            "server_time": now.isoformat(),
        }

    def purge_journals(self, keep_days: int) -> list[Path]:
        """Deletes journals older than `keep_days`.

        The journal grows by about 5 MB per day and was never purged: on a
        service that runs continuously, the volume ends up saturating the
        host's disk -- shared with the other products of the suite.
        """
        if not self._data_dir or not self._data_dir.exists():
            return []
        cutoff = (utcnow() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        removed = []
        for journal in sorted(self._data_dir.glob("events-*.jsonl")):
            day = journal.stem.removeprefix("events-")
            # string comparison: the filename's ISO format sorts correctly
            if len(day) == 10 and day < cutoff:
                try:
                    journal.unlink()
                    removed.append(journal)
                except OSError as exc:
                    log.warning("could not purge journal %s: %s", journal.name, exc)
        return removed

    @staticmethod
    def read_journal(path: Path, max_age_hours: float = 24.0) -> list[Event]:
        """Reads a JSONL back into events. Reads only -- writing them is the
        pipeline's job, so that a restored event goes through dedup like any
        other. It used to store them directly, and the deduper therefore woke
        up blind at every restart."""
        if not path.exists():
            return []
        cutoff = utcnow() - timedelta(hours=max_age_hours)
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                data = json.loads(line)
                data.pop("action", None)
                event = Event.model_validate(data)
            except Exception:
                continue
            if event.time.replace(tzinfo=event.time.tzinfo or UTC) < cutoff:
                continue
            events.append(event)
        return events

    @contextmanager
    def replaying(self) -> Iterator[None]:
        """During a replay the store must not rewrite its own journal, and must
        not refresh `last_seen`: doing so granted every dead alert a fresh six
        hours of grace at each restart and hid the sweep completely."""
        self._replaying = True
        try:
            yield
        finally:
            self._replaying = False
