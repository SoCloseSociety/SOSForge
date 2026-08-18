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
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models.event import Event, utcnow

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

        if self._persist and self._data_dir:
            self._data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------- writing

    def upsert(self, event: Event) -> tuple[Event, str]:
        """Inserts or updates. Returns (event, action) where action = new|update|noop."""
        with self._lock:
            existing = self._by_id.get(event.id)
            if existing is None:
                self._by_id[event.id] = event
                self._ring.append(event)
                if len(self._ring) == self._ring.maxlen:
                    self._gc()
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

    def _gc(self) -> None:
        """The ring evicted elements: purge the index, and above all PROMOTE a
        survivor in the clusters whose representative just disappeared.

        Without this promotion, a whole quake dropped out of the feed:
        `primary_only` hides any event whose `cluster_id` is not its own, so
        if EMSC (arrived first, evicted first) left while the USGS solution
        stayed, nobody displayed that quake anymore.
        """
        alive = {e.id for e in self._ring}
        for dead in [k for k in self._by_id if k not in alive]:
            self._by_id.pop(dead, None)

        promoted: dict[str, str] = {}
        for event in self._ring:
            cluster = event.cluster_id
            if not cluster or cluster in alive:
                continue
            # the oldest survivor of the cluster becomes the new primary
            new_primary = promoted.setdefault(cluster, event.id)
            event.cluster_id = new_primary

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

        The ingestion horizon exempts alerts that are severe AND ongoing,
        because a red cyclone does not expire in three days. But nothing ever
        made them leave: a dissipated cyclone, a replaced volcanic bulletin,
        stayed displayed forever. A source that stops mentioning an alert has
        implicitly said it is over.
        """
        cutoff = utcnow() - timedelta(hours=max_silence_hours)
        with self._lock:
            # ONLY ongoing alerts are affected. A quake is not "silent": its
            # source normally stops mentioning it as soon as it leaves its
            # publication window. Purging non-ongoing events amounted to
            # keeping only seven hours of history while the UI offers 24 h.
            stale = [e for e in self._ring if e.ongoing and e.last_seen < cutoff]
            if not stale:
                return []
            dead = {e.id for e in stale}
            kept = [e for e in self._ring if e.id not in dead]
            self._ring.clear()
            self._ring.extend(kept)
            for event_id in dead:
                self._by_id.pop(event_id, None)
            self._gc()
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

    def load_backlog(self, path: Path) -> int:
        """Reloads a JSONL at startup (restart without a hole in the map)."""
        if not path.exists():
            return 0
        loaded = 0
        self._replaying = True
        cutoff = utcnow() - timedelta(hours=24)
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                data = json.loads(line)
                data.pop("action", None)
                event = Event.model_validate(data)
            except Exception:
                continue
            if event.time.replace(tzinfo=event.time.tzinfo or UTC) < cutoff:
                continue
            self.upsert(event)
            loaded += 1
        self._replaying = False
        return loaded
