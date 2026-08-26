"""SSN UNAM (Mexico).

The fixture is a VERBATIM excerpt of
`http://www.ssn.unam.mx/rss/ultimos-sismos.xml`, captured on 2026-08-26:
the XML declaration, the channel header and three of the fifteen items exactly
as served, entities and doubled spaces included.
"""

from __future__ import annotations

from datetime import UTC

from app.models.event import Kind, Severity
from app.sources.ssn import SsnSource

# Written as adjacent literals rather than a triple-quoted block only so the
# long lines fit the 100-column limit. The concatenation is byte-for-byte the
# response as received: tabs, doubled spaces, HTML entities and all.
SSN_XML = (
    '<?xml version="1.0"?>\n'
    '\t<rss version="2.0" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">\n'
    "\t<channel>\n"
    "\t<title>Últimos sismos registrados por el SSN</title>\n"
    "\t\t<link>http://www.ssn.unam.mx</link>\n"
    "\t\t<description>Reporte de los últimos sismos en México</description>\n"
    "\t\t<item>\n"
    "\t\t\t<title>4.0, 27 km al SUROESTE de  COYUCA DE BENITEZ, GRO</title>\n"
    "\t\t\t<description><![CDATA[ <p>Fecha:2026-08-26 01:39:32 (Hora de M&eacute;xico)<br/>Lat/Lon:"
    " 16.876/-100.304<br/>Profundidad: 13.4 km </p> ]]></description>\n"
    "\t\t\t<link><![CDATA[ http://www2.ssn.unam.mx:8080/jsp/localizacion-de-sismo.jsp?latitud=16.87"
    "6&longitud=-100.304&prf=13.4 km&ma=4.0&fecha=2026-08-26&hora=01:39:32&loc=27 km al SUROESTE de"
    "  COYUCA DE BENITEZ, GRO&evento=1 ]]></link>\n"
    "\t\t\t<geo:lat>16.876</geo:lat>\n"
    "\t\t\t<geo:long>-100.304</geo:long>\n"
    "\t\t</item>\n"
    "\t\t<item>\n"
    "\t\t\t<title>3.7, 163 km al SUROESTE de  CIHUATLAN, JAL</title>\n"
    "\t\t\t<description><![CDATA[ <p>Fecha:2026-08-25 16:43:57 (Hora de M&eacute;xico)<br/>Lat/Lon:"
    " 18.218/-105.677<br/>Profundidad: 10.0 km </p> ]]></description>\n"
    "\t\t\t<link><![CDATA[ http://www2.ssn.unam.mx:8080/jsp/localizacion-de-sismo.jsp?latitud=18.21"
    "8&longitud=-105.677&prf=10.0 km&ma=3.7&fecha=2026-08-25&hora=16:43:57&loc=163 km al SUROESTE d"
    "e  CIHUATLAN, JAL&evento=1 ]]></link>\n"
    "\t\t\t<geo:lat>18.218</geo:lat>\n"
    "\t\t\t<geo:long>-105.677</geo:long>\n"
    "\t\t</item>\n"
    "\t\t<item>\n"
    "\t\t\t<title>1.4, 3 km al NOROESTE de  ZACATECAS, ZAC</title>\n"
    "\t\t\t<description><![CDATA[ <p>Fecha:2026-08-25 14:36:33 (Hora de M&eacute;xico)<br/>Lat/Lon:"
    " 22.799/-102.585<br/>Profundidad: 3.8 km </p> ]]></description>\n"
    "\t\t\t<link><![CDATA[ http://www2.ssn.unam.mx:8080/jsp/localizacion-de-sismo.jsp?latitud=22.79"
    "9&longitud=-102.585&prf=3.8 km&ma=1.4&fecha=2026-08-25&hora=14:36:33&loc=3 km al NOROESTE de  "
    "ZACATECAS, ZAC&evento=1 ]]></link>\n"
    "\t\t\t<geo:lat>22.799</geo:lat>\n"
    "\t\t\t<geo:long>-102.585</geo:long>\n"
    "\t\t</item>\n"
    "\t</channel>\n"
    "\t</rss>"
)


def parse():
    return SsnSource().parse_payload(SSN_XML)


