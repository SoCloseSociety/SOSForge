"""The geocoder must hand back the country, not only the point.

An alert published without coordinates carries its country and nothing else,
so the local agent needs to know which country the reader is in. Making them
type an ISO code they may not know is a worse answer than reading it off the
place they just searched for.
"""

from __future__ import annotations

import httpx
import pytest

from app import geocode

# Verbatim response of
# https://nominatim.openstreetmap.org/search?q=Paris&format=jsonv2&limit=1&addressdetails=1
PARIS = [
    {
        "place_id": 97620444,
        "licence": "Data (c) OpenStreetMap contributors, ODbL 1.0.",
        "osm_type": "relation",
        "osm_id": 71525,
        "lat": "48.8534951",
        "lon": "2.3483915",
        "category": "boundary",
        "type": "administrative",
        "place_rank": 11,
        "importance": 0.897098092136026,
        "addresstype": "city",
        "name": "Paris",
        "display_name": "Paris, Ile-de-France, Metropolitan France, France",
        "address": {
            "city": "Paris",
            "ISO3166-2-lvl6": "FR-75C",
            "state": "Ile-de-France",
            "ISO3166-2-lvl4": "FR-IDF",
            "region": "Metropolitan France",
            "country": "France",
            "country_code": "fr",
        },
        "boundingbox": ["48.8155755", "48.9021560", "2.2241220", "2.4697602"],
    }
]

# The open ocean has no country, and pretending otherwise is the failure mode
# lesson 15 is about.
OFFSHORE = [
    {
        "lat": "-8.4",
        "lon": "121.4",
        "display_name": "Flores Sea",
        "type": "sea",
        "boundingbox": ["-9.0", "-7.0", "120.0", "123.0"],
    }
]


@pytest.fixture(autouse=True)
def _clear_cache():
    geocode._cache.clear()
    yield
    geocode._cache.clear()


def transport_for(payload: list[dict], seen: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


class TestTheCountryComesBackWithThePoint:
    @pytest.mark.asyncio
    async def test_a_city_yields_an_iso2_code(self, monkeypatch):
        monkeypatch.setattr(geocode, "_transport", transport_for(PARIS))

        results = await geocode.search("Paris")

        assert results[0]["country_code"] == "FR", "uppercase ISO2, as every source uses"
        assert results[0]["lat"] == pytest.approx(48.8534951)

    @pytest.mark.asyncio
    async def test_a_place_with_no_country_says_none(self, monkeypatch):
        monkeypatch.setattr(geocode, "_transport", transport_for(OFFSHORE))

        results = await geocode.search("Flores Sea")

        assert results[0]["country_code"] is None

    @pytest.mark.asyncio
    async def test_it_actually_asks_for_the_address(self, monkeypatch):
        """Nominatim omits the address block unless asked, so the field would
        silently be None everywhere without this parameter."""
        seen: dict = {}
        monkeypatch.setattr(geocode, "_transport", transport_for(PARIS, seen))

        await geocode.search("Paris")

        assert seen["params"].get("addressdetails") == "1"

    @pytest.mark.asyncio
    async def test_a_failure_stays_a_failure_not_a_crash(self, monkeypatch):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no network")

        monkeypatch.setattr(geocode, "_transport", httpx.MockTransport(boom))

        # the search bar must keep working on the local text filter alone
        assert await geocode.search("Paris") == []
