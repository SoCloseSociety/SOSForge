"""The startup backfill: it loads the real day feed, and it has a deadline
for the whole request.

httpx's `timeout=30.0` bounds each read, not the request: a body that
trickles one byte every 2 s never trips it, and the lifespan's startup
waited for good (the API binds no socket before startup ends). Reproduced
on 2026-10-28 while hunting a CI hang. Both servers here listen on loopback;
nothing leaves the machine. The feed is `all_day.geojson` verbatim, as USGS
served it on 2026-10-28 (generated 16:29:30 UTC, 207 features)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest

from app.sources import usgs

FEED = Path(__file__).parent / "fixtures" / "usgs_all_day_2026-10-28.geojson"

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


@contextlib.asynccontextmanager
async def serving(handler: Handler) -> AsyncIterator[str]:
    """A loopback HTTP server; every connection is closed on the way out."""
    writers: list[asyncio.StreamWriter] = []

    async def tracked(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)
        with contextlib.suppress(ConnectionError):
            await reader.readuntil(b"\r\n\r\n")
            await handler(reader, writer)

    server = await asyncio.start_server(tracked, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/earthquakes/feed/v1.0/summary/all_day.geojson"
    finally:
        server.close()
        for writer in writers:
            writer.close()
        await server.wait_closed()


async def _feed(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    body = FEED.read_bytes()
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/geo+json\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def _trickle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
    writer.write(b"Content-Length: 100000\r\n\r\n")
    while True:
        writer.write(b" ")
        await writer.drain()
        await asyncio.sleep(0.2)


async def test_the_day_feed_is_loaded_oldest_first() -> None:
    emitted: list = []

    async def emit(event) -> None:
        emitted.append(event)

    async with serving(_feed) as url:
        count = await usgs.backfill(url, emit)
    assert count == len(emitted) == 207
    times = [event.time for event in emitted]
    assert times == sorted(times)
    assert all(event.source == "usgs" for event in emitted)


async def test_a_trickling_body_cannot_hold_the_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usgs, "BACKFILL_DEADLINE_S", 1.0)
    emitted: list = []

    async def emit(event) -> None:
        emitted.append(event)

    async with serving(_trickle) as url:
        async with asyncio.timeout(5.0):  # unfixed, this is where it fails, not hangs
            with pytest.raises(TimeoutError, match="no complete answer within 1 s"):
                await usgs.backfill(url, emit)
    assert emitted == []
