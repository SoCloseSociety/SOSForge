"""One task per block, one budget for the report.

The composer starts every block at once and waits `budget` seconds
(`SOS_PLACE_BUDGET_SECONDS`, 8). What finished is returned; what did not
comes back `unavailable` with code `budget` (or `warming` for a global file
read for the first time), and the response says `complete: false` with
`retry_after_s: 5`. The block tasks are NOT cancelled: their fetches keep
running to their own timeouts and fill the cache, so the panel's refetch
finds them. A dead or slow upstream therefore never holds another block.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.place import block as B
from app.place.context import Context
from app.place.fetch import FetchError, Refused, spawn

log = logging.getLogger(__name__)

RETRY_AFTER_S = 5
Builder = Callable[[Context], Awaitable[B.Block]]


@dataclass
class Report:
    body: dict[str, Any] | None
    etag: str | None
    refused: bool = False
    retry_after: float = 0.0


async def _guard(spec: B.Spec, builder: Builder, ctx: Context) -> B.Block | None:
    """A block's own failure stays in that block. None = refused (429)."""
    try:
        return await builder(ctx)
    except Refused:
        return None
    except FetchError as exc:
        return B.unavailable(spec, exc.code, exc.reason)
    except Exception as exc:  # noqa: BLE001 -- one bad payload must not sink the report
        log.warning("place block %s: %s", spec.id, type(exc).__name__)
        return B.unavailable(spec, "upstream_parse", f"{type(exc).__name__} building {spec.id}")


async def run(
    ctx: Context, builders: list[tuple[B.Spec, Builder]], budget_s: float
) -> tuple[dict[str, B.Block], bool]:
    """Every block, and whether all of them finished inside the budget.
    Raises `Refused` when a block needed a new fetch and the client's
    budget was empty."""
    loop = asyncio.get_running_loop()
    tasks = {
        spec.id: spawn(_guard(spec, fn, ctx), f"place-block:{spec.id}") for spec, fn in builders
    }
    deadline = loop.time() + budget_s
    waiting: set[asyncio.Future[Any]] = set(tasks.values())
    while waiting and not ctx.refused:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        _, waiting = await asyncio.wait(
            waiting, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
    if ctx.refused:
        raise Refused(ctx.retry_after)
    blocks: dict[str, B.Block] = {}
    complete = True
    for spec, _ in builders:
        task = tasks[spec.id]
        result = task.result() if task.done() else None
        if result is None:
            complete = False
            code = ctx.pending.get(spec.id, "budget")
            reason = (
                "first read of this global file still in flight"
                if code == "warming"
                else f"not finished within the report's {budget_s:g} s budget"
            )
            result = B.unavailable(spec, code, reason)
        blocks[spec.id] = result
    return blocks, complete


def body(ctx: Context, blocks: dict[str, B.Block], complete: bool) -> dict[str, Any]:
    now = datetime.now(UTC)
    counts = {"ok": 0, "unavailable": 0, "not_applicable": 0}
    for b in blocks.values():
        counts[b.status] += 1
        if b.status == "ok" and (b.as_of_kind or b.spec.as_of_kind) == "live":
            b.as_of = now  # a live read is as of the answer itself
    radius: float | int = int(ctx.radius_km) if ctx.radius_km.is_integer() else ctx.radius_km
    return {
        "api": 2,
        "server_time": B.iso(now),
        "query": {"lat": ctx.lat, "lon": ctx.lon, "radius_km": radius, "lang": ctx.lang},
        "place": ctx.place.public(),
        "complete": complete,
        "retry_after_s": None if complete else RETRY_AFTER_S,
        "counts": counts,
        "blocks": {bid: b.public(now, ctx.lang) for bid, b in blocks.items()},
    }


def _identity(bid: str, blk: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in blk.items() if k != "fetched_at"}
    if blk.get("as_of_kind") == "live":
        # a live block is as of the answer itself: its as_of IS server_time
        out.pop("as_of", None)
    data = out.get("data")
    if bid == "alerts_now" and isinstance(data, dict):
        # an event's identity as /api/events hashes it: id, revision and
        # updated_at. `last_seen` moves on every poll of an unchanged alert
        # (measured: two warm reports 2 s apart had different ETags).
        out["data"] = {
            **data,
            "events": [
                [e.get("id"), e.get("revision"), e.get("updated_at")] for e in data["events"]
            ],
        }
    return out


def etag(payload: dict[str, Any]) -> str:
    """sha1 of the canonical body minus `server_time` and every
    `fetched_at`: a 304 then means nothing in the report changed, `stale`,
    `complete`, `counts`, `missing` and `error` included."""
    stripped = {k: v for k, v in payload.items() if k != "server_time"}
    stripped["blocks"] = {bid: _identity(bid, blk) for bid, blk in payload["blocks"].items()}
    text = json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f'"{hashlib.sha1(text.encode()).hexdigest()[:20]}"'
