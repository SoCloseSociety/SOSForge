"""What changed since a client last asked.

A phone that cannot keep a websocket open polls every fifteen minutes on
mobile data. Re-reading 300 events each time to learn that two were revised
is what this avoids: the hub records every message it fans out, in order,
and `/api/events?since=` replays only the ids touched after the cursor.

The hard part is not the additions, it is the departures. A REST client has
no server-side memory of what it holds, yet an event relocated OUT of its
zone, downgraded below its floor, or cancelled must still reach it -- or the
notification stays on its lock screen forever. So every entry also records
the event's matching fields as they were just BEFORE that change. The first
entry after a cursor therefore says what the client could have seen at the
cursor, and `main.py` answers "was it in your scope then, is it now".

The log is in memory and starts with the process. A cursor is opaque and
carries the process `epoch`: one minted by a previous process, or older than
the retained window, is answered with a reset -- the full read the client
would have done anyway -- never with a silently incomplete delta.
"""

from __future__ import annotations

import re
import secrets
from collections import OrderedDict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.event import Event

# Entries kept. Re-polled alerts that fingerprint as noop never reach the hub,
# so this is real changes only: measured on the live feed at roughly 20 to 150
# a minute, which is many hours of history.
MAX_ENTRIES = 50_000
# Last known state per id. Above the ring size (5000) with room for churn.
MAX_STATE = 20_000

CURSOR = re.compile(r"^[0-9a-f]{8}:[0-9]{1,12}$")


@dataclass(frozen=True)
class View:
    """The fields a scope or a base filter can look at, nothing else."""

    lat: float | None
    lon: float | None
    severity: str
    kind: str
    country_code: str | None
    magnitude: float | None
    primary: bool
    bbox: Sequence[float] | None = None

    @classmethod
    def of_public(cls, event: dict[str, Any]) -> View:
        cluster = event.get("cluster_id")
        return cls(
            lat=event.get("lat"),
            lon=event.get("lon"),
            severity=str(event.get("severity") or "info"),
            kind=str(event.get("kind") or "other"),
            country_code=event.get("country_code"),
            magnitude=event.get("magnitude"),
            primary=not (cluster and cluster != event.get("id")),
            bbox=event.get("bbox"),
        )

    @classmethod
    def of_event(cls, event: Event) -> View:
        return cls(
            lat=event.lat,
            lon=event.lon,
            severity=event.severity.value,
            kind=event.kind.value,
            country_code=event.country_code,
            magnitude=event.magnitude,
            primary=not (event.cluster_id and event.cluster_id != event.id),
            bbox=event.bbox,
        )


# What the log knows about an event just before a change.
NEW = "new"  # it did not exist: nobody can have held it
KNOWN = "known"  # it existed, and `prev` is what it looked like
UNKNOWN = "unknown"  # it existed but was never seen by the log: assume held


@dataclass(frozen=True)
class Entry:
    seq: int
    event_id: str
    removed: bool
    before: str
    prev: View | None


@dataclass(frozen=True)
class Touched:
    """One id touched after a cursor: its state at the cursor, its fate now."""

    event_id: str
    before: str
    prev: View | None
    removed: bool


@dataclass(frozen=True)
class Delta:
    touched: list[Touched]
    reset: str | None  # None, or why the cursor cannot be served


class ChangeLog:
    def __init__(self, maxlen: int = MAX_ENTRIES, max_state: int = MAX_STATE):
        self.epoch = secrets.token_hex(4)
        self._entries: deque[Entry] = deque(maxlen=maxlen)
        self._state: OrderedDict[str, View] = OrderedDict()
        self._max_state = max_state
        self._seq = 0
        # the seq of the last entry that fell off the deque: a cursor below it
        # cannot be served completely
        self._floor = 0

    @property
    def head(self) -> int:
        return self._seq

    def cursor(self, seq: int | None = None) -> str:
        return f"{self.epoch}:{self._seq if seq is None else seq}"

    def seed(self, events: Iterable[Event]) -> None:
        """What the store holds before the first broadcast. Events restored
        from the journal or the backfill go in quietly, without a message;
        without this, their first revision would have no `prev` and the log
        would have to guess whether a client held them."""
        for event in events:
            self._remember(event.id, View.of_event(event))

    def record(self, message: dict) -> None:
        """One hub message. Ticks carry no change and are not recorded."""
        kind = message.get("type")
        if kind in ("event", "update"):
            event = message.get("event") or {}
            event_id = event.get("id")
            if not event_id:
                return
            event_id = str(event_id)
            prev = self._state.get(event_id)
            # an `event` message is a first insertion; an `update` of an id
            # the log never saw is a quiet one it cannot vouch for
            new_kind = NEW if kind == "event" else UNKNOWN
            before = KNOWN if prev is not None else new_kind
            self._append(Entry(self._seq + 1, event_id, False, before, prev))
            self._remember(event_id, View.of_public(event))
        elif kind == "purge":
            for raw_id in message.get("ids") or ():
                event_id = str(raw_id)
                prev = self._state.pop(event_id, None)
                before = KNOWN if prev is not None else UNKNOWN
                self._append(Entry(self._seq + 1, event_id, True, before, prev))

    def _remember(self, event_id: str, view: View) -> None:
        self._state[event_id] = view
        self._state.move_to_end(event_id)
        while len(self._state) > self._max_state:
            self._state.popitem(last=False)

    def _append(self, entry: Entry) -> None:
        if len(self._entries) == self._entries.maxlen:
            self._floor = self._entries[0].seq
        self._seq = entry.seq
        self._entries.append(entry)

    @staticmethod
    def well_formed(cursor: str) -> bool:
        return bool(CURSOR.match(cursor))

    def parse(self, cursor: str) -> tuple[int | None, str | None]:
        """(seq, None) when the cursor can be served, (None, reason) otherwise."""
        if not self.well_formed(cursor):
            return None, "unknown_cursor"
        epoch, _, seq_text = cursor.partition(":")
        if epoch != self.epoch:
            return None, "unknown_cursor"
        seq = int(seq_text)
        if seq > self._seq:
            return None, "unknown_cursor"
        if seq < self._floor:
            return None, "expired"
        return seq, None

    def since(self, cursor: str) -> Delta:
        """Ids touched after the cursor, each listed once: the state it had AT
        the cursor (from its first entry after it) and whether its last entry
        is a removal."""
        seq, reason = self.parse(cursor)
        if seq is None:
            return Delta([], reason)
        first: dict[str, Entry] = {}
        last_removed: dict[str, bool] = {}
        for entry in self._entries:
            if entry.seq <= seq:
                continue
            first.setdefault(entry.event_id, entry)
            last_removed[entry.event_id] = entry.removed
        return Delta(
            [
                Touched(event_id, e.before, e.prev, last_removed[event_id])
                for event_id, e in first.items()
            ],
            None,
        )
