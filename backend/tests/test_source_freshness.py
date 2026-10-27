"""PR #5 final review: "Last new event" must not be stamped by a baseline.

Driven through `MeteoalarmSource.run()` itself, in its own order (country by
country), on the real feeds of `fixtures/meteoalarm_freshness_2026-10-27.json`.
`select()` is pinned to the capture time, or the warnings would expire under
the test. A feed "failing" is the transport answering 503 for it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.sources.alerts_world as alerts_world
from app.sources.alerts_world import MeteoalarmSource
from app.sources.base import SourceHealth

FX = Path(__file__).parent / "fixtures"
REAL = json.loads((FX / "meteoalarm_freshness_2026-10-27.json").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(REAL["now"])
GREECE = REAL["greece"]
# the Greek feed before its red Thessalia warning was published
GREECE_BEFORE_RED = {"warnings": GREECE["warnings"][:2]}
REAL_CLIENT = httpx.AsyncClient  # before any test patches it


class _AtCapture(MeteoalarmSource):
    def select(self, data: Any, country: str, now: datetime | None = None):
        return super().select(data, country, now=NOW)


async def _run(monkeypatch, cycles: list[dict[str, Any]]) -> list[datetime | None]:
    """Run the source for `len(cycles)` cycles; each maps a country to its
    payload (None: 503). Returns `last_event_at` at the end of every cycle."""
    source = _AtCapture(poll_seconds=0, countries=("portugal", "greece"))
    per_cycle = len(source.countries)
    calls: list[str] = []
    marks: list[datetime | None] = []
    done = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if done.is_set():
            return httpx.Response(503)
        if calls and len(calls) % per_cycle == 0:
            marks.append(source.health.last_event_at)  # the previous cycle has closed
            if len(marks) == len(cycles):
                done.set()
                return httpx.Response(503)
        country = request.url.path.rsplit("feeds-", 1)[1]
        calls.append(country)
        payload = cycles[(len(calls) - 1) // per_cycle][country]
        return httpx.Response(503) if payload is None else httpx.Response(200, json=payload)

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_CLIENT(*args, **kwargs)

    async def emit(_event: Any) -> None:
        return None

    monkeypatch.setattr(alerts_world.httpx, "AsyncClient", client)
    task = asyncio.create_task(source.run(source.observed(emit)))
    try:
        await asyncio.wait_for(done.wait(), timeout=10)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return marks


@pytest.mark.asyncio
async def test_the_first_cycle_is_the_baseline_not_the_first_country(monkeypatch):
    # Portugal first (nothing orange), then Greece's standing warnings: the
    # per-country ok() used to prime the source after Portugal, and the footer
    # showed the startup time as Meteoalarm's last new event
    both = {"portugal": REAL["portugal"], "greece": GREECE}
    marks = await _run(monkeypatch, [both, both])
    assert marks == [None, None]


@pytest.mark.asyncio
async def test_a_country_back_after_failing_since_startup_lists_its_baseline(monkeypatch):
    down = {"portugal": REAL["portugal"], "greece": None}
    up = {"portugal": REAL["portugal"], "greece": GREECE}
    marks = await _run(monkeypatch, [down, up, up])
    assert marks == [None, None, None]


@pytest.mark.asyncio
async def test_a_warning_published_after_the_baseline_is_new_data(monkeypatch):
    before = {"portugal": REAL["portugal"], "greece": GREECE_BEFORE_RED}
    after = {"portugal": REAL["portugal"], "greece": GREECE}
    marks = await _run(monkeypatch, [before, after])
    assert marks[0] is None
    assert marks[1] is not None and marks[1] > NOW  # stamped by this process


def test_a_feed_that_answered_before_keeps_counting_new_ids():
    # failing AFTER having answered is an outage, not a baseline: what it
    # lists when it comes back and was never seen is new
    health = SourceHealth("tsunami")
    health.feed_ok("PHEB")
    health.observe("tsunami:a", datetime.now(UTC))
    health.ok(1)
    health.feed_failed("PHEB", ConnectionError("down"))
    health.ok(0)
    health.feed_ok("PHEB")
    health.observe("tsunami:b", datetime.now(UTC))
    assert health.last_event_at is not None
