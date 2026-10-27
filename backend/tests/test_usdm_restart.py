"""PR #5 final review: a USDM release that lands while the server is down.

Two real consecutive weekly maps (their D4 features): the map valid
2026-10-13 (`usdm_20261013_D4.json`) and the one valid 2026-10-20
(`usdm_current_2026-10-22_D4.json`). Each driven through `UsdmSource.run()`,
one process per map, sharing only what a restart shares: the data dir.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.sources.usdm as usdm
from app.sources.base import Source
from app.sources.usdm import UsdmSource

FX = Path(__file__).parent / "fixtures"
PREVIOUS = json.loads((FX / "usdm_20261013_D4.json").read_text(encoding="utf-8"))
CURRENT = json.loads((FX / "usdm_current_2026-10-22_D4.json").read_text(encoding="utf-8"))
PREVIOUS_STAMP = PREVIOUS["last_modified"]
CURRENT_STAMP = "Thu, 22 Oct 2026 10:15:57 GMT"  # the header of usdm_current.json
REAL_CLIENT = httpx.AsyncClient  # before any test patches it


async def _one_cycle(monkeypatch, payload: dict, stamp: str):
    """A fresh process: one poll of `payload`; returns (emitted ids, withdrawn)."""
    source = UsdmSource(poll_seconds=3600)
    emitted: list[str] = []
    withdrawn: list[tuple[str, str]] = []

    async def emit(event: Any) -> None:
        emitted.append(event.id)

    async def retract(event_id: str, reason: str) -> None:
        withdrawn.append((event_id, reason))

    source.retract = retract

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers={"Last-Modified": stamp})

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_CLIENT(*args, **kwargs)

    monkeypatch.setattr(usdm.httpx, "AsyncClient", client)
    task = asyncio.create_task(source.run(emit))
    try:
        for _ in range(1000):
            if source.health.last_ok is not None:
                break
            await asyncio.sleep(0.005)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert source.health.last_ok is not None, source.health.last_error
    return emitted, withdrawn


@pytest.mark.asyncio
async def test_a_restart_between_two_releases_still_withdraws_last_week(monkeypatch, tmp_path):
    monkeypatch.setattr(Source, "memory_path", lambda self: tmp_path / f"memory-{self.name}.json")
    before, _ = await _one_cycle(monkeypatch, PREVIOUS, PREVIOUS_STAMP)
    after, withdrawn = await _one_cycle(monkeypatch, CURRENT, CURRENT_STAMP)
    assert len(before) == 9 and len(after) == 7
    gone = sorted(set(before) - set(after))
    assert len(gone) == 7  # only 2 of the 9 D4 regions kept their id
    assert withdrawn == [(event_id, "stale") for event_id in gone]


@pytest.mark.asyncio
async def test_a_restart_on_the_same_map_withdraws_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(Source, "memory_path", lambda self: tmp_path / f"memory-{self.name}.json")
    await _one_cycle(monkeypatch, CURRENT, CURRENT_STAMP)
    _, withdrawn = await _one_cycle(monkeypatch, CURRENT, CURRENT_STAMP)
    assert withdrawn == []
