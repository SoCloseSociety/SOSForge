# ruff: noqa: E501 -- the fixtures below are verbatim excerpts of real
# responses. Re-wrapping a line changes the bytes, and on the bulletins it
# changes the COLUMNS, which is the only thing their parser has to go on
# (lesson 8).
"""What the alert tells a person to do, what people felt, and what the sea did.

Every fixture here is a verbatim excerpt of a real response, captured on the
date named next to it. Values are dropped, never retyped.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.models.event import DESCRIPTION_MAX, Event, normalize_text
from app.sources.cap_text import parse_cap_text, pick_info
from app.sources.felt import FeltReportCache, parse_counts, parse_unid
from app.sources.nws import parse_feature as parse_nws
from app.sources.tsunami_bulletin import BulletinCache, parse_bulletin
from app.sources.usgs import parse_feature as parse_usgs

# --------------------------------------------------------------------- NWS

# api.weather.gov/alerts, 2026-08-21T18:56-05:00, NWS Quad Cities. The
# polygon is cut down to one ring; nothing else is touched.
TORNADO_WARNING = json.loads(
    r"""
{
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[-89.35, 41.29], [-89.19, 41.4], [-89.05, 41.24], [-89.21, 41.13], [-89.35, 41.29]]]},
    "properties": {
        "id": "urn:oid:2.49.0.1.840.0.838296fb6a68bc609c89af64757bef488ab9dff4.001.1",
        "areaDesc": "Bureau, IL; Putnam, IL",
        "sent": "2026-08-21T18:56:00-05:00",
        "effective": "2026-08-21T18:56:00-05:00",
        "expires": "2026-08-21T19:45:00-05:00",
        "ends": "2026-08-21T19:45:00-05:00",
        "status": "Actual",
        "messageType": "Alert",
        "severity": "Extreme",
        "certainty": "Observed",
        "urgency": "Immediate",
        "event": "Tornado Warning",
        "senderName": "NWS Quad Cities IA IL",
        "headline": "Tornado Warning issued August 21 at 6:56PM CDT until August 21 at 7:45PM CDT by NWS Quad Cities IA IL",
        "description": "TORDVN\n\nThe National Weather Service in the Quad Cities has issued a\n\n* Tornado Warning for...\nNortheastern Putnam County in north central Illinois...\nEast central Bureau County in north central Illinois...\n\n* Until 745 PM CDT.\n\n* At 655 PM CDT, a severe thunderstorm capable of producing a tornado\nwas located over Hollowayville, or near Hennepin, moving southeast\nat 20 mph.\n\nHAZARD...Tornado.\n\nSOURCE...Radar indicated rotation.\n\nIMPACT...Flying debris will be dangerous to those caught without\nshelter. Mobile homes will be damaged or destroyed.\nDamage to roofs, windows, and vehicles will occur.  Tree\ndamage is likely.\n\n* This dangerous storm will be near...\nHennepin, Spring Valley, DePue, Granville, Ladd, Dalzell, Bureau\nJunction, Mark, Seatonville, and Standard around 700 PM CDT.\n\nThis also includes Donnelley DePue State Fish and Wildlife Area.\n\nThis includes the following highways...\nInterstate 80 in Illinois between mile markers 62 and 73.\nInterstate 180 between mile markers 7 and 11.",
        "instruction": "TAKE COVER NOW! Move to a basement or an interior room on the lowest\nfloor of a sturdy building. Avoid windows. If you are outdoors, in a\nmobile home, or in a vehicle, move to the closest substantial shelter\nand protect yourself from flying debris.",
        "response": "Shelter"
    }
}
"""
)

# api.weather.gov/alerts, 2026-08-21T17:48-05:00, NWS North Platte. A WATCH:
# `instruction` is null, which is not a gap in the feed -- a watch says
# conditions are favourable, it has nothing to instruct yet.
THUNDERSTORM_WATCH = json.loads(
    r"""
{
    "type": "Feature",
    "geometry": null,
    "properties": {
        "id": "urn:oid:2.49.0.1.840.0.b4efba7bc51f65c71f8c1233d53628dc7dcfaa68.001.1",
        "areaDesc": "Cherry, NE; Deuel, NE; Garden, NE; Grant, NE; Sheridan, NE",
        "sent": "2026-08-21T17:48:00-05:00",
        "effective": "2026-08-21T17:48:00-05:00",
        "expires": "2026-08-22T00:00:00-05:00",
        "ends": "2026-08-22T00:00:00-05:00",
        "severity": "Severe",
        "certainty": "Possible",
        "urgency": "Future",
        "event": "Severe Thunderstorm Watch",
        "senderName": "NWS North Platte NE",
        "headline": "Severe Thunderstorm Watch issued August 21 at 5:48PM CDT until August 22 at 12:00AM CDT by NWS North Platte NE",
        "description": "THE NATIONAL WEATHER SERVICE HAS ISSUED SEVERE THUNDERSTORM WATCH\n612 IN EFFECT UNTIL MIDNIGHT CDT /11 PM MDT/ TONIGHT FOR THE\nFOLLOWING AREAS\n\nIN NEBRASKA THIS WATCH INCLUDES 5 COUNTIES\n\nIN NORTH CENTRAL NEBRASKA\n\nCHERRY\n\nIN PANHANDLE NEBRASKA\n\nDEUEL                 GARDEN                SHERIDAN\n\nIN WEST CENTRAL NEBRASKA\n\nGRANT\n\nTHIS INCLUDES THE CITIES OF BIG SPRINGS, CHAPPELL, GORDON,\nHYANNIS, LEWELLEN, OSHKOSH, RUSHVILLE, AND VALENTINE.",
        "instruction": null,
        "response": "Monitor",
        "affectedZones": ["https://api.weather.gov/zones/county/NEC031"]
    }
}
"""
)


def test_nws_keeps_the_instruction() -> None:
    """The field the parser used to download and throw away."""
    event = parse_nws(TORNADO_WARNING)
    assert event is not None
    assert event.instruction is not None
    assert event.instruction.startswith("TAKE COVER NOW!")
    # The whole instruction survives. It is 244 characters and the cap is
    # 2000: the point of the cap is a hostile feed, not this.
    assert "protect yourself from flying debris." in event.instruction
    assert event.response_type == "Shelter"


def test_nws_instruction_is_reflowed_not_re_wrapped() -> None:
    """CAP arrives hard-wrapped at about 60 columns for a teletype. A browser
    wraps for itself, so the teletype's line breaks are noise -- but the blank
    lines between paragraphs are meaning."""
    event = parse_nws(TORNADO_WARNING)
    assert event is not None
    assert "lowest floor of a sturdy building" in event.instruction
    assert "lowest\nfloor" not in event.instruction


def test_nws_description_keeps_its_bullets() -> None:
    event = parse_nws(TORNADO_WARNING)
    assert event is not None
    assert event.description is not None
    # "* Tornado Warning for..." opened a new line without a blank line
    # before it; a bullet starts a paragraph.
    assert "\n\n* Tornado Warning for..." in event.description


def test_a_real_description_fits_under_the_cap() -> None:
    """Measured on 1631 real NWS alerts: the median description is 698
    characters and the 90th percentile 1019, against a cap of 1200. This one
    is 1001 raw and 1000 once reflowed -- it arrives whole. The cap exists
    for the tail (the longest seen was 2711) and for a feed that misbehaves,
    not for the ordinary alert."""
    event = parse_nws(TORNADO_WARNING)
    assert event is not None
    assert event.description is not None
    assert len(event.description) <= DESCRIPTION_MAX
    assert not event.description.endswith(" ...")
    assert event.description.endswith("Interstate 180 between mile markers 7 and 11.")


def test_nws_watch_has_no_instruction_and_that_is_not_a_zero() -> None:
    """15% of Severe/Extreme NWS alerts publish no instruction. Absent must
    stay absent: an empty string would render as an empty box titled
    "what to do"."""
    event = parse_nws(THUNDERSTORM_WATCH)
    assert event is not None
    assert event.instruction is None
    assert event.response_type == "Monitor"
    assert event.description is not None


def test_the_instruction_reaches_the_browser() -> None:
    """`public()` strips `raw`. A value the client needs has to be a real
    field, and this is the assertion that says so."""
    event = parse_nws(TORNADO_WARNING)
    assert event is not None
    payload = event.public()
    assert payload["instruction"].startswith("TAKE COVER NOW!")
    assert payload["response_type"] == "Shelter"
    assert "raw" not in payload


# ------------------------------------------------------------ normalization


def test_text_is_data_not_markup() -> None:
    """The instruction comes from feeds we do not control and ends up in a
    browser. It is kept as TEXT: nothing is stripped, because a real alert
    says `temperatures < 32F`, and nothing is unescaped, because we never
    emit it as markup. The renderer's contract is a text node."""
    hostile = "Move now <script>alert(1)</script> & stay tuned"
    assert normalize_text(hostile, 500) == "Move now <script>alert(1)</script> & stay tuned"


