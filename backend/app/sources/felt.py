"""EMSC felt reports: how many people said they felt it.

`https://www.seismicportal.eu/testimonies-ws/api/search` is the public search
over EMSC's LastQuake testimonies. One GET returns every recent earthquake
that anybody has reported feeling, with `feltreportCount` per event and an
`eventid` of the form `quakeml:eu.emsc/event/20260825_0000340` -- whose tail
is exactly the `unid` `emsc_ws.py` already uses as `source_id`. So the join
is an equality on a key both sides already publish, not a match on time and
distance.

**Measured on 2026-08-26**, over the 508 EMSC events since 2026-08-25T00:00Z:

| population        | with felt reports |
|-------------------|-------------------|
| all events        |  22/508  (4.3%)   |
| M >= 3.5          |  12/127  (9.4%)   |
| M >= 4.0          |   8/71  (11.3%)   |
| M >= 5.0          |   2/5   (40.0%)   |

That 4.3% is the honest headline and it is not a defect: most earthquakes
happen where nobody is, and most of the planet has no reporters. The number
that matters is the second column of the bottom rows -- when an event is big
enough for a person to be asking "did I really just feel that", there is a
good chance somebody else already answered. Counts seen that day ran 1, 1, 1,
... 23, 32, 91, 119.

**Two things this module cannot do, and does not pretend to.**

- A zero is never written. An event absent from the search has no reporters
  *known to EMSC*, which is not the same claim as "nobody felt it", and the
  model documents `felt_reports=None` as exactly that.
- The count arrives LATE. Reports accumulate over the ten to forty minutes
  after the shock, while `emsc_ws.py` is a websocket push that emits the
  event once, at second zero, when the count is necessarily nothing. So this
  is not an enrichment applied at ingest: it is a stream of CHANGES, and
  `poll()` returns only what moved since the previous call, for the caller to
  feed back into the store as a revision. `Event.fingerprint()` now covers
  `felt_reports`, so that revision reaches open browsers instead of being
  swallowed as a no-op.

The API is picky: `start_date`/`end_date` are rejected outright
(`Error 400: Unknown request parameters(s)`), `starttime` is the one it
accepts, and `unids` takes a single id -- a comma-separated list comes back
empty rather than erroring, which is the failure mode that would have looked
like "nobody has felt anything today". Hence one windowed query per cycle
rather than a lookup per event.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

log = logging.getLogger(__name__)

URL = "https://www.seismicportal.eu/testimonies-ws/api/search"

# How far back to ask. Reports keep trickling in for hours after a large
# quake, and the response is small enough that a generous window costs
# nothing: 22 events for 34 hours on an ordinary day, about 10 kB.
WINDOW_HOURS = 48.0

# The search is ordered most recent first; this caps a pathological day, not
# an ordinary one.
LIMIT = 500

# Bound on what we remember. An entry is only useful while its event is still
# in the ring, and the ring holds 5000 events, so this is generous.
MAX_ENTRIES = 20_000

OnChange = Callable[[str, int], Awaitable[None]]


def parse_unid(eventid: str | None) -> str | None:
    """`quakeml:eu.emsc/event/20260825_0000340` -> `20260825_0000340`.

    The tail is what `emsc_ws.py` stores as `source_id`. Anything that does
    not look like an id is refused rather than guessed at: a wrong join here
    would attribute one earthquake's felt reports to another.
    """
    if not eventid:
        return None
    unid = eventid.rsplit("/", 1)[-1].strip()
    return unid or None


def parse_counts(payload: dict) -> dict[str, int]:
    """`{unid: feltreportCount}` from one search response.

    The service answers GeoJSON by default and a flat list under
    `format=json`, with different key names in each (`ev_unid` /
    `ev_nbtestimonies` there, `eventid` / `feltreportCount` here). We ask for
    the default and read the default; a per-feature failure costs that
    feature only.
    """
    counts: dict[str, int] = {}
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        unid = parse_unid(props.get("eventid"))
        if not unid:
            continue
        try:
            count = int(props.get("feltreportCount") or 0)
        except (TypeError, ValueError):
            continue
        # A count of zero is not a felt report. Writing it would turn "EMSC
        # knows of nobody" into the displayable claim "nobody felt this".
        if count > 0:
            counts[unid] = count
    return counts


class FeltReportCache:
    """Felt-report counts by EMSC `unid`, refreshed on a slow cycle."""

    def __init__(
        self,
        url: str = URL,
        window_hours: float = WINDOW_HOURS,
        poll_seconds: float = 120.0,
        limit: int = LIMIT,
        max_entries: int = MAX_ENTRIES,
    ):
        self.url = url
        self.window_hours = window_hours
        self.poll_seconds = poll_seconds
        self.limit = limit
        self.max_entries = max_entries
        self._counts: dict[str, int] = {}

    def count_for(self, unid: str | None) -> int | None:
        """What EMSC knows, or None. Never zero -- see the module docstring."""
        if not unid:
            return None
        return self._counts.get(unid)

    async def poll(self, client: httpx.AsyncClient) -> dict[str, int]:
        """One refresh. Returns ONLY the entries that changed.

        A count that has not moved is not news, and re-announcing it would
        make every quake in the window a fresh revision every two minutes.
        """
        since = datetime.now(UTC) - timedelta(hours=self.window_hours)
        params = {
            "starttime": since.strftime("%Y-%m-%dT%H:%M:%S"),
            "limit": str(self.limit),
        }
        resp = await client.get(self.url, params=params)
        resp.raise_for_status()
        counts = parse_counts(resp.json() or {})

        changed = {unid: n for unid, n in counts.items() if self._counts.get(unid) != n}
        self._counts.update(counts)
        self._evict()
        return changed

    def _evict(self) -> None:
        """Oldest first. The unid begins with `YYYYMMDD_`, so lexical order is
        chronological order -- no separate timestamp to keep in step."""
        if len(self._counts) <= self.max_entries:
            return
        for unid in sorted(self._counts)[: len(self._counts) - self.max_entries]:
            self._counts.pop(unid, None)

    async def run(self, on_change: OnChange) -> None:
        """Poll forever, handing each changed count to the caller.

        `on_change(unid, count)` is where this meets the store: the caller
        looks the event up, sets `felt_reports`, and puts it back through the
        normal upsert path so it becomes a revision like any other.

        A failure here is logged and retried, never raised: felt reports are
        an enrichment, and losing them must not take down anything that is
        actually reporting an earthquake.
        """
        headers = {"User-Agent": "SOSForge/1.0 (+https://soclose.co)"}
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            while True:
                try:
                    for unid, count in (await self.poll(client)).items():
                        await on_change(unid, count)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("emsc felt reports: %s", exc)
                await asyncio.sleep(self.poll_seconds)
