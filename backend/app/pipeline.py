"""The single path every event follows, whatever its source.

    source -> normalize -> filter -> dedupe -> store -> hub -> browsers

One single entry point: if a source ever dedupes badly or floods, this is
where to look.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.countries import resolve as resolve_country
from app.dedupe import Deduper
from app.hub import hub
from app.models.event import Event, Kind, Severity
from app.store.ring import EventStore

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store: EventStore, deduper: Deduper):
        self.store = store
        self.deduper = deduper
        self.ingested = 0
        self.dropped = 0
        self.quiet = False  # during backfill: fill up without waking anyone

    async def emit(self, event: Event) -> None:
        if event.magnitude is not None and event.magnitude < settings.min_magnitude:
            self.dropped += 1
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
        breaking = action == "new" and 0 <= stored.age_seconds <= settings.breaking_seconds

        await hub.broadcast(
            {
                "type": "event" if action == "new" else "update",
                "event": stored.public(),
                "primary": self.deduper.is_primary(stored),
                "breaking": breaking,
            }
        )
        if action == "new" and (stored.severity.value in ("severe", "extreme") or stored.tsunami):
            log.info(
                "ALERT %s %s M%s %s",
                stored.severity.value.upper(),
                stored.kind.value,
                stored.magnitude,
                stored.place,
            )