def test_control_characters_are_removed() -> None:
    """A NUL or an ESC is not text; `\\n` and `\\t` are. Dropping the ESC is
    what disarms an ANSI sequence -- the `[31m` that follows is ordinary
    printable text and stays, visibly inert, which is the honest outcome:
    the bytes that could make a terminal obey are gone, and nothing that a
    feed legitimately wrote has been silently rewritten."""
    assert normalize_text("Take\x00 cover\x1b[31m now", 500) == "Take cover[31m now"
    assert normalize_text("a\rb", 500) == "a b"


def test_empty_stays_empty() -> None:
    assert normalize_text("", 100) is None
    assert normalize_text("   \n\n  ", 100) is None
    assert normalize_text(None, 100) is None


def test_truncation_prefers_a_sentence_boundary() -> None:
    text = "First sentence here. " * 20
    cut = normalize_text(text, 100)
    assert cut is not None
    assert cut.endswith(" ...")
    assert "sentenc ..." not in cut


# -------------------------------------------------------------------- USGS

# earthquake.usgs.gov all_week.geojson, fetched 2026-08-26. `cdi` is what
# people REPORTED (3.8), `mmi` what the model ESTIMATED (3.417). They
# disagree, which is the entire reason both are kept.
HOPE_ALASKA = json.loads(
    r"""
{
    "type": "Feature",
    "id": "aka2026qoynpe",
    "geometry": {"type": "Point", "coordinates": [-149.666, 60.911, 31.3]},
    "properties": {
        "mag": 3.9,
        "place": "1 km WSW of Hope, Alaska",
        "time": 1787384560939,
        "updated": 1787619229530,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/aka2026qoynpe",
        "felt": 947,
        "cdi": 3.8,
        "mmi": 3.417,
        "alert": null,
        "status": "reviewed",
        "tsunami": 0,
        "sig": 594,
        "magType": "ml",
        "type": "earthquake",
        "title": "M 3.9 - 1 km WSW of Hope, Alaska"
    }
}
"""
)

