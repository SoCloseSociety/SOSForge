"""PR #5 final review: "Last new event" must not be stamped by a baseline.

Driven through `MeteoalarmSource.run()` itself, in its own order (country by
country), on the real feeds of `fixtures/meteoalarm_freshness_2026-10-27.json`.
`select()` is pinned to the capture time, or the warnings would expire under
the test. A feed "failing" is the transport answering 503 for it.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.sources.alerts_world as alerts_world
import app.sources.tsunami as tsunami
from app.sources.alerts_world import MeteoalarmSource
from app.sources.base import SourceHealth
from app.sources.tsunami import TsunamiSource

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


# ------------------------------------------- recheck of 0cc11f6: per feed
#
# The baseline was ONE flag for the whole source: the centre coming back set
# it, and the OTHER centre's genuinely new bulletin in the same cycle was
# never stamped. Real Atom feeds: PAAQ (`tsunami_PAAQAtom_2026-10-26.xml`,
# byte-identical on 2026-10-27) and PHEB (`tsunami_PHEBAtom_2026-10-27.xml`).

PAAQ_XML = (FX / "tsunami_PAAQAtom_2026-10-26.xml").read_bytes()
PHEB_XML = (FX / "tsunami_PHEBAtom_2026-10-27.xml").read_bytes()
# the PHEB feed before its bulletin was issued: the same document, no entry
PHEB_BEFORE = re.sub(rb"<entry>.*?</entry>", b"", PHEB_XML, flags=re.S)


async def _run_tsunami(monkeypatch, cycles: list[dict[str, bytes | None]]):
    """`cycles`: centre -> its Atom document (None: 503). Bulletin texts
    answer 404, which the source survives. Returns `last_event_at` at the end
    of every cycle."""
    source = TsunamiSource(poll_seconds=0)
    per_cycle = len(source.feeds)
    feeds = {url: center for center, url in source.feeds.items()}
    calls: list[str] = []
    marks: list[datetime | None] = []
    done = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        center = feeds.get(str(request.url))
        if center is None:
            return httpx.Response(404)  # a text bulletin
        if done.is_set():
            return httpx.Response(503)
        if calls and len(calls) % per_cycle == 0:
            marks.append(source.health.last_event_at)
            if len(marks) == len(cycles):
                done.set()
                return httpx.Response(503)
        calls.append(center)
        document = cycles[(len(calls) - 1) // per_cycle][center]
        return httpx.Response(503) if document is None else httpx.Response(200, content=document)

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_CLIENT(*args, **kwargs)

    async def emit(_event: Any) -> None:
        return None

    monkeypatch.setattr(tsunami.httpx, "AsyncClient", client)
    task = asyncio.create_task(source.run(source.observed(emit)))
    try:
        await asyncio.wait_for(done.wait(), timeout=10)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return marks


@pytest.mark.asyncio
async def test_a_returning_centre_does_not_hide_the_other_centres_new_bulletin(monkeypatch):
    assert list(TsunamiSource().feeds) == ["PAAQ", "PHEB"]  # PAAQ is read first
    down = {"PAAQ": None, "PHEB": PHEB_BEFORE}
    back = {"PAAQ": PAAQ_XML, "PHEB": PHEB_XML}  # PAAQ back, PHEB's bulletin new
    marks = await _run_tsunami(monkeypatch, [down, back])
    assert marks[0] is None
    assert marks[1] is not None  # stamped by PHEB's bulletin, not hidden by PAAQ


@pytest.mark.asyncio
async def test_a_returning_centre_alone_lists_its_baseline(monkeypatch):
    down = {"PAAQ": None, "PHEB": PHEB_XML}
    back = {"PAAQ": PAAQ_XML, "PHEB": PHEB_XML}
    marks = await _run_tsunami(monkeypatch, [down, back, back])
    assert marks == [None, None, None]


def test_an_id_also_listed_by_a_feed_that_was_up_is_new_data():
    # BMKG lists one quake in several feeds: a new quake in the cycle one of
    # them comes back is still news, because a feed that was up lists it too
    health = SourceHealth("bmkg")
    health.feed_failed("gempadirasakan", ConnectionError("down"))
    health.feed_ok("autogempa", ["bmkg:old"])
    health.observe("bmkg:old", datetime.now(UTC))
    health.ok(1)
    health.feed_ok("gempadirasakan", ["bmkg:felt-earlier", "bmkg:new"])
    health.feed_ok("autogempa", ["bmkg:new"])
    health.observe("bmkg:felt-earlier", datetime.now(UTC))
    assert health.last_event_at is None  # gempadirasakan's own baseline
    health.observe("bmkg:new", datetime.now(UTC))
    assert health.last_event_at is not None
