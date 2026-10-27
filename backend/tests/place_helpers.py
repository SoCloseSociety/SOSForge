"""Offline plumbing for the place report tests: every upstream answer is a
verbatim capture under tests/fixtures/place/, served by an httpx
MockTransport keyed by URL (and by byte range for the `.om` cassettes)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.place import cyclones, fetch, geo, om
from app.place.context import Context

FIXTURES = Path(__file__).parent / "fixtures" / "place"


def fixture(family: str, name: str) -> Path:
    return FIXTURES / family / name


def read_bytes(family: str, name: str) -> bytes:
    return fixture(family, name).read_bytes()


def read_json(family: str, name: str) -> Any:
    return json.loads(fixture(family, name).read_text(encoding="utf-8"))


def meta_of(family: str, name: str) -> dict[str, Any]:
    return read_json(family, name + ".meta.json")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def ctx_for(
    lat: float,
    lon: float,
    now: datetime,
    *,
    radius: float = 300.0,
    lang: str = "en",
    take: Callable[[], float] | None = None,
) -> Context:
    lat, lon = geo.round_point(lat, lon)
    return Context(lat, lon, radius, lang, now, geo.place_of(lat, lon), take_token=take)


Handler = Callable[[httpx.Request], httpx.Response]


class Router:
    """URL -> answer. Unknown URLs answer 599 so a test sees what it missed."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str, str | None], Handler] = {}
        self.prefixes: list[tuple[str, Handler]] = []
        self.seen: list[httpx.Request] = []

    def add(
        self,
        url: str,
        body: bytes | str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        range_: str | None = None,
    ) -> None:
        data = body.encode() if isinstance(body, str) else body

        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, headers=headers or {}, content=data)

        self.routes[(method, url, range_)] = answer

    def handler(
        self, url: str, fn: Handler, method: str = "GET", range_: str | None = None
    ) -> None:
        self.routes[(method, url, range_)] = fn

    def prefix(self, prefix: str, fn: Handler) -> None:
        self.prefixes.append((prefix, fn))

    def cassette(self, folder: Path) -> dict[str, Any]:
        """Every exchange of an Open-Meteo `.om` cassette, served by
        (method, url, Range) exactly as recorded."""
        cas = json.loads((folder / "cassette.json").read_text())
        for e in cas["exchanges"]:
            body = (folder / e["body_file"]).read_bytes()
            self.add(
                e["url"],
                body,
                status=e["status"],
                headers=dict(e["headers"]),
                method=e["method"],
                range_=e["range"],
            )
        return cas

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = str(request.url)
        key = (request.method, url, request.headers.get("range"))
        fn = self.routes.get(key) or self.routes.get((request.method, url, None))
        if fn is None:
            for prefix, candidate in self.prefixes:
                if url.startswith(prefix):
                    fn = candidate
                    break
        if fn is None:
            return httpx.Response(599, content=b"no route in the test router")
        return fn(request)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def install(router: Router | None = None) -> Router:
    router = router or Router()
    fetch.reset(router.transport())
    om._shapes.clear()
    om.decoder_ready.cache_clear()
    cyclones._state.__init__()  # type: ignore[misc]
    return router