def test_the_feed_timestamps_are_mexico_local_not_utc():
    """The trap this source exists to survive.

    Cross-checked against EMSC on the very event in this fixture: the SSN says
    `2026-08-26 01:39:32`, EMSC publishes the same M4.0 at 16.876/-100.304 as
    `2026-08-26T07:39:32Z`. Exactly UTC-6, second for second. Reading these as
    UTC would put every Mexican earthquake six hours in the past.
    """
    events = parse()
    newest = events[0]
    utc = newest.time.astimezone(UTC)
    assert (utc.hour, utc.minute, utc.second) == (7, 39, 32)
    assert utc.day == 26
    assert newest.raw["local_time"] == "2026-08-26 01:39:32"

    # and the second event of the fixture, same check: 16:43:57 -> 22:43:57Z
    other = next(e for e in events if e.magnitude == 3.7)
    assert other.time.astimezone(UTC).hour == 22
    assert other.time.astimezone(UTC).minute == 43


def test_the_key_is_derived_because_the_feed_has_no_identifier():
    """No `guid`, no `pubDate`: the origin time to the second is the key."""
    events = parse()
    assert [e.id for e in events] == [
        "ssn:20260826013932",
        "ssn:20260825164357",
        "ssn:20260825143633",
    ]


def test_magnitude_place_depth_and_position_come_out_of_three_places():
    (newest, *_) = parse()
    assert newest.kind is Kind.EARTHQUAKE
    assert newest.magnitude == 4.0
    assert newest.mag_type == "M"
    assert newest.depth_km == 13.4
    assert (newest.lat, newest.lon) == (16.876, -100.304)
    # the doubled space of the real title is collapsed
    assert newest.place == "27 km al SUROESTE de COYUCA DE BENITEZ, GRO"
    assert newest.title == "M 4.0 -- 27 km al SUROESTE de COYUCA DE BENITEZ, GRO"


def test_it_carries_the_small_quakes_the_global_catalogues_drop():
    """The whole point of the source. Measured on 2026-08-26, USGS returned
    ZERO events over the entire Mexican box for the same window."""
    events = parse()
    assert min(e.magnitude for e in events) == 1.4
    tiny = next(e for e in events if e.magnitude == 1.4)
    assert tiny.severity is Severity.INFO
    assert tiny.place.endswith("ZACATECAS, ZAC")


def test_a_quake_is_a_point_in_time_never_an_ongoing_alert():
    """`ongoing` would put these in reach of the silence sweep, and the SSN
    stops listing an event as soon as it leaves its fifteen-item window."""
    for event in parse():
        assert event.ongoing is False
        assert event.expires is None


def test_mexico_is_stamped_from_the_state_abbreviation():
    for event in parse():
        assert event.country == "Mexico"


def test_a_foreign_location_is_not_stamped_mexico():
    """A national feed is not a claim about the country. The SSN locates
    events across its borders, and `app.countries.resolve` can read those from
    the place text (measured: "..., GUATEMALA" -> GT) while it cannot read a
    Mexican state abbreviation at all -- so the abbreviation is what we stamp
    on, and anything else is left to the pipeline."""
    xml = SSN_XML.replace(
        "4.0, 27 km al SUROESTE de  COYUCA DE BENITEZ, GRO",
        "4.0, 35 km al SUR de TECUN UMAN, GUATEMALA",
    )
    foreign = SsnSource().parse_payload(xml)[0]
    assert foreign.country is None
    assert foreign.place.endswith("GUATEMALA")


def test_one_broken_item_does_not_cost_the_others():
    """An out-of-range coordinate now RAISES in the model: unguarded, a single
    bad row would take the whole batch down with it (lesson 15)."""
    xml = SSN_XML.replace("<geo:lat>16.876</geo:lat>", "<geo:lat>3237.5</geo:lat>")
    events = SsnSource().parse_payload(xml)
    assert len(events) == 3
    assert events[0].lat is None
    # the event survives without a position, and the rest are untouched
    assert events[0].magnitude == 4.0
    assert events[1].lat == 18.218


def test_an_item_with_no_date_is_dropped_rather_than_guessed():
    xml = SSN_XML.replace("Fecha:2026-08-26 01:39:32 (Hora de M&eacute;xico)", "Fecha: n/d")
    events = SsnSource().parse_payload(xml)
    assert len(events) == 2
    assert all(e.id != "ssn:20260826013932" for e in events)


def test_the_dead_permalink_is_never_shipped():
    """`www2.ssn.unam.mx:8080/jsp/...` answers 404 -- verified live."""
    for event in parse():
        assert event.url is not None
        assert "www2.ssn.unam.mx" not in event.url
