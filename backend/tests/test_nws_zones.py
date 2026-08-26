"""89% of live NWS alerts have no geometry. All of them name a zone that does."""

from __future__ import annotations

import httpx
import pytest

from app.sources.nws_zones import ZoneResolver, _centroid_of

# verbatim shape of https://api.weather.gov/zones/county/MDC031
ZONE_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[-77.2, 39.0], [-77.0, 39.0], [-77.0, 39.2], [-77.2, 39.2], [-77.2, 39.0]]],
}


class TestCentroid:
    def test_a_county_polygon_yields_its_centre(self):
        centre = _centroid_of(ZONE_GEOMETRY)
        assert centre is not None
        lat, lon = centre
        assert 39.0 <= lat <= 39.2
        assert -77.2 <= lon <= -77.0

    def test_a_marine_zone_in_several_pieces_still_yields_one_point(self):
        multi = {
            "type": "MultiPolygon",
            "coordinates": [ZONE_GEOMETRY["coordinates"], ZONE_GEOMETRY["coordinates"]],
        }
        assert _centroid_of(multi) is not None

    def test_no_geometry_is_no_position_not_a_wrong_one(self):
        """Lesson 15: a wrong position is far worse than a missing one."""
        assert _centroid_of(None) is None
        assert _centroid_of({"type": "Point", "coordinates": [1, 2]}) is None
        assert _centroid_of({"type": "Polygon", "coordinates": []}) is None


class TestResolver:
    @pytest.mark.asyncio
    async def test_it_learns_a_zone_once_and_never_asks_again(self, tmp_path):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"geometry": ZONE_GEOMETRY})

        transport = httpx.MockTransport(handler)
        resolver = ZoneResolver(tmp_path / "zones.json")
        url = "https://api.weather.gov/zones/county/MDC031"

        async with httpx.AsyncClient(transport=transport) as client:
            assert await resolver.resolve(client, [url]) == 1
            assert await resolver.resolve(client, [url, url, url]) == 0

        assert calls["n"] == 1
        assert resolver.known(url) is not None

    @pytest.mark.asyncio
    async def test_the_cache_survives_a_restart(self, tmp_path):
        cache = tmp_path / "zones.json"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"geometry": ZONE_GEOMETRY})

        url = "https://api.weather.gov/zones/county/MDC031"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await ZoneResolver(cache).resolve(client, [url])

        # 511 distinct zones in one live snapshot: refetching them at every
        # restart would be five hundred requests for boundaries that never move
        assert ZoneResolver(cache).known(url) is not None

    @pytest.mark.asyncio
    async def test_a_zone_that_fails_is_not_retried_forever(self, tmp_path):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        url = "https://api.weather.gov/zones/county/NOPE"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = ZoneResolver(tmp_path / "zones.json")
            await resolver.resolve(client, [url])
            await resolver.resolve(client, [url])

        assert calls["n"] == 1
        assert resolver.has(url) is True
        assert resolver.known(url) is None

    @pytest.mark.asyncio
    async def test_it_does_not_burst_five_hundred_requests_at_once(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"geometry": ZONE_GEOMETRY})

        urls = [f"https://api.weather.gov/zones/county/Z{i}" for i in range(500)]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            learned = await ZoneResolver(tmp_path / "z.json").resolve(client, urls)

        assert learned == 25, "the whole backlog was fetched in one cycle"
