"""INFORM's front end drops a request that follows another too closely.

Measured 2026-10-28. From production (the VPS): drmkc.jrc.ec.europa.eu, an
F5 load balancer, answers a request and then closes the connection without a
response ("Server disconnected without sending a response", curl 000 in
0.13 s) for requests that follow too closely; 9 of 10 passed 10 s apart.
From the dev Mac (SOSForge User-Agent, a few spaced requests): a request on
the same session (the kept-alive connection, or a new one carrying its
cookies) 0 or 1.0 s after the previous answer was dropped, 2.0 s, 3.0 s and
8.0 s after it was answered. The 8111e5a code path itself, run cold from the
Mac, put Thailand, Italy and Mauritius at `upstream_unreachable`: the
GetByWorkflowGroup request on the reused connection was dropped, and so was
its immediate fresh retry.

`Frontend` plays that front end over the verbatim captures. The mechanism
tests divide INFORM's times by ten (`_scaled`); the real numbers are held by
the headline test (real policy, real 8 s budget) and by
`test_the_inform_policy_fits_the_report_budget`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time

import httpx
import pytest

from app import place as P
from app.core.config import Settings
from app.place import fetch, risk
from tests.place_helpers import Router, at, ctx_for, install, meta_of, read_bytes

HOST = "drmkc.jrc.ec.europa.eu"
CAPTURES = (
    "inform_workflow_groups.json",
    "inform_workflow_2026mid.json",
    "inform_mid2026_tha.json",
    "inform_mid2026_ita.json",
)
GROUPS_URL = meta_of("risk", "inform_workflow_groups.json")["url"]
SAMUI = P.Query(9.51, 100.01, 300.0, "en")
ROME = P.Query(41.9, 12.5, 300.0, "en")
NOW = at("2026-10-28T05:00:00Z")
# measured from the Mac (module docstring): dropped at 1.0 s, answered at 2.0 s
TOO_CLOSE_S = 2.0


class Frontend:
    """The F5 as measured: a request that starts less than `too_close` s
    after the previous request to the host ENDED is dropped without a
    response, and a dropped request counts as a request. `drop_at` drops the
    requests of those indexes whatever their gap (production: one of ten
    dropped even 10 s apart)."""

    def __init__(self, router: Router, too_close: float) -> None:
        self.too_close = too_close
        self.answers = {meta_of("risk", n)["url"]: read_bytes("risk", n) for n in CAPTURES}
        self.log: list[tuple[float, float, str, str]] = []  # start, end, outcome, path
        self.drop_at: set[int] = set()
        router.prefix(f"https://{HOST}/", self)  # type: ignore[arg-type]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        now = asyncio.get_running_loop().time()
        index = len(self.log)
        last_end = self.log[-1][1] if self.log else None
        path = request.url.path.rsplit("/", 1)[-1]
        if "Scores" in request.url.path:
            path = f"Scores {request.url.params.get('Iso3')}"
        if index in self.drop_at or (last_end is not None and now - last_end < self.too_close):
            self.log.append((now, now, "dropped", path))
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.", request=request
            )
        body = self.answers.get(str(request.url))
        self.log.append((now, now, "answered" if body is not None else "unrouted", path))
        if body is None:
            return httpx.Response(599)
        headers = {"content-type": "application/json; charset=utf-8"}
        return httpx.Response(200, headers=headers, content=body)

    def outcomes(self) -> list[str]:
        return [entry[2] for entry in self.log]

    def gaps(self) -> list[float]:
        """Seconds from each request's end to the next one's start."""
        return [b[0] - a[1] for a, b in zip(self.log, self.log[1:], strict=False)]


def _scaled(monkeypatch: pytest.MonkeyPatch, **values: float) -> fetch.Host:
    """INFORM's policy with other times (the mechanism under test, not the
    production numbers). A field this code does not have is left out, so on
    a tree without it the test runs and fails on what it observes."""
    known = {f.name for f in dataclasses.fields(fetch.Host)}
    policy = dataclasses.replace(
        fetch.HOSTS[HOST], **{k: v for k, v in values.items() if k in known}
    )
    monkeypatch.setitem(fetch.HOSTS, HOST, policy)
    return policy


async def _release_landed(within: float) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + within
    while loop.time() < end:
        entry = risk._inform_cache.peek(("group", "INFORM2026_Mid"))
        if entry is not None and entry.value is not None:
            return True
        await asyncio.sleep(0.05)
    return False


def _country(result: P.Report) -> dict:
    assert result.body is not None
    block: dict = result.body["blocks"]["risk.country"]
    return block


# ----------------------------------------------- the real policy, the real budget


async def test_a_report_after_the_startup_warm_up_pays_one_request_inside_the_budget() -> None:
    router = install()
    f5 = Frontend(router, TOO_CLOSE_S)
    tasks = P.start(enabled=True)
    try:
        # the release (WorkflowGroups + the published group) is read at startup
        assert await _release_landed(within=6.0), "no INFORM release read at startup"
        assert f5.outcomes() == ["answered", "answered"]
        before = len(f5.log)
        started = time.monotonic()
        result = await P.report(SAMUI, lambda: ([], []), budget_s=Settings().place_budget_seconds)
        took = time.monotonic() - started
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    block = _country(result)
    assert block["status"] == "ok", block.get("error")
    assert block["data"]["iso3"] == "THA" and block["data"]["inform"] == 4.9
    # one request for a new country, never dropped, inside the 8 s budget
    assert [e[2:] for e in f5.log[before:]] == [("answered", "Scores THA")]
    assert "dropped" not in f5.outcomes()
    assert took < Settings().place_budget_seconds
    assert min(f5.gaps()) >= fetch.HOSTS[HOST].spacing - 0.01


def test_the_inform_policy_fits_the_report_budget() -> None:
    policy = fetch.HOSTS[HOST]
    budget = Settings().place_budget_seconds
    assert budget == 8.0  # SOS_PLACE_BUDGET_SECONDS, not lengthened
    # one request at a time, spaced past the largest dropped gap (1.0 s)
    # and the smallest answered one (2.0 s) measured from the Mac
    assert policy.concurrency == 1 and policy.spacing > TOO_CLOSE_S
    assert policy.retry_pause >= policy.spacing
    # a dropped first attempt (1.24 s at worst from the Mac, a new
    # connection; 0.13 s from the VPS), the pause, then an answer as fast as
    # the measured ones (1.22 to 1.47 s): inside the report's budget. An
    # 8.3 s answer (Italy, measured) fits no budget: the fetch then fills the
    # cache after the report says `budget`, and the panel asks again.
    assert 1.24 + policy.retry_pause + 1.47 < budget
    # inside the host timeout, the first attempt still waits long enough for
    # the slowest answer measured (8.3 s), and a retry follows the pause
    first = (policy.timeout - policy.retry_pause) * 2 / 3
    assert first >= 8.3
    assert policy.timeout - first - policy.retry_pause >= fetch.MIN_ATTEMPT_S


# ------------------------------------------------------ the mechanism, scaled


async def test_two_cold_reports_at_once_wait_the_interval_and_none_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = install()
    policy = _scaled(monkeypatch, spacing=0.3, retry_pause=0.5)
    f5 = Frontend(router, too_close=0.2)
    a, b = await asyncio.gather(
        P.report(SAMUI, lambda: ([], []), budget_s=5.0),
        P.report(ROME, lambda: ([], []), budget_s=5.0),
    )
    assert _country(a)["status"] == "ok", _country(a).get("error")
    assert _country(b)["status"] == "ok", _country(b).get("error")
    assert (_country(a)["data"]["iso3"], _country(b)["data"]["iso3"]) == ("THA", "ITA")
    # groups and the group once (single flight), then one Scores per country
    assert sorted(e[3] for e in f5.log) == [
        "INFORM2026_Mid",
        "Scores ITA",
        "Scores THA",
        "WorkflowGroups",
    ]
    assert "dropped" not in f5.outcomes()
    assert min(f5.gaps()) >= policy.spacing - 0.01


async def test_a_dropped_request_is_retried_after_the_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = install()
    policy = _scaled(monkeypatch, spacing=0.3, retry_pause=0.5)
    f5 = Frontend(router, too_close=0.2)
    f5.drop_at = {2}  # the Scores request, whatever its gap
    block = await risk.build_country(ctx_for(9.512, 100.013, NOW))
    assert block.status == "ok", block.error
    assert f5.outcomes() == ["answered", "answered", "dropped", "answered"]
    assert f5.gaps()[2] >= policy.retry_pause - 0.01  # the retry waited the pause


async def test_a_pause_that_leaves_no_usable_attempt_is_not_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = install()
    _scaled(monkeypatch, timeout=1.2, spacing=0.3, retry_pause=0.5)
    f5 = Frontend(router, too_close=0.2)
    f5.drop_at = {0}
    started = time.monotonic()
    with pytest.raises(fetch.FetchError) as raised:
        await fetch.get(GROUPS_URL)
    # 1.2 s minus the 0.5 s pause leaves less than one usable second
    assert raised.value.code == "upstream_unreachable"
    assert f5.outcomes() == ["dropped"]
    assert time.monotonic() - started < 0.3  # failed at once, no pause slept


async def test_other_hosts_and_their_retries_are_not_slowed() -> None:
    router = install()
    f5 = Frontend(router, TOO_CLOSE_S)
    quake = "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
    router.add(quake, b"{}")
    om_url = "https://openmeteo.s3.amazonaws.com/data/x.om"
    om_seen: list[float] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        om_seen.append(asyncio.get_running_loop().time())
        if len(om_seen) == 1:
            raise httpx.RemoteProtocolError("dropped", request=request)
        return httpx.Response(200, content=b"x")

    router.handler(om_url, flaky)
    await fetch.get(GROUPS_URL)  # INFORM is now in its quiet time
    started = time.monotonic()
    answers = await asyncio.gather(*(fetch.get(quake) for _ in range(4)), fetch.get(om_url))
    assert time.monotonic() - started < 0.5
    assert [r.status_code for r in answers] == [200] * 5
    assert len(om_seen) == 2 and om_seen[1] - om_seen[0] < 0.2  # its retry is immediate
    assert f5.outcomes() == ["answered"]
    spaced = [
        h
        for h, p in fetch.HOSTS.items()
        if getattr(p, "spacing", 0.0) or getattr(p, "retry_pause", 0.0)
    ]
    assert spaced == [HOST]


async def test_a_release_refresh_keeps_the_copy_and_says_when_it_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = install()
    _scaled(monkeypatch, spacing=0.05, retry_pause=0.05)
    f5 = Frontend(router, too_close=0.0)
    assert await risk.refresh_inform_release() is True
    first = risk._inform_cache.peek("groups")
    assert first is not None and first.fetched_at is not None
    copy, when = first.value, first.fetched_at
    # the copy is fresh: a forced refresh reads again all the same
    assert await risk.refresh_inform_release() is True
    assert f5.outcomes() == ["answered"] * 4
    f5.drop_at = set(range(len(f5.log), len(f5.log) + 10))
    assert await risk.refresh_inform_release() is False
    kept = risk._inform_cache.peek("groups")
    assert kept is not None and kept.value == copy and kept.fetched_at != when
    assert kept.fresh()  # a failed refresh never throws the good copy away
