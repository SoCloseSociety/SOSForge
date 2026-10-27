"""Round 2, lane P -- one visitor's searches no longer queue everyone else.

Measured with Nominatim mocked (tools/bench_geocode.py, 2026-10-27): one
address firing 10 searches at once made a second visitor wait 9.96 s for
one search, 30 made it wait 29.98 s. The 1 req/s queue is Nominatim's
policy and stays; what changed is that one address may only put `burst`
searches in it at once.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app import geocode
from app import main as main_module
from app.geocode import AddressBudget


class TestTheBudget:
    def test_a_burst_then_one_per_second(self):
        budget = AddressBudget(burst=3, per_second=1.0)
        assert [budget.take("a", now=0.0) for _ in range(3)] == [0.0, 0.0, 0.0]
        assert budget.take("a", now=0.0) == pytest.approx(1.0)
        assert budget.take("a", now=1.0) == 0.0
        assert budget.take("a", now=1.0) > 0

    def test_addresses_do_not_share(self):
        budget = AddressBudget(burst=1, per_second=1.0)
        assert budget.take("a", now=0.0) == 0.0
        assert budget.take("a", now=0.0) > 0
        assert budget.take("b", now=0.0) == 0.0

    def test_no_address_is_not_one_shared_bucket(self):
        budget = AddressBudget(burst=1, per_second=1.0)
        assert all(budget.take(None, now=0.0) == 0.0 for _ in range(10))

    def test_memory_is_bounded(self):
        budget = AddressBudget(burst=1, per_second=0.001, max_addresses=100)
        for i in range(1000):
            budget.take(f"10.0.{i // 256}.{i % 256}", now=float(i))
        assert len(budget._tokens) <= 100


@pytest.fixture
def client(monkeypatch) -> TestClient:
    def nominatim(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        return httpx.Response(200, json=[{"display_name": q, "lat": "35.6", "lon": "139.7"}])

    monkeypatch.setattr(geocode, "_transport", httpx.MockTransport(nominatim))
    monkeypatch.setattr(geocode, "_MIN_INTERVAL", 0.0)  # the policy is not under test
    monkeypatch.setattr(geocode, "budget", AddressBudget(burst=3, per_second=1.0))
    geocode._cache.clear()
    return TestClient(main_module.app)


class TestTheRoute:
    def test_a_fourth_search_at_once_is_told_to_come_back(self, client):
        statuses = [client.get("/api/geocode", params={"q": f"place {i}"}) for i in range(4)]
        assert [r.status_code for r in statuses] == [200, 200, 200, 429]
        assert int(statuses[3].headers["Retry-After"]) >= 1

    def test_a_cached_answer_is_never_refused(self, client):
        assert client.get("/api/geocode", params={"q": "tokyo"}).status_code == 200
        for i in range(2):
            client.get("/api/geocode", params={"q": f"other {i}"})
        # the budget is spent, the answer for "tokyo" is not a Nominatim call
        repeat = [client.get("/api/geocode", params={"q": "Tokyo "}) for _ in range(5)]
        assert all(r.status_code == 200 for r in repeat)
        assert repeat[0].json()["results"][0]["lat"] == 35.6
