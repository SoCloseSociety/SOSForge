"""The lifespan's shutdown must end, even when a cancel does not arrive.

The CI backend job hung 5 h 25 min on 2026-10-28, inside the one test that
runs the real lifespan (`with TestClient(app)`, test_audit5_security.py).
The shutdown cancelled each task once and waited for all of them with no
timeout. anyio 4.15.1, which httpx connects through, loses such a cancel when
it lands in the loop step where `connect_tcp`'s winning attempt cancels its
own task group (`tg.cancel_scope.cancel()` in `try_connect`): the host task
wakes with ONE CancelledError, anyio's, and the group's scope swallows it as
its own. The source polls on, and the wait never returns. Measured offline
by holding the real connects: lose order 10/10 hung (this branch and
origin/main 8111e5a), keep order 0/10; under uvicorn, still running 30 s
after SIGTERM, 3/3.

`ConnectRace` is a source whose poll has that shape, through anyio's public
API; the test drives the shutdown into the losing order. It is bounded by
`asyncio.timeout`: the unfixed shutdown fails here, it does not hang.
"""

from __future__ import annotations

import asyncio
import time

import anyio
import pytest

from app.sources.base import Emit, Source

# one 0.5 s re-cancel round is needed here; the unfixed shutdown never ends
SHUTDOWN_BOUND_S = 3.0


class ConnectRace(Source):
    """Polls the way `anyio.connect_tcp` connects: an attempt in a task group,
    which cancels the group's scope once its handshake completes
    (`connected`), while the host waits in the group."""

    name = "connect-race"

    def __init__(self) -> None:
        super().__init__()
        self.armed = asyncio.Event()
        self.connected = asyncio.Event()
        self.polls = 0
        self.lost: list[int] = []  # cancel requests outstanding after a poll

    async def run(self, emit: Emit) -> None:
        while True:
            self.polls += 1
            async with anyio.create_task_group() as tg:

                async def attempt() -> None:
                    self.armed.set()
                    await self.connected.wait()
                    tg.cancel_scope.cancel()

                tg.start_soon(attempt)
                await anyio.sleep_forever()
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                self.lost.append(task.cancelling())
            await asyncio.sleep(60)  # until the next poll


async def test_a_cancel_anyio_swallows_does_not_hold_the_shutdown(v2, monkeypatch, tmp_path):
    main = v2.main
    race = ConnectRace()

    async def no_backfill(url: str, emit: Emit) -> int:
        return 0

    async def idle() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "sources", main.sources)  # the lifespan rebinds it
    monkeypatch.setattr(main, "build_sources", lambda: [race])
    monkeypatch.setattr(main, "backfill", no_backfill)
    monkeypatch.setattr(main.climate, "run", idle)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "enable_felt_reports", False)
    monkeypatch.setattr(main.settings, "enable_place", False)

    lifespan = main.lifespan(main.app)
    await lifespan.__aenter__()
    (source_task,) = [t for t in asyncio.all_tasks() if t.get_name() == "src:connect-race"]
    await race.armed.wait()  # the attempt and its host are both parked
    # The handshake completes, and the shutdown starts one callback later in
    # the same loop step: the attempt cancels its group's scope first, so the
    # lifespan's Task.cancel() finds the host's waiter already cancelled.
    race.connected.set()
    await asyncio.sleep(0)
    started = time.monotonic()
    try:
        async with asyncio.timeout(SHUTDOWN_BOUND_S):
            await lifespan.__aexit__(None, None, None)
    except TimeoutError:
        pytest.fail(
            f"the shutdown was still waiting after {SHUTDOWN_BOUND_S} s: "
            f"cancel lost {race.lost}, {race.polls} poll(s)"
        )
    elapsed = time.monotonic() - started

    # the race really was lost: the poll returned with the cancel outstanding
    assert race.lost == [1], "anyio delivered the cancel: this test no longer plays the race"
    assert source_task.cancelled()
    assert race.polls == 1
    assert elapsed < SHUTDOWN_BOUND_S