# The same feed, the ordinary case: 96% of USGS events carry none of the
# three. Verbatim from all_hour.geojson on 2026-08-26.
UNFELT_QUAKE = json.loads(
    r"""
{
    "type": "Feature",
    "id": "nn00905573",
    "geometry": {"type": "Point", "coordinates": [-117.9231, 38.1928, 8.4]},
    "properties": {
        "mag": 0.9,
        "place": "31 km SSE of Mina, Nevada",
        "time": 1787739301321,
        "updated": 1787739430996,
        "felt": null,
        "cdi": null,
        "mmi": null,
        "alert": null,
        "status": "automatic",
        "tsunami": 0,
        "magType": "ml",
        "type": "earthquake",
        "title": "M 0.9 - 31 km SSE of Mina, Nevada"
    }
}
"""
)


def test_usgs_keeps_reported_and_modelled_intensity_apart() -> None:
    event = parse_usgs(HOPE_ALASKA)
    assert event is not None
    assert event.intensity_cdi == pytest.approx(3.8)
    assert event.intensity_mmi == pytest.approx(3.417)
    assert event.felt_reports == 947


def test_usgs_absent_intensity_is_never_a_zero() -> None:
    """ "Nobody felt it" is a claim. "No reports reached USGS" is a fact. Only
    the second one is true, so the field stays None."""
    event = parse_usgs(UNFELT_QUAKE)
    assert event is not None
    assert event.intensity_cdi is None
    assert event.intensity_mmi is None
    assert event.felt_reports is None


# ----------------------------------------------------------------- CAP text

# severeweather.wmo.int/v2/cap-alerts/es-aemet-es/2026/08/26/09/34/01-...xml,
# cut to the two `<info>` blocks' text fields. AEMET puts Spanish FIRST.
AEMET_CAP = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns = "urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>2.49.0.0.724.0.ES.20260826093401.645501RIRI28061787736841</identifier>
  <sent>2026-08-26T09:34:01-00:00</sent>
  <info>
    <language>es-ES</language>
    <event>Aviso de rissagas de nivel naranja</event>
    <responseType>Monitor</responseType>
    <severity>Severe</severity>
    <headline>Aviso de rissagas de nivel naranja. Costa - Menorca</headline>
    <description>Oscilaci&#243;n del nivel del mar: 1.5 m.</description>
    <instruction>Est&#233; preparado. Tome precauciones y mant&#233;ngase informado de la predicci&#243;n meteorol&#243;gica m&#225;s actualizada.</instruction>
  </info>
  <info>
    <language>en-GB</language>
    <event>Severe rissagas warning</event>
    <responseType>Monitor</responseType>
    <severity>Severe</severity>
    <headline>Severe rissagas warning. Costa - Menorca</headline>
    <description>Sea level oscillation : 1.5 m.</description>
    <instruction>Be prepared. Take precautions and keep up to date with the latest weather forecast. Severe damages to people and properties may occur, especially to those vulnerable or in exposed areas.</instruction>
  </info>
