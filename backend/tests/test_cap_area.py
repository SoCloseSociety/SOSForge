# ruff: noqa: E501 -- the fixtures below are verbatim XML documents. Wrapping
# a line changes the bytes, and a fixture that is not what the agency served
# proves nothing (lesson 8).
"""Every WMO alert on the live feed reached the map with no position: 293/293.

The aggregate carries no geometry at all; the CAP document it links sometimes
does. Each fixture below is a verbatim excerpt of a document served by
`https://severeweather.wmo.int/v2/cap-alerts/...` on 2026-08-26 -- whole
elements dropped where they are prose for humans, no value ever retyped.
"""

from __future__ import annotations

import httpx
import pytest

from app.sources.cap_area import CapAreaCache, parse_cap_position, parse_polygon

# us-noaa-nws-en-marine/2026/08/26/08/09/00-6d8c123a30321345133350811cb34cf2.xml,
# down to the elements the parser reads.
NWS_MARINE_CAP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
    <identifier>urn:oid:2.49.0.1.840.0.95be17eef62e26a6192083f7b400094e909aa6cc.001.1</identifier>
    <info>
        <area>
            <areaDesc>Straits of Florida from west end of Seven Mile Bridge to south of Halfmoon Shoal 20 to 60 NM out</areaDesc>
            <polygon>24.1,-81.52 23.87,-81.58 23.94,-81.84 24.17,-81.79 24.1,-81.52</polygon>
            <geocode>
                <valueName>SAME</valueName>
                <value>077074</value>
            </geocode>
            <geocode>
                <valueName>UGC</valueName>
                <value>GMZ074</value>
            </geocode>
        </area>
    </info>
</alert>
"""

# Verbatim excerpt of de-dwd-en/2026/08/26/07/17/00-1be3e43d9af4fb800b55...xml.
# The trap: DWD publishes the area to SUBTRACT as a `geocode` whose value is a
# coordinate list. Anything that scans the document for numbers instead of
# reading `area/polygon` averages the hole into the position.
DWD_CAP_WITH_EXCLUDE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>2.49.0.0.276.0.DWD.PVW.1787728620000.cf893869-1513-4e61-a89c-76c55fe404e6.ENG</identifier>
  <info>
    <eventCode>
      <valueName>AREA_COLOR</valueName>
      <value>255 235 59</value>
    </eventCode>
    <area>
      <areaDesc>polygonal event area</areaDesc>
      <polygon>54.316143,10.79881 54.339145,10.841779 54.357342,10.868026 54.366461,10.904083 54.37785,10.921985 54.384668,10.94934 54.381008,11.020579 54.376176,11.018894 54.373937,10.990128 54.368166,11.021713 54.376113,11.071758 54.39201,11.091555 54.396196,11.109576 54.389233,11.110459 54.393769,11.11958 54.387469,11.131478 54.374269,11.126163 54.352967,11.081985 54.358241,11.069052 54.355493,11.060235 54.34406,11.063075 54.346232,11.073509 54.30194,11.076901 54.276438,11.084799 54.246453,11.082756 54.198961,11.094285 54.19578,11.078448 54.213984,11.076596 54.206047,11.05889 54.213002,11.049833 54.207012,11.03551 54.214305,11.030728 54.217635,11.01822 54.206554,10.992934 54.207158,10.966797 54.209542,10.951528 54.218903,10.957709 54.221689,10.946644 54.231818,10.944741 54.239666,10.924126 54.240573,10.911456 54.231046,10.913251 54.229686,10.894984 54.261436,10.882414 54.263176,10.86955 54.257275,10.858402 54.262874,10.832983 54.234848,10.793985 54.224945,10.781535 54.230557,10.753165 54.229555,10.729858 54.218932,10.710872 54.218125,10.699346 54.231766,10.69878 54.243112,10.717614 54.261173,10.715681 54.269725,10.728763 54.282091,10.707008 54.30551,10.716875 54.310214,10.736871 54.307057,10.762434 54.316143,10.79881 54.316143,10.79881</polygon>
      <altitude>0.0</altitude>
      <ceiling>9842.5197</ceiling>
      <geocode>
        <valueName>EXCLUDE_POLYGON</valueName>
        <value>54.371039,10.071132 54.369236,10.116049 54.386933,10.136347 54.41201,10.131966 54.4245,10.103053 54.417659,10.086475 54.403827,10.073758 54.398889,10.086209 54.371039,10.071132 54.371039,10.071132</value>
      </geocode>
    </area>
  </info>
</alert>
"""

# cn-cma-xx/2026/08/26/08/09/13-53fc51b1ff8ae9a952e60bcf476e803.xml, second
# `info` block dropped (the Chinese one, same area repeated). 144 of the 443
# Severe-or-Extreme alerts look exactly like this: an administrative code, a
# name, and no geometry anywhere.
CMA_CAP_NO_GEOMETRY = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
	<identifier>13013241600000_20260826160913</identifier>
	<sender>Yuanshi_County_Meteorological_Observatory</sender>
	<sent>2026-08-26T16:09:13+08:00</sent>
	<info>
		<language>en-US</language>
		<event>Flash flood events</event>
		<severity>Minor</severity>
		<headline>Yuanshi County Meteorological Observatory updates blue warning for flash flood disasters [Level IV/General]</headline>
		<area>
			<areaDesc>Yuanshi County</areaDesc>
			<geocode>
				<valueName>CPEAS Geographic Code</valueName>
				<value>130132000000</value>
			</geocode>
		</area>
	</info>
