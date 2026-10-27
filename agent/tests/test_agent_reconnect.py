"""Reconnection: how long the agent stays deaf after a drop.

`run_once` only ever leaves by an exception -- it reads until the socket dies
-- so the `delay = 1.0` written after it could never run. The backoff that
grew to 60 s during one bad night (a closed lid, a hotel captive portal)
stayed at 60 s for the life of the process: every later blip, on a network
that had been fine for days, cost a full minute of deafness. The reconnect
snapshot does catch up what landed meanwhile, but an alert said a minute late
is the one this agent exists to say early.
"""

from __future__ import annotations

import asyncio

import pytest

from sosforge_agent import agent as agent_module
from sosforge_agent.agent import Agent
from sosforge_agent.config import Config


def _agent() -> Agent:
    return Agent(Config(lat=35.68, lon=139.77, url="ws://x/ws"), notifier=lambda *a, **k: True)


async def _drive(agent: Agent, monkeypatch, script: list[bool], sleeps: int) -> list[float]:
    """Runs `run_forever` over a script of connection attempts (True = the
    server delivered frames before the drop, False = it never answered) and
    returns the delays it slept between them."""
    attempts = iter(script)
    slept: list[float] = []

    async def run_once() -> None:
        if next(attempts):
            agent.handle({"type": "snapshot", "events": [], "server_time": None})
            agent.delivered = True
        raise ConnectionError("dropped")

    async def sleep(delay: float) -> None:
        slept.append(delay)
        if len(slept) >= sleeps:
            raise asyncio.CancelledError

    monkeypatch.setattr(agent, "run_once", run_once)
    monkeypatch.setattr(agent_module.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await agent.run_forever()
    return slept


async def test_run_once_marks_a_connection_that_delivered():
    """The real `run_once`, against a real server that sends a snapshot and
    closes: `delivered` is what the backoff reset reads, so it must come
    from the socket, not from the fake above."""
    import json

    import websockets

    async def handler(ws) -> None:
        await ws.send(json.dumps({"type": "snapshot", "events": [], "server_time": None}))
        await ws.close(code=1013, reason="too_many_connections")

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        agent = Agent(Config(lat=0, lon=0, url=f"ws://127.0.0.1:{port}/ws"), lambda *a, **k: True)
        with pytest.raises(websockets.ConnectionClosed):
            await agent.run_once()
    assert agent.delivered


async def test_failed_attempts_back_off_up_to_a_minute(monkeypatch):
    slept = await _drive(_agent(), monkeypatch, [False] * 8, sleeps=8)
    assert slept == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


async def test_a_connection_that_worked_resets_the_backoff(monkeypatch):
    # a bad night, then a session that delivered, then a blip
    slept = await _drive(_agent(), monkeypatch, [False] * 7 + [True, False], sleeps=9)
    assert slept[6] == 60.0
    assert slept[7] == 1.0, "a drop after a working session waited out the old backoff"
    assert slept[8] == 2.0