</alert>
"""

# .../me-meteo-xx/2026/08/26/04/55/17-...xml. Montenegrin first, English
# second, and BOTH publish `<instruction/>`: the element is there and empty.
MONTENEGRO_CAP = """<?xml version="1.0" encoding="utf-8" standalone="yes" ?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>2.49.0.1.499.0.1787720117045</identifier>
  <sent>2026-08-26T06:55:17+02:00</sent>
  <info>
    <language>cnr</language>
    <event>High Temperature</event>
    <responseType>Monitor</responseType>
    <headline>Upozorenje na visoku temperaturu</headline>
    <description>Tmax &gt; 25&#176;C</description>
    <instruction/>
  </info>
  <info>
    <language>en-GB</language>
    <event>High Temperature</event>
    <responseType>Monitor</responseType>
    <headline>High Temperature Warning</headline>
    <description>Maximum temperature-Tmax &gt; 25C</description>
    <instruction/>
  </info>
</alert>
"""


def test_cap_prefers_the_english_info_block() -> None:
    """The local-language block comes first in this real document. Taking the
    first one would put a Spanish instruction under an English headline."""
    text = parse_cap_text(AEMET_CAP)
    assert text.instruction is not None
    assert text.instruction.startswith("Be prepared.")
    assert text.description == "Sea level oscillation : 1.5 m."
    assert text.response_type == "Monitor"


def test_cap_empty_element_is_absent() -> None:
    """`<instruction/>` is an element that says nothing. It must not become an
    empty string that the UI then renders as a heading with no content."""
    text = parse_cap_text(MONTENEGRO_CAP)
    assert text.instruction is None
    assert text.headline == "High Temperature Warning"


def test_cap_entities_arrive_as_text_not_markup() -> None:
    """XML parsing resolves `&gt;` to `>`. That is a character in a string,
    and it stays one: nothing downstream re-reads it as markup."""
    text = parse_cap_text(MONTENEGRO_CAP)
    assert text.description == "Maximum temperature-Tmax > 25C"


def test_cap_survives_a_broken_document() -> None:
    """This runs over documents from a hundred agencies. One bad file costs
    that file."""
    assert not parse_cap_text("<alert>not closed")
    assert not parse_cap_text(b"")
    assert pick_info(__import__("xml.etree.ElementTree", fromlist=["x"]).fromstring("<a/>")) is None


def test_cap_text_fits_the_model() -> None:
    """The parser's output is only worth anything if the model accepts it."""
    text = parse_cap_text(AEMET_CAP)
    event = Event(
        id="wmo:x",
        source="wmo",
        source_id="x",
        time=datetime(2026, 8, 26, 9, 34, tzinfo=UTC),
        instruction=text.instruction,
        description=text.description,
        response_type=text.response_type,
    )
    assert event.public()["instruction"].startswith("Be prepared.")


# ------------------------------------------------------------ felt reports

# seismicportal.eu/testimonies-ws/api/search?starttime=2026-08-25T00:00:00,
# fetched 2026-08-26, first two features.
EMSC_TESTIMONIES = json.loads(
    r"""
{
    "type": "FeatureCollection",
    "metadata": {"count": 2},
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [39.5157, 39.6803]},
            "properties": {
                "eventid": "quakeml:eu.emsc/event/20260825_0000340",
                "source_id": 2051146,
                "time": "2026-08-25T23:17:40.570 UTC",
                "magtype": "ml",
                "mag": 2.5,
                "lon": 39.516,
                "lat": 39.68,
                "depth": 12.4,
                "region": "EASTERN TURKEY",
                "feltreportCount": 1,
                "last_update": "2026-08-25T23:27:07.000 UTC",
                "source_catalog": "EMSC",
                "auth": "EMSC"
            }
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-3.6449, 37.1728]},
            "properties": {
                "eventid": "quakeml:eu.emsc/event/20260825_0000323",
                "source_id": 2051118,
                "time": "2026-08-25T21:41:08.250 UTC",
                "magtype": "ml",
                "mag": 1.7,
                "lon": -3.645,
                "lat": 37.173,
                "depth": 8.3,
                "region": "SPAIN",
                "feltreportCount": 4,
                "last_update": "2026-08-25T21:56:44.000 UTC",
                "source_catalog": "EMSC",
                "auth": "EMSC"
            }
        }
    ]
}
"""
)


def test_felt_joins_on_the_key_both_sides_publish() -> None:
    """`quakeml:eu.emsc/event/20260825_0000340` -> `20260825_0000340`, which
    is exactly what `emsc_ws.py` stores as `source_id`. No time-and-distance
    matching, no guessing."""
    assert parse_unid("quakeml:eu.emsc/event/20260825_0000340") == "20260825_0000340"
    assert parse_unid(None) is None
    assert parse_unid("") is None


def test_felt_counts_are_read_from_the_real_payload() -> None:
    counts = parse_counts(EMSC_TESTIMONIES)
    assert counts == {"20260825_0000340": 1, "20260825_0000323": 4}