</alert>
"""


class TestPolygon:
    def test_a_cap_polygon_is_lat_lon_not_lon_lat(self):
        """CAP orders each pair latitude first, the opposite of GeoJSON. The
        marine zone above sits off the Florida Keys: around 24 N, 81.7 W."""
        point = parse_polygon("24.1,-81.52 23.87,-81.58 23.94,-81.84 24.17,-81.79 24.1,-81.52")
        assert point is not None
        lat, lon = point
        assert 23.8 < lat < 24.2
        assert -81.9 < lon < -81.5

    def test_a_pair_out_of_range_is_refused_not_clamped(self):
        """Lesson 15, and the exact shape that caused it: the JMA
        degrees-minutes variant went through a parser that bounded nothing."""
        assert parse_polygon("3237.5,13040.7") is None
        assert parse_polygon("48.85,2.35 91.0,2.35") is None
        assert parse_polygon("48.85,2.35 48.9,-181.0") is None

    def test_one_bad_pair_rejects_the_ring_it_does_not_skip_it(self):
        """A ring read half-right is a point somewhere else entirely, and
        nothing downstream can tell the difference."""
        assert parse_polygon("24.1,-81.52 nonsense 23.94,-81.84") is None

    def test_nonsense_yields_nothing_rather_than_a_point(self):
        assert parse_polygon("") is None
        assert parse_polygon("24.1") is None
        assert parse_polygon("north,west") is None


class TestCapDocument:
    def test_it_places_the_alert_that_drew_itself(self):
        point = parse_cap_position(NWS_MARINE_CAP)
        assert point is not None
        lat, lon = point
        assert 23.8 < lat < 24.2
        assert -81.9 < lon < -81.5

    def test_the_excluded_area_is_not_part_of_the_position(self):
        """DWD publishes the hole to subtract as a `geocode` whose value is
        a coordinate list, so anything that scans the document for numbers
        instead of reading `area/polygon` averages the hole into the point."""
        point = parse_cap_position(DWD_CAP_WITH_EXCLUDE)
        assert point is not None
        lat, lon = point
        # event area alone: 54.2870, 10.9338. Averaging the hole in as well
        # gives 10.8188, so the longitude is what separates the two.
        assert round(lat, 4) == 54.2870
        assert round(lon, 4) == 10.9338

    def test_an_alert_that_only_names_an_administrative_code_stays_unplaced(self):
        """The Chinese CMA publishes a GB/T 2260 code and nothing else. We do
        not own that gazetteer, so the honest answer is no position."""
        assert parse_cap_position(CMA_CAP_NO_GEOMETRY) is None

    def test_a_broken_document_does_not_raise(self):
        assert parse_cap_position("") is None
        assert parse_cap_position("<alert>") is None


class TestCache:
    @pytest.mark.asyncio
    async def test_a_cap_document_is_fetched_once_and_never_again(self, tmp_path):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, text=NWS_MARINE_CAP)

        cache = CapAreaCache(tmp_path / "wmo-cap.json")
        path = "us-noaa-nws-en-marine/2026/08/26/08/09/00-6d8c123a.xml"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await cache.resolve(client, [path]) == 1
            assert await cache.resolve(client, [path, path]) == 0

        assert calls["n"] == 1
        assert cache.known(path) is not None

    @pytest.mark.asyncio
    async def test_a_document_without_geometry_is_not_asked_for_again(self, tmp_path):
        """Otherwise the 158 Chinese warnings are re-fetched every five
        minutes, forever, for an answer that will never change."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, text=CMA_CAP_NO_GEOMETRY)

        cache = CapAreaCache(tmp_path / "wmo-cap.json")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await cache.resolve(client, ["cn-cma-xx/a.xml"])
            await cache.resolve(client, ["cn-cma-xx/a.xml"])
        assert calls["n"] == 1
        assert cache.known("cn-cma-xx/a.xml") is None
        assert cache.has("cn-cma-xx/a.xml")

    @pytest.mark.asyncio
    async def test_it_never_bursts_at_the_agency(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=NWS_MARINE_CAP)

        cache = CapAreaCache(tmp_path / "wmo-cap.json")
        wanted = [f"x/{i}.xml" for i in range(500)]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            learned = await cache.resolve(client, wanted)
        assert learned == cache.per_cycle < 500

    @pytest.mark.asyncio
    async def test_the_cache_survives_a_restart(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=NWS_MARINE_CAP)

        path = tmp_path / "wmo-cap.json"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await CapAreaCache(path).resolve(client, ["a/b.xml"])
        assert CapAreaCache(path).known("a/b.xml") is not None

    @pytest.mark.asyncio
    async def test_the_cache_is_bounded_and_drops_the_oldest(self, tmp_path):
        """A CAP document is not an NWS zone: there is a new one per alert
        issued, about 2000 a day. Unbounded, the process ends up holding every
        alert the planet ever published."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=NWS_MARINE_CAP)

        cache = CapAreaCache(tmp_path / "wmo-cap.json", per_cycle=10, max_entries=15)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for start in (0, 10, 20):
                await cache.resolve(client, [f"x/{i}.xml" for i in range(start, start + 10)])

        assert len(cache._positions) == 15
        assert not cache.has("x/0.xml")  # oldest, dropped
        assert cache.has("x/29.xml")  # newest, kept

    @pytest.mark.asyncio
    async def test_a_dead_agency_costs_a_position_not_the_cycle(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        cache = CapAreaCache(tmp_path / "wmo-cap.json")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await cache.resolve(client, ["a/b.xml"]) == 1
        assert cache.known("a/b.xml") is None
