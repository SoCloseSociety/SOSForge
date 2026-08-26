"""Fifth audit -- what the sources actually sent today, versus what we read.

Every payload below is a verbatim excerpt of a live response fetched on
2026-08-26. That is the only kind of fixture this product accepts: a parser is
pinned to a real schema, and an invented payload proves only that the code
runs.
"""

from __future__ import annotations

from app.countries import resolve
from app.sources.eew import CencSource
from app.sources.regional import GeofonSource, GeonetSource


class TestCencDoesNotPutAChineseFlagOnTheWorld:
    """CENC relays FOREIGN earthquakes -- 12 of the 50 entries live today were
    outside China. The parser stamped every one of them `country="China"`.

    This is the JMA trap, already documented in CLAUDE.md and already paid for
    once, reintroduced through a different source: a M7.7 in Indonesia wearing
    a Chinese flag. A wrong country is not cosmetic here -- it is the field
    people scan to know whether it concerns them.
    """

    # verbatim rows from https://api.wolfx.jp/cenc_eqlist.json
    PAYLOAD = {
        "No1": {
            "type": "reviewed",
            "EventID": "CD20260826070010",
            "time": "2026-08-26 15:00:10",
            "latitude": 39.32,
            "longitude": 74.51,
            "depth": 10.0,
            "magnitude": 3.4,
            "placeName": "新疆克孜勒苏州阿克陶县",
        },
        "No33": {
            "type": "reviewed",
            "EventID": "CD20260820120000",
            "time": "2026-08-20 20:00:00",
            "latitude": -8.4,
            "longitude": 121.4,
            "depth": 30.0,
            "magnitude": 7.7,
            "placeName": "印尼弗洛勒斯岛附近海域",
        },
        "No10": {
            "type": "reviewed",
            "EventID": "CD20260824030000",
            "time": "2026-08-24 11:00:00",
            "latitude": -14.7,
            "longitude": -73.3,
            "depth": 40.0,
            "magnitude": 6.7,
            "placeName": "秘鲁",
        },
    }

    def test_a_chinese_quake_keeps_its_country(self):
        events = {e.source_id: e for e in CencSource().parse_payload(self.PAYLOAD)}
        home = events["CD20260826070010"]
        assert home.country == "China"
        assert home.country_code == "CN"

    def test_a_relayed_foreign_quake_does_not(self):
        events = {e.source_id: e for e in CencSource().parse_payload(self.PAYLOAD)}
        flores = events["CD20260820120000"]
        assert flores.country != "China", "a M7.7 off Indonesia was wearing a Chinese flag"
        assert flores.country_code != "CN"

    def test_the_south_american_one_either(self):
        events = {e.source_id: e for e in CencSource().parse_payload(self.PAYLOAD)}
        assert events["CD20260824030000"].country_code != "CN"

    def test_the_timezone_handling_is_untouched(self):
        """Beijing time without an offset. Proven against GDACS at the time of
        the audit: 15:00:10 CST == 07:00:10 UTC."""
        events = {e.source_id: e for e in CencSource().parse_payload(self.PAYLOAD)}
        assert events["CD20260826070010"].time.hour == 7


class TestGeonetDoesNotShowQuakesItWithdrew:
    """21 of the 100 features live today carried `quality: "deleted"` -- GeoNet
    had withdrawn them -- and the parser emitted all of them.

    A phantom M5.2 at MMI 5 next to Wellington, re-emitted at every 60 s poll,
    with no way for it to ever leave. The retraction channel built for
    cancelled early warnings is exactly what this needs.
    """

    def feature(self, quality: str, public_id: str = "2026p576644") -> dict:
        # verbatim shape from https://api.geonet.org.nz/quake?MMI=3
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [174.7, -41.0]},
            "properties": {
                "publicID": public_id,
                "time": "2026-08-26T04:12:33.123Z",
                "depth": 22.5,
                "magnitude": 5.18,
                "mmi": 5,
                "locality": "15 km north-west of Porirua",
                "quality": quality,
            },
        }

    def test_a_withdrawn_quake_is_not_emitted(self):
        source = GeonetSource()
        events = source.parse_payload({"features": [self.feature("deleted")]})
        assert events == [], "GeoNet withdrew it and we published it anyway"

    def test_it_is_retracted_so_it_leaves_the_screens_it_reached(self):
        source = GeonetSource()
        source.parse_payload({"features": [self.feature("deleted")]})
        assert source.retractions == ["geonet:2026p576644"]

    def test_a_normal_quake_is_untouched(self):
        source = GeonetSource()
        events = source.parse_payload({"features": [self.feature("best")]})
        assert len(events) == 1
        assert events[0].magnitude == 5.2  # the parser rounds to one decimal, deliberately
        assert source.retractions == []


class TestGeofonAlertIsALabelNotADisclaimer:
    """The live `status` field reads:

        "A:automatic. Disclaimer: Unless revised by a geophysicist,
         automatically determined earthquake locations (status A) may be
         erroneous!"

    `split(":")[-1]` takes everything after the LAST colon, so the alert badge
    was rendering that entire sentence, on most of the hundred solutions the
    feed carries.
    """

    # verbatim from https://geofon.gfz.de/eqinfo/list.php?fmt=geojson
    FEATURE = {
        "id": "gfz2026qrez",
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [71.58, 36.55, 102]},
        "properties": {
            "evtype": "all",
            "mag": 4.6,
            "magType": "mb",
            "place": "Afghanistan-Tajikistan Border Region",
            "status": (
                "A:automatic. Disclaimer: Unless revised by a geophysicist, "
                "automatically determined earthquake locations (status A) may be erroneous!"
            ),
            "time": "2026-08-26T02:24:10.570",
            "url": "https://geofon.gfz.de/eqinfo/event.php?id=gfz2026qrez",
        },
    }

    def test_the_badge_says_automatic_not_a_paragraph(self):
        events = GeofonSource().parse_payload({"features": [self.FEATURE]})
        assert len(events) == 1
        assert events[0].alert == "automatic"

    def test_an_automatic_solution_is_marked_preliminary(self):
        events = GeofonSource().parse_payload({"features": [self.FEATURE]})
        assert events[0].preliminary is True


class TestTheFlagIsRightOrAbsent:
    """Rule 12 of this codebase: when in doubt, no flag. A wrong flag is worse
    than none, because it is read as a fact."""

    def test_the_us_state_of_georgia_does_not_get_the_caucasus_flag(self):
        # both shapes exist in the live USGS feed, so a bare suffix cannot decide
        assert resolve(None, "1 km NW of Trion, Georgia") is None
        assert resolve(None, "10 km WNW of Resaca, Georgia") is None

    def test_an_unambiguous_georgian_place_still_resolves(self):
        assert resolve("Georgia", "21 km NNW of T'q'ibuli, Georgia") == "GE"

    def test_papua_new_guinea_gets_its_own_flag(self):
        """`new guinea` is declared ambiguous and is a substring of every
        `papua new guinea` label, so the longer, unambiguous name was
        unreachable: all six live PNG events resolved to nothing."""
        assert resolve(None, "New Ireland region, Papua New Guinea") == "PG"
        assert resolve(None, "82 km SW of Rabaul, Papua New Guinea") == "PG"

    def test_a_bare_new_guinea_stays_ambiguous(self):
        assert resolve(None, "somewhere in New Guinea") is None

    def test_usgs_spells_timor_leste_without_a_hyphen(self):
        assert resolve(None, "34 km NNE of Lospalos, Timor Leste") == "TL"
