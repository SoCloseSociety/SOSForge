"""`alerts_now`: what SOSForge's own feed has under way around the place.

Read through a provider `main.py` passes in (the store and the sources'
health): this package never imports `app.main`. The scope is the one the
websocket and `/api/events` use (`app/scope.py`), so the report and the
live map cannot disagree about what is nearby. Unplaced events of the
place's countries are COUNTED, never mixed into `events`: a country-wide
warning with no position is not "within 300 km".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.models.event import Event
from app.place import block as B
from app.place.context import Context
from app.scope import SEVERITY_RANK, parse_scope

MAX_EVENTS = 20

SPEC = B.Spec(
    "alerts_now",
    "live",
    "live",
    300.0,
    B.Source(
        "SOSForge live feed",
        "https://sosforge.soclose.co",
        "SOSForge, merged from the public sources listed on the page",
        "per source, see /api/sources",
    ),
)

# () -> (the store's primary events newest first, every source's health)
AlertsProvider = Callable[[], tuple[list[Event], list[dict]]]


def build(ctx: Context, provider: AlertsProvider) -> B.Block:
    events, health = provider()
    if not any(h.get("last_ok") for h in health):
        return B.unavailable(SPEC, "warming", "no source has completed a cycle yet")
    radius = int(ctx.radius_km) if ctx.radius_km.is_integer() else ctx.radius_km
    codes = list(ctx.place.country_codes)
    scope = parse_scope(
        {
            "zones": f"{ctx.lat},{ctx.lon},{radius}",
            "unplaced": ",".join(codes) if codes else "drop",
        }
    )
    assert scope is not None
    kept, _ = scope.apply(events)
    placed = [e for e in kept if e.bbox is not None or (e.lat is not None and e.lon is not None)]
    unplaced = len(kept) - len(placed)
    placed.sort(key=lambda e: (SEVERITY_RANK[e.severity.value], e.time), reverse=True)
    down = sorted(h["name"] for h in health if not h.get("connected") or h.get("degraded"))
    now = datetime.now(UTC)
    return B.ok(
        SPEC,
        now,
        {
            "radius_km": radius,
            "total": len(placed),
            "events": [e.public() for e in placed[:MAX_EVENTS]],
            "unplaced_in_country": unplaced,
            "sources_down": down,
        },
        fetched_at=now,
    )
