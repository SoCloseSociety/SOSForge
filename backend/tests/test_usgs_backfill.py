"""The startup backfill has a deadline for the whole request.

httpx's `timeout=30.0` bounds each read, not the request: a body that
trickles one byte every 2 s never trips it, and the lifespan's startup
waited for good (the API binds no socket before startup ends). Reproduced
on 2026-10-28 while hunting a CI hang. The server here is a loopback
listener that behaves that way; nothing leaves the machine."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.sources import usgs


async def _trickle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.readuntil(b"\r\n\r\n")
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100000\r\n\r\n"
    )
    with contextlib.suppress(ConnectionError):
        while True:
            writer.write(b" ")
            await writer.drain()
            await asyncio.sleep(0.2)


async def test_a_trickling_body_cannot_hold_the_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usgs, "BACKFILL_DEADLINE_S", 1.0)
    server = await asyncio.start_server(_trickle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    emitted: list[object] = []

    async def emit(event: object) -> None:
        emitted.append(event)

    try:
        async with asyncio.timeout(5.0):  # unfixed, this is where it fails, not hangs
            with pytest.raises(TimeoutError, match="no complete answer within 1 s"):
                await usgs.backfill(f"http://127.0.0.1:{port}/all_day.geojson", emit)
    finally:
        server.close()
    assert emitted == []
