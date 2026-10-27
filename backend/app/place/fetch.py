"""Every upstream request of the place report goes through here.

One shared client with the identifying User-Agent, and around it what keeps
SOSForge polite and the event loop free:

- a process-wide cap (16 requests in flight) and a per-host cap (6 for the
  Open-Meteo bucket, whose cold European report reads about 30 variables;
  1 for NCEI's single server; 2 for everyone else);
- a per-host token bucket: concurrency alone does not bound a scan of fast
  cache misses, and the per-client limit still lets one address start about
  240 cold reports an hour (WRI's Carto account says 81 a minute);
- a queue bound: a request that finds 16 others waiting for its host, or
  cannot get a slot and a token within its own timeout, fails at once as
  `busy` instead of piling up;
- one timeout per host (connect 5 s everywhere), and a retry only where a
  failure was seen to be transient AND the retry still fits that timeout;
- error reasons built from the status and the host, never from `str(exc)`:
  an httpx `HTTPStatusError` quotes the full URL, and a place report's URLs
  carry the place (NWS puts it in the PATH);
- caches: one LRU per block (single flight, a failure remembered 60 s and
  never longer), and a dedicated 4-thread executor for decoding, so the
  loop that owes every socket a tick each second never parses 5 MB itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx

from app.core import privacy

log = logging.getLogger(__name__)

USER_AGENT = "SOSForge/1.0 (+https://sosforge.soclose.co)"
CONNECT_TIMEOUT = 5.0
QUEUE_MAX = 16
FAILURE_MEMORY_S = 60.0


@dataclass(frozen=True)
class Host:
    timeout: float
    concurrency: int = 2
    per_minute: float | None = None  # token bucket rate; None = not rate limited here
    retries: int = 0
    fresh_retry: bool = False  # the retry opens a new connection


HOSTS: dict[str, Host] = {
    "aviationweather.gov": Host(15.0),
    "api.met.no": Host(10.0, per_minute=600.0),  # the TOS allows 20/s per application
    # NWS abuse policy: retries at a one-minute interval, so never an
    # immediate one; an error falls back to MET for that report.
    "api.weather.gov": Host(10.0, per_minute=60.0),
    "openmeteo.s3.amazonaws.com": Host(15.0, concurrency=6, per_minute=3000.0, retries=1),
    "coastwatch.noaa.gov": Host(15.0, per_minute=60.0),
    "www.ndbc.noaa.gov": Host(15.0),
    "drought.emergency.copernicus.eu": Host(10.0, per_minute=60.0),
    "firms.modaps.eosdis.nasa.gov": Host(45.0, retries=1),  # 5.4 MB files, a reset seen
    "maps.effis.emergency.copernicus.eu": Host(10.0, per_minute=60.0),
    "wri-rw.carto.com": Host(10.0, per_minute=30.0),
    "drmkc.jrc.ec.europa.eu": Host(15.0, per_minute=20.0, retries=1, fresh_retry=True),
    "earthquake.usgs.gov": Host(20.0, per_minute=60.0),
    "www.ngdc.noaa.gov": Host(30.0, concurrency=1, per_minute=20.0),
    "webservices.volcano.si.edu": Host(15.0, per_minute=60.0),
    # IBTrACS, downloaded in the background (333 MB), never in a report
    "www.ncei.noaa.gov": Host(600.0, concurrency=1),
}

# The access to these hosts carries the place in the query or the path: the
# httpx log line keeps only scheme://host for them (core/privacy.py).
privacy.host_only(HOSTS)


class FetchError(Exception):
    """A failed upstream read, with an operator reason that never quotes the
    URL, and the `unavailable` code it maps to."""

    def __init__(self, code: str, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.status = status


class Refused(Exception):
    """The client's own budget is empty and this report would start a new
    upstream fetch: the whole request is answered 429."""

    def __init__(self, retry_after: float) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class _Bucket:
    def __init__(self, per_minute: float) -> None:
        self.rate = per_minute / 60.0
        self.burst = max(2.0, per_minute / 4.0)
        self.tokens = self.burst
        self.at = time.monotonic()

    async def take(self) -> None:
        while True:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
            self.at = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            await asyncio.sleep((1.0 - self.tokens) / self.rate)


@dataclass
class _HostState:
    sem: asyncio.Semaphore
    bucket: _Bucket | None
    waiting: int = 0


class Upstream:
    """The shared client and its limits. Built lazily inside the running loop
    (semaphores bind to it), rebuilt by `reset()` in tests."""

    def __init__(self, max_upstream: int = 16, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.max_upstream = max_upstream
        self._client: httpx.AsyncClient | None = None
        self._global: asyncio.Semaphore | None = None
        self._hosts: dict[str, _HostState] = {}

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            transport=self.transport,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    def _state(self, host: str) -> _HostState:
        state = self._hosts.get(host)
        if state is None:
            policy = HOSTS[host]
            state = _HostState(
                asyncio.Semaphore(policy.concurrency),
                _Bucket(policy.per_minute) if policy.per_minute else None,
            )
            self._hosts[host] = state
        return state

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        accept: tuple[int, ...] = (200, 206),
    ) -> httpx.Response:
        host = urlsplit(url).hostname or ""
        policy = HOSTS[host]  # an unlisted host is a programming error
        if self._global is None:
            self._global = asyncio.Semaphore(self.max_upstream)
        state = self._state(host)
        if state.waiting >= QUEUE_MAX:
            raise FetchError("busy", f"{QUEUE_MAX} requests already waiting for {host}")
        state.waiting += 1
        try:
            async with asyncio.timeout(policy.timeout):
                await state.sem.acquire()
                try:
                    if state.bucket is not None:
                        await state.bucket.take()
                    await self._global.acquire()
                except BaseException:
                    state.sem.release()
                    raise
        except TimeoutError:
            raise FetchError("busy", f"no slot for {host} within {policy.timeout:g} s") from None
        finally:
            state.waiting -= 1
        try:
            return await self._attempts(url, host, policy, headers or {}, method, accept)
        finally:
            self._global.release()
            state.sem.release()

    async def _attempts(
        self,
        url: str,
        host: str,
        policy: Host,
        headers: dict[str, str],
        method: str,
        accept: tuple[int, ...],
    ) -> httpx.Response:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + policy.timeout
        attempts = 1 + policy.retries
        last: FetchError | None = None
        for attempt in range(attempts):
            remaining = deadline - loop.time()
            if remaining < 1.0 and last is not None:
                raise last
            # With a retry allowed, the first attempt leaves a third of the
            # timeout for it: a stall that eats the whole budget would make
            # "retry once" a promise that can never be kept.
            limit = remaining * (2 / 3) if attempt < attempts - 1 else remaining
            fresh = attempt > 0 and policy.fresh_retry
            try:
                response = await self._send(url, method, headers, limit, fresh)
            except httpx.ConnectTimeout:
                # TCP + TLS: 1.3 to 2 s to api.met.no from the dev machine
                last = FetchError(
                    "timeout", f"no connection to {host} within {CONNECT_TIMEOUT:g} s"
                )
                continue
            except (TimeoutError, httpx.TimeoutException):
                last = FetchError("timeout", f"no answer from {host} within {policy.timeout:g} s")
                continue
            except httpx.TransportError as exc:
                last = FetchError("upstream_unreachable", f"{type(exc).__name__} from {host}")
                continue
            if response.status_code in accept or response.status_code == 304:
                if response.status_code == 203:
                    # MET Norway: a deprecated product. Used, and said, without the URL.
                    log.warning("%s answered 203 (deprecated product)", host)
                return response
            raise FetchError(
                "upstream_http", f"HTTP {response.status_code} from {host}", response.status_code
            )
        assert last is not None
        raise last

    async def _send(
        self, url: str, method: str, headers: dict[str, str], limit: float, fresh: bool
    ) -> httpx.Response:
        timeout = httpx.Timeout(limit, connect=min(CONNECT_TIMEOUT, limit))
        async with asyncio.timeout(limit):
            if fresh:
                async with self._new_client() as client:
                    return await client.request(method, url, headers=headers, timeout=timeout)
            return await self.client().request(method, url, headers=headers, timeout=timeout)

    async def download(
        self, url: str, path: Any, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        """Stream a large body to `path` (IBTrACS, 333 MB) under the host's
        caps and timeout. Returns the response (headers only) or raises."""
        host = urlsplit(url).hostname or ""
        policy = HOSTS[host]
        state = self._state(host)
        if self._global is None:
            self._global = asyncio.Semaphore(self.max_upstream)
        async with state.sem, self._global:
            try:
                async with asyncio.timeout(policy.timeout):
                    timeout = httpx.Timeout(60.0, connect=CONNECT_TIMEOUT)
                    async with self.client().stream(
                        "GET", url, headers=headers or {}, timeout=timeout
                    ) as response:
                        if response.status_code == 304:
                            return response
                        if response.status_code != 200:
                            raise FetchError(
                                "upstream_http",
                                f"HTTP {response.status_code} from {host}",
                                response.status_code,
                            )
                        with open(path, "wb") as out:
                            async for chunk in response.aiter_bytes(1 << 20):
                                out.write(chunk)
                        return response
            except (TimeoutError, httpx.TimeoutException):
                raise FetchError(
                    "timeout", f"no answer from {host} within {policy.timeout:g} s"
                ) from None
            except httpx.TransportError as exc:
                raise FetchError(
                    "upstream_unreachable", f"{type(exc).__name__} from {host}"
                ) from None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


upstream = Upstream()


# ------------------------------------------------------------------- caches


@dataclass
class Loaded:
    """What a loader returns: the parsed value, how long it is fresh, and
    optional revalidation hints (Last-Modified, ETag) for the next read."""

    value: Any
    ttl: float
    size: int = 0
    hints: dict[str, str] = field(default_factory=dict)


@dataclass
class Entry:
    value: Any = None
    fetched_at: datetime | None = None
    fresh_until: float = 0.0
    error: FetchError | None = None
    error_until: float = 0.0
    task: asyncio.Task[None] | None = None
    size: int = 0
    hints: dict[str, str] = field(default_factory=dict)

    def fresh(self) -> bool:
        return self.value is not None and time.monotonic() < self.fresh_until

    def remembered_failure(self) -> bool:
        return self.error is not None and time.monotonic() < self.error_until


class Cache:
    """An LRU of entries, bounded by count and optionally by bytes. An entry
    whose fetch is in flight is never evicted (its waiters hold it)."""

    registry: list[Cache] = []

    def __init__(self, name: str, max_entries: int | None = None, max_bytes: int | None = None):
        self.name = name
        # a per-place cache follows SOS_PLACE_CACHE_ENTRIES; a fixed size
        # (one global file, a few probes) does not
        self.scalable = max_entries is None
        self.max_entries = 4096 if max_entries is None else max_entries
        self.max_bytes = max_bytes
        self.entries: OrderedDict[Hashable, Entry] = OrderedDict()
        self.bytes = 0
        Cache.registry.append(self)

    def get(self, key: Hashable) -> Entry:
        entry = self.entries.get(key)
        if entry is None:
            entry = Entry()
            self.entries[key] = entry
            self._evict()
        else:
            self.entries.move_to_end(key)
        return entry

    def peek(self, key: Hashable) -> Entry | None:
        return self.entries.get(key)

    def sized(self, entry: Entry, size: int) -> None:
        self.bytes += size - entry.size
        entry.size = size
        self._evict()

    def _evict(self) -> None:
        def over() -> bool:
            return len(self.entries) > self.max_entries or (
                self.max_bytes is not None and self.bytes > self.max_bytes
            )

        if not over():
            return
        for key in list(self.entries):
            if not over():
                break
            if self.entries[key].task is not None:
                continue
            gone = self.entries.pop(key)
            self.bytes -= gone.size

    def clear(self) -> None:
        self.entries.clear()
        self.bytes = 0


Loader = Callable[[Entry], Awaitable[Loaded]]


async def obtain(
    cache: Cache,
    key: Hashable,
    loader: Loader,
    claim: Callable[[], None],
    *,
    swr: bool = False,
    on_first_wait: Callable[[], None] | None = None,
) -> Entry:
    """The entry for `key`, fetching it if needed.

    - fresh: returned as is;
    - a fetch in flight: joined (free: no rate token), or, with `swr` and a
      previous copy, that copy is returned while the refresh runs;
    - a failure remembered (60 s): returned, with the last good copy if any;
    - otherwise a NEW fetch starts, which is the one thing that spends the
      client's token (`claim`, which raises `Refused` when it is empty).

    The fetch is a task of its own: a report that stops waiting (its budget
    ran out) leaves it running to its own timeout, filling the cache."""
    entry = cache.get(key)
    if entry.fresh():
        return entry
    if entry.task is None and entry.remembered_failure():
        return entry
    if entry.task is None:
        claim()
        entry.task = spawn(_fill(cache, entry, loader), f"place:{cache.name}")
    if swr and entry.value is not None:
        return entry
    if entry.value is None and on_first_wait is not None:
        on_first_wait()
    task = entry.task
    if task is not None:
        await asyncio.shield(task)
    return entry


async def _fill(cache: Cache, entry: Entry, loader: Loader) -> None:
    try:
        loaded = await loader(entry)
    except FetchError as exc:
        entry.error = exc
        entry.error_until = time.monotonic() + FAILURE_MEMORY_S
    except Exception as exc:  # noqa: BLE001 -- a parser bug must become a block error
        host = cache.name
        entry.error = FetchError("upstream_parse", f"{type(exc).__name__} reading {host}")
        entry.error_until = time.monotonic() + FAILURE_MEMORY_S
        log.warning("place %s: %s", cache.name, type(exc).__name__)
    else:
        entry.value = loaded.value
        entry.fetched_at = datetime.now(UTC)
        entry.fresh_until = time.monotonic() + max(0.0, loaded.ttl)
        entry.error = None
        entry.hints = loaded.hints
        cache.sized(entry, loaded.size)
    finally:
        entry.task = None


# ------------------------------------------------------- background + threads

_background: set[asyncio.Task[Any]] = set()


def _retrieve(task: asyncio.Task[Any]) -> None:
    """Every background task has this: asyncio would otherwise log "Task
    exception was never retrieved" with the exception, and an httpx
    exception's text holds the URL."""
    _background.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None and not isinstance(exc, (FetchError, Refused)):
        log.warning("place task %s failed: %s", task.get_name(), type(exc).__name__)


T = TypeVar("T")


def spawn(coro: Awaitable[T], name: str) -> asyncio.Task[T]:
    task: asyncio.Task[T] = asyncio.ensure_future(coro)
    task.set_name(name)
    _background.add(task)
    task.add_done_callback(_retrieve)
    return task


# Decoding (.om, FIRMS and METAR CSV, PNG, any payload over 1 MB) runs here:
# not `asyncio.to_thread`, whose default executor (up to 32 threads) is
# shared with everything else in the process.
EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sos-place")


async def in_thread(fn: Callable[..., T], *args: Any) -> T:
    return await asyncio.get_running_loop().run_in_executor(EXECUTOR, fn, *args)


def configure(max_upstream: int) -> None:
    upstream.max_upstream = max_upstream


def reset(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Tests: a fresh client on `transport`, every cache emptied."""
    global upstream
    upstream = Upstream(upstream.max_upstream, transport)
    for cache in Cache.registry:
        cache.clear()


async def get(url: str, **kwargs: Any) -> httpx.Response:
    """`upstream.get`, through the module attribute so `reset()` is seen."""
    return await upstream.get(url, **kwargs)