def test_felt_never_writes_a_zero() -> None:
    """A count of zero is EMSC saying it knows of nobody, which is not the
    same claim as "nobody felt it"."""
    payload = {
        "features": [
            {
                "properties": {
                    "eventid": "quakeml:eu.emsc/event/20260825_0000001",
                    "feltreportCount": 0,
                }
            }
        ]
    }
    assert parse_counts(payload) == {}


@pytest.mark.asyncio
async def test_felt_poll_returns_only_what_moved() -> None:
    """The window is re-queried every couple of minutes and republishes the
    same events. Announcing an unchanged count would make every quake in the
    window a fresh revision, forever."""
    responses = [
        EMSC_TESTIMONIES,
        EMSC_TESTIMONIES,
        {
            "features": [
                {
                    "properties": {
                        "eventid": "quakeml:eu.emsc/event/20260825_0000340",
                        "feltreportCount": 57,
                    }
                }
            ]
        },
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = responses[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=payload)

    cache = FeltReportCache()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        first = await cache.poll(client)
        assert first == {"20260825_0000340": 1, "20260825_0000323": 4}
        assert await cache.poll(client) == {}
        assert await cache.poll(client) == {"20260825_0000340": 57}
    assert cache.count_for("20260825_0000340") == 57
    assert cache.count_for("nothing-like-this") is None


# ------------------------------------------------------------ tsunami text

# tsunami.gov/events/PHEB/2026/07/28/26209004/1/WEPA40/WEPA40.txt, the
# 2026-07-28 Kyushu M7.1 PTWC threat message. Columns untouched.
PTWC_THREAT = """
TSUNAMI MESSAGE NUMBER 1
NWS PACIFIC TSUNAMI WARNING CENTER HONOLULU HI
0734 UTC TUE JUL 28 2026

...PTWC TSUNAMI THREAT MESSAGE...


PRELIMINARY EARTHQUAKE PARAMETERS - FROM JMA
--------------------------------------------

  * MAGNITUDE      7.1
  * ORIGIN TIME    0728 UTC JUL 28 2026
  * COORDINATES    32.6 NORTH  130.7 EAST
  * DEPTH          10 KM / 6 MILES
  * LOCATION       KYUSHU  JAPAN


ESTIMATED TIMES OF ARRIVAL
--------------------------

  * ESTIMATED TIMES OF ARRIVAL -ETA- OF THE INITIAL TSUNAMI WAVE
    FOR PLACES WITH A POTENTIAL TSUNAMI THREAT. ACTUAL ARRIVAL
    TIMES MAY DIFFER AND THE INITIAL WAVE MAY NOT BE THE
    LARGEST. A TSUNAMI IS A SERIES OF WAVES AND THE TIME BETWEEN
    WAVES CAN BE FIVE MINUTES TO ONE HOUR.

    LOCATION         REGION             COORDINATES    ETA(UTC)
    ------------------------------------------------------------
    NAGASAKI         JAPAN             32.7N 129.7E   0754 07/28
    NOBEOKA          JAPAN             32.5N 131.8E   0932 07/28
    SHIMIZU          JAPAN             32.8N 133.0E   0942 07/28


POTENTIAL IMPACTS
-----------------

  * A TSUNAMI IS A SERIES OF WAVES.

$$
"""

# tsunami.gov/events/PAAQ/2026/08/24/tkaiuo/1/WEAK53/WEAK53.txt -- the
# ordinary case, and by far the commonest: an Information statement. There
# is no arrival table and no gauge table, and there must be no answer.
NTWC_INFORMATION = """
Tsunami Information Statement Number 1
NWS National Tsunami Warning Center Palmer AK
1221 PM AKDT Mon Aug 24 2026

...THIS IS A TSUNAMI INFORMATION STATEMENT...

EVALUATION
----------
 * There is NO tsunami danger from this earthquake.


PRELIMINARY EARTHQUAKE PARAMETERS
---------------------------------

 * The following parameters are based on a rapid preliminary
   assessment of the earthquake and changes may occur.

 * Magnitude      4.1
 * Origin Time    1214 AKDT Aug 24 2026
                  1314  PDT Aug 24 2026
                  2014  UTC Aug 24 2026
 * Coordinates    59.0 North 154.3 West
 * Depth          60 miles
 * Location       60 miles SE of Iliamna, Alaska
                  215 miles SW of Anchorage, Alaska

$$
"""

# NWS product TSUAK1 / WEAK51 PAAQ 292335, the first NTWC message of the
# 2025-07-29 Kamchatka M8.8. Local times, no year, no coordinates.
NTWC_FORECAST = """
BULLETIN
Public Tsunami Message Number 1
NWS National Tsunami Warning Center Palmer AK
435 PM PDT Tue Jul 29 2025

...A TSUNAMI ADVISORY IS NOW IN EFFECT...

PRELIMINARY EARTHQUAKE PARAMETERS
---------------------------------

 * Magnitude      8.0
 * Origin Time    1525 AKDT Jul 29 2025
                  1625  PDT Jul 29 2025
                  2325  UTC Jul 29 2025
 * Coordinates    52.2 North 160.0 East


FORECASTS OF TSUNAMI ACTIVITY
-----------------------------
 * Tsunami activity is forecasted to start at the following
   locations at the specified times.

                 FORECAST
                 START
SITE             OF TSUNAMI
----             ----------

 * Alaska
Shemya           1646 AKDT Jul 29
Adak             1746 AKDT Jul 29
Saint Paul       1906 AKDT Jul 29


OBSERVATIONS OF TSUNAMI ACTIVITY
--------------------------------
 * No tsunami observations are available to report.

$$
"""

# NWS product TSUAK1 / WEAK51 PAAQ 311417, the final message of the same
# event. Feet, local abbreviations, and one gauge with no time at all.
NTWC_OBSERVATIONS = """
BULLETIN
Public Tsunami Message Number 35
NWS National Tsunami Warning Center Palmer AK
717 AM PDT Thu Jul 31 2025

...THE TSUNAMI ADVISORY IS CANCELLED...

OBSERVATIONS OF TSUNAMI ACTIVITY - UPDATED
------------------------------------------
 * Observed max tsunami height is the highest recorded water level
   above the tide level up to the time of this message.

                                  TIME               OBSERVED MAX
 SITE                         OF MEASUREMENT         TSUNAMI HEIGHT
 ---------------------------- ----------------       --------------
 Amchitka  Alaska             0056  PDT Jul 30           1.7ft
 Saint Paul  Alaska                                      0.4ft
 Crescent City  CA            0139  PDT Jul 30           4.0ft
 Kushiro  Japan                                          1.1ft

$$
"""

# NWS product TSUPAC / WEPA40 PHEB, the PTWC side of the same event. Metres
# and feet on one line, times in UTC, DART buoys among the gauges.
PTWC_OBSERVATIONS = """
TSUNAMI MESSAGE NUMBER 24
NWS PACIFIC TSUNAMI WARNING CENTER HONOLULU HI
0157 UTC THU JUL 31 2025

TSUNAMI OBSERVATIONS
--------------------

  * THE FOLLOWING ARE TSUNAMI WAVE OBSERVATIONS FROM COASTAL
    AND/OR DEEP-OCEAN SEA LEVEL GAUGES AT THE INDICATED
    LOCATIONS. THE MAXIMUM TSUNAMI HEIGHT IS MEASURED WITH
    RESPECT TO THE NORMAL TIDE LEVEL.

                            GAUGE      TIME OF   MAXIMUM     WAVE
                         COORDINATES   MEASURE   TSUNAMI   PERIOD
    GAUGE LOCATION        LAT   LON     (UTC)     HEIGHT    (MIN)
    -------------------------------------------------------------
    CORONEL CL           37.0S  73.2W    0129   1.03M/ 3.4FT  38
    LEBU CL              37.6S  73.7W    2224   0.55M/ 1.8FT  16
    COLIUMO CL           36.5S  73.0W    2247   1.12M/ 3.7FT  28
    DART 21419           44.4N 155.7E    0151   0.09M/ 0.3FT  42

$$
"""

ISSUED_2026 = datetime(2026, 7, 28, 7, 36, tzinfo=UTC)
ISSUED_2025_START = datetime(2025, 7, 29, 23, 35, tzinfo=UTC)
ISSUED_2025_END = datetime(2025, 7, 31, 14, 17, tzinfo=UTC)


def test_ptwc_arrival_times_are_utc() -> None:
    bulletin = parse_bulletin(PTWC_THREAT, ISSUED_2026)
    assert [a.site for a in bulletin.arrivals] == [
        "NAGASAKI, JAPAN",
        "NOBEOKA, JAPAN",
        "SHIMIZU, JAPAN",
    ]
    first = bulletin.first_arrival
    assert first is not None
    assert first.at == datetime(2026, 7, 28, 7, 54, tzinfo=UTC)
    assert first.site == "NAGASAKI, JAPAN"


def test_the_origin_time_is_not_an_arrival() -> None:
    """`* Origin Time    1525 AKDT Jul 29 2025` has the exact shape of an NTWC
    arrival row. Matched outside its section it becomes "first wave reaches
    * Origin Time" -- a sentence built from the earthquake's own clock. Rows
    are read only inside their underlined section, and this is the test that
    holds that line."""
    bulletin = parse_bulletin(NTWC_FORECAST, ISSUED_2025_START)
    assert [a.site for a in bulletin.arrivals] == ["Shemya", "Adak", "Saint Paul"]
    assert all("Origin" not in a.site for a in bulletin.arrivals)


def test_ntwc_local_time_becomes_utc() -> None:
    """`1646 AKDT Jul 29` is 00:46 UTC on the 30th. AKDT is UTC-8, and the
    abbreviation itself carries the summer-time answer -- there is no rule to
    apply and nothing to guess."""
    bulletin = parse_bulletin(NTWC_FORECAST, ISSUED_2025_START)
    first = bulletin.first_arrival
    assert first is not None
    assert first.site == "Shemya"
    assert first.at == datetime(2025, 7, 30, 0, 46, tzinfo=UTC)


def test_an_unknown_time_zone_is_refused_not_assumed() -> None:
    """Defaulting to UTC would put a tsunami arrival eight hours wrong and say
    nothing about it. Lesson 15, applied to a clock."""
    invented = NTWC_FORECAST.replace("AKDT", "ZQTX")
    assert parse_bulletin(invented, ISSUED_2025_START).arrivals == []


def test_information_statement_says_nothing_and_that_is_correct() -> None:
    """Nearly every bulletin these feeds publish is one of these. An empty
    Bulletin is falsy, so nothing is attached and nothing is claimed."""
    bulletin = parse_bulletin(NTWC_INFORMATION, datetime(2026, 8, 24, 20, 21, tzinfo=UTC))
    assert not bulletin
    assert bulletin.first_arrival is None
    assert bulletin.largest is None


def test_ptwc_gauge_readings_are_metres() -> None:
    bulletin = parse_bulletin(PTWC_OBSERVATIONS, ISSUED_2025_END)
    assert len(bulletin.observations) == 4
    largest = bulletin.largest
    assert largest is not None
    assert largest.site == "COLIUMO CL"
    assert largest.metres == pytest.approx(1.12)


def test_ntwc_gauge_readings_are_feet_and_survive_a_missing_clock() -> None:
    """Saint Paul reported 0.4ft with no time of measurement. Dropping that
    row would be dropping an observation because its clock was missing."""
    bulletin = parse_bulletin(NTWC_OBSERVATIONS, ISSUED_2025_END)
    by_site = {o.site: o for o in bulletin.observations}
    assert by_site["Amchitka, Alaska"].metres == pytest.approx(1.7 * 0.3048, abs=0.005)
    assert by_site["Amchitka, Alaska"].at == datetime(2025, 7, 30, 7, 56, tzinfo=UTC)
    assert by_site["Saint Paul, Alaska"].at is None
    assert by_site["Saint Paul, Alaska"].metres == pytest.approx(0.12, abs=0.005)
    largest = bulletin.largest
    assert largest is not None
    assert largest.site == "Crescent City, CA"
    assert largest.metres == pytest.approx(1.22, abs=0.005)


def test_a_tab_aligned_bulletin_reads_the_same() -> None:
    """The same product goes out space-aligned and tab-aligned. Every column
    rule here is written in spaces, so tabs are expanded first."""
    tabbed = NTWC_OBSERVATIONS.replace(
        " Amchitka  Alaska             0056  PDT Jul 30           1.7ft",
        "Amchitka\tAlaska\t      0056  PDT Jul 30\t\t 1.7ft",
    )
    bulletin = parse_bulletin(tabbed, ISSUED_2025_END)
    assert any(o.site == "Amchitka, Alaska" for o in bulletin.observations)


def test_an_absurd_height_is_refused() -> None:
    absurd = PTWC_OBSERVATIONS.replace("1.03M/ 3.4FT", "903M/ 2963FT")
    sites = {o.site for o in parse_bulletin(absurd, ISSUED_2025_END).observations}
    assert "CORONEL CL" not in sites


@pytest.mark.asyncio
async def test_a_bulletin_is_fetched_once() -> None:
    """The Atom feed republishes the same entry every 30 seconds for as long
    as it is the latest bulletin, and the document at that URL never
    changes."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=PTWC_THREAT)

    cache = BulletinCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        url = "https://www.tsunami.gov/events/PHEB/2026/07/28/26209004/1/WEPA40/WEPA40.txt"
        first = await cache.get(client, url, ISSUED_2026)
        again = await cache.get(client, url, ISSUED_2026)
    assert calls["n"] == 1
    assert first.first_arrival == again.first_arrival


@pytest.mark.asyncio
async def test_a_failed_bulletin_costs_the_arrival_time_not_the_alert() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    cache = BulletinCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bulletin = await cache.get(client, "https://www.tsunami.gov/x.txt", ISSUED_2026)
    assert not bulletin
    # NOT cached: a network failure is not a permanent answer about the
    # content, unlike "this bulletin has no table".
    assert cache.known("https://www.tsunami.gov/x.txt") is None


# ----------------------------------------------------- the tsunami wiring

# tsunami.gov/events/xml/PAAQAtom.xml, fetched 2026-08-24. Both links kept
# verbatim: the CAP document comes FIRST in the entry, and the parser has to
# walk past it -- the CAP was checked against the real file for this same
# event and carries no arrival table and no gauge reading.
PAAQ_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
   <entry>
    <title>60 miles SE of Iliamna, Alaska</title><updated>2026-08-24T20:21:42Z</updated>
<geo:lat>59.000</geo:lat>
<geo:long>-154.300</geo:long>
<summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
     <strong>Category:</strong> Information<br/><strong>Preliminary Magnitude: </strong>4.1(Mwp)<br/></div></summary>
    <id>urn:uuid:7562d0a0-6bb2-4df0-a5f7-8e6496c52ab0</id><link rel="related" title="CapXML document" href="https://www.tsunami.gov/events/PAAQ/2026/08/24/tkaiuo/1/WEAK53/PAAQCAP.xml" type="application/cap+xml" />
<link rel="alternate" title="Bulletin" href="https://www.tsunami.gov/events/PAAQ/2026/08/24/tkaiuo/1/WEAK53/WEAK53.txt" type="application/xml" />
</entry>
</feed>
"""


def test_the_text_bulletin_is_the_link_we_want() -> None:
    """The CAP document is listed first and is the wrong file: it has the
    instruction, not the arrival table."""
    import xml.etree.ElementTree as ET

    from app.sources.tsunami import NS, text_bulletin_link

    entry = ET.fromstring(PAAQ_ATOM).find(".//atom:entry", NS)
    assert entry is not None
    link = text_bulletin_link(entry)
    assert link is not None
    assert link.endswith("WEAK53.txt")


def test_applying_a_bulletin_adds_without_inventing() -> None:
    import xml.etree.ElementTree as ET

    from app.sources.tsunami import NS, apply_bulletin, parse_entry

    entry = ET.fromstring(PAAQ_ATOM).find(".//atom:entry", NS)
    assert entry is not None
    event = parse_entry(entry, "PAAQ")
    assert event is not None
    assert event.wave_eta is None

    apply_bulletin(event, parse_bulletin(PTWC_THREAT, ISSUED_2026))
    assert event.wave_eta == datetime(2026, 7, 28, 7, 54, tzinfo=UTC)
    assert event.wave_eta_site == "NAGASAKI, JAPAN"

    # An empty bulletin -- what an Information statement yields, and what a
    # failed fetch yields -- changes nothing it does not know about.
    apply_bulletin(event, parse_bulletin(NTWC_INFORMATION, ISSUED_2026))
    assert event.wave_eta == datetime(2026, 7, 28, 7, 54, tzinfo=UTC)
    assert event.wave_max_m is None


# ------------------------------------------------------------- fingerprint


def _quake(**overrides: object) -> Event:
    base: dict = {
        "id": "usgs:aka2026qoynpe",
        "source": "usgs",
        "source_id": "aka2026qoynpe",
        "time": datetime(2026, 8, 20, 10, 22, 40, tzinfo=UTC),
        "magnitude": 3.9,
        "place": "1 km WSW of Hope, Alaska",
    }
    base.update(overrides)
    return Event(**base)


def test_a_growing_felt_count_is_a_revision() -> None:
    """The whole reason these fields are worth having is that they arrive
    LATE: DYFI needs minutes to collect reports and a gauge needs the wave to
    travel. `EventStore.upsert` compares fingerprints, so a field outside the
    fingerprint updates in the source and never in the browser -- the store
    calls it a no-op and keeps the version where nobody had felt anything."""
    quiet = _quake()
    felt = _quake(felt_reports=947, intensity_cdi=3.8, intensity_mmi=3.417)
    assert quiet.fingerprint() != felt.fingerprint()
    assert felt.fingerprint() != _quake(felt_reports=1200).fingerprint()


def test_a_first_gauge_reading_is_a_revision() -> None:
    warning = _quake(id="tsunami:PHEB:x", source="tsunami", source_id="x")
    observed = _quake(
        id="tsunami:PHEB:x",
        source="tsunami",
        source_id="x",
        wave_max_m=1.12,
        wave_max_site="COLIUMO CL",
        wave_eta=datetime(2026, 8, 20, 11, 0, tzinfo=UTC),
    )
    assert warning.fingerprint() != observed.fingerprint()


def test_a_rewritten_instruction_is_a_revision() -> None:
    """A warning that is extended or upgraded rewrites what to do, and that
    rewrite is the reason to re-read the alert at all."""
    watch = _quake(instruction="Monitor the forecast.", response_type="Monitor")
    warning = _quake(instruction="TAKE COVER NOW!", response_type="Shelter")
    assert watch.fingerprint() != warning.fingerprint()
