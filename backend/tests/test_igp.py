"""IGP -- Instituto Geofisico del Peru.

The fixture is a VERBATIM excerpt of
`https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/2026`, captured on
2026-08-26: four of the 591 objects of the year, sliced out of the real
response body rather than re-serialized, so the field order, the string-typed
numbers and the empty `tipomagnitud` are exactly as served.

The four were chosen for what each one proves: `2026-0004` is the case where
the LOCAL date and the UTC date disagree (1 January locally, 2 January UTC),
`2026-0589` has no intensity, `2026-0590` and `2026-0591` are the two most
recent events of the feed and both carry a Mercalli range.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.models.event import Kind, Severity
from app.sources.igp import IgpSource, parse_intensity, years_to_poll

IGP_JSON = (
    '[{"codigo":"2026-0004","reporte_acelerometrico_pdf":"https://www.igp.gob.pe/servicios/ap'
    'i-acelerometrica/ran/file/20260004_20260102_040839.pdf","idlistasismos":10815,"fecha_loc'
    'al":"2026-01-01T00:00:00.000Z","hora_local":"1970-01-01T23:08:39.000Z","fecha_utc":"2026'
    '-01-02T00:00:00.000Z","hora_utc":"1970-01-01T04:08:39.000Z","latitud":"-10.11","longitud'
    '":"-75.24","magnitud":"3.6","profundidad":16,"referencia":"35 km al E de Pozuzo, Oxapamp'
    'a - Pasco","referencia2":null,"referencia3":null,"tipomagnitud":"","mapa":"","informe":"'
    '","publicado":"1","numero_reporte":4,"id_pdf_tematico":0,"createdAt":"2026-01-02T04:14:3'
    '4.000Z","updatedAt":"2026-01-02T04:14:34.000Z","intensidad":"II-III Pozuzo"},{"codigo":"'
    '2026-0589","reporte_acelerometrico_pdf":"https://www.igp.gob.pe/servicios/api-aceleromet'
    'rica/ran/file/20260589_20260826_062604.pdf","idlistasismos":11400,"fecha_local":"2026-08'
    '-26T00:00:00.000Z","hora_local":"1970-01-01T01:26:04.000Z","fecha_utc":"2026-08-26T00:00'
    ':00.000Z","hora_utc":"1970-01-01T06:26:04.000Z","latitud":"-3.69","longitud":"-79.76","m'
    'agnitud":"4","profundidad":97,"referencia":"61 km al E de Zarumilla, Zarumilla - Tumbes"'
    ',"referencia2":null,"referencia3":null,"tipomagnitud":"","mapa":"","informe":"","publica'
    'do":"1","numero_reporte":589,"id_pdf_tematico":0,"createdAt":"2026-08-26T06:30:35.000Z",'
    '"updatedAt":"2026-08-26T06:30:35.000Z","intensidad":""},{"codigo":"2026-0590","reporte_a'
    'celerometrico_pdf":"https://www.igp.gob.pe/servicios/api-acelerometrica/ran/file/2026059'
    '0_20260826_074009.pdf","idlistasismos":11401,"fecha_local":"2026-08-26T00:00:00.000Z","h'
    'ora_local":"1970-01-01T02:40:09.000Z","fecha_utc":"2026-08-26T00:00:00.000Z","hora_utc":'
    '"1970-01-01T07:40:09.000Z","latitud":"-12.21","longitud":"-75.32","magnitud":"3.6","prof'
    'undidad":10,"referencia":"17 km al S de Chupaca, Chupaca - Junín","referencia2":null,"re'
    'ferencia3":null,"tipomagnitud":"","mapa":"","informe":"","publicado":"1","numero_reporte'
    '":590,"id_pdf_tematico":0,"createdAt":"2026-08-26T07:43:55.000Z","updatedAt":"2026-08-26'
    'T07:43:55.000Z","intensidad":"II-III Chupaca"},{"codigo":"2026-0591","reporte_aceleromet'
    'rico_pdf":"https://www.igp.gob.pe/servicios/api-acelerometrica/ran/file/20260591_2026082'
    '6_100231.pdf","idlistasismos":11402,"fecha_local":"2026-08-26T00:00:00.000Z","hora_local'
    '":"1970-01-01T05:02:31.000Z","fecha_utc":"2026-08-26T00:00:00.000Z","hora_utc":"1970-01-'
    '01T10:02:31.000Z","latitud":"-9.88","longitud":"-76.42","magnitud":"3.5","profundidad":1'
    '5,"referencia":"20 km al O de Huánuco, Huánuco - Huánuco","referencia2":null,"referencia'
    '3":null,"tipomagnitud":"","mapa":"","informe":"","publicado":"1","numero_reporte":591,"i'
    'd_pdf_tematico":0,"createdAt":"2026-08-26T10:09:40.000Z","updatedAt":"2026-08-26T10:09:4'
    '0.000Z","intensidad":"II-III Huánuco"}]'
)

# The feed is a whole year and the source keeps only a recent window, so the
# tests pin "now" to the day the fixture was captured. A wall-clock default
# would make this file start failing a week after it was written, which is the
# opposite of what a regression test is for.
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def parse(window_days: float = 7.0):
    source = IgpSource(window_days=window_days)
    return source.parse_payload(json.loads(IGP_JSON), now=NOW)


def by_code(window_days: float = 400.0):
    return {e.source_id: e for e in parse(window_days)}


def test_the_instant_is_a_date_from_one_field_and_a_clock_from_another():
    """The trap this source exists to survive.

    `hora_utc` is `"1970-01-01T10:02:31.000Z"`: the epoch date is padding and
    only the clock part is real. Parsing it as a timestamp puts every Peruvian
    earthquake in 1970; parsing `fecha_utc` alone puts it at midnight. The
    instant is one half of each.
    """
    newest = parse()[0]
    assert newest.source_id == "2026-0591"
    assert newest.time == datetime(2026, 8, 26, 10, 2, 31, tzinfo=UTC)


def test_the_utc_pair_is_read_and_not_the_local_one():
    """There are TWO date/time pairs and they are five hours apart. On 147 of
    the year's 591 events the two DATES differ, so mixing them would have been
    right three times out of four and silently a day out for the rest.

    `2026-0004` is one of those: local 2026-01-01 23:08:39, UTC 2026-01-02
    04:08:39. Reading `fecha_local` with `hora_utc` yields 1 January.
    """
    event = by_code()["2026-0004"]
    assert event.time == datetime(2026, 1, 2, 4, 8, 39, tzinfo=UTC)
    assert event.time.day == 2
    # the local reading is kept, clearly labelled, and is NOT the timestamp
    assert event.raw["local_time"] == "2026-01-01 23:08:39"


def test_the_year_long_list_is_trimmed_to_a_recent_window():
    """410 KB and 591 events in August. Emitting all of it every minute would
    be 591 round-trips a cycle to re-say what the store already knows
    (lesson 6: an aggregating source carries its own relevance rule)."""
    recent = parse(window_days=7.0)
    assert [e.source_id for e in recent] == ["2026-0591", "2026-0590", "2026-0589"]
    # the January event is eight months old and does not come through
    assert all(e.source_id != "2026-0004" for e in recent)


def test_the_newest_event_is_the_last_element_of_the_response():
    """The API serves the year OLDEST first. A source that emitted the head
    would have been permanently eight months stale."""
    raw = json.loads(IGP_JSON)
    assert raw[0]["codigo"] == "2026-0004"
    assert raw[-1]["codigo"] == "2026-0591"
    assert parse()[0].source_id == "2026-0591"


def test_strings_that_look_like_numbers_are_parsed_as_numbers():
    """`latitud`, `longitud` and `magnitud` all arrive quoted, and `magnitud`
    is sometimes an integer-looking `"4"`."""
    events = by_code()
    quake = events["2026-0589"]
    assert (quake.lat, quake.lon) == (-3.69, -79.76)
    assert quake.magnitude == 4.0
    assert quake.depth_km == 97.0
    assert quake.kind is Kind.EARTHQUAKE


def test_the_magnitude_scale_is_not_invented_when_the_feed_omits_it():
    """`tipomagnitud` is empty on all 591 rows of the year."""
    for event in by_code().values():
        assert event.mag_type == "M"


def test_the_mercalli_intensity_is_read_out_of_a_field_glued_to_a_place_name():
    """`intensidad` is "II-III Chupaca": the only field in this feed that says
    what people actually felt, which is the question magnitude does not answer.
    Present on 491 of the year's 591 events."""
    events = by_code()
    felt = events["2026-0590"]
    assert felt.intensity_mmi == 3.0
    assert felt.alert == "MMI II-III"
    assert felt.raw["intensidad"] == "II-III Chupaca"


def test_a_missing_intensity_is_absent_not_zero():
    """Most of the planet has no reporters. A zero would claim nobody felt it."""
    quiet = by_code()["2026-0589"]
    assert quiet.intensity_mmi is None
    assert quiet.alert is None


def test_intensity_parsing_takes_the_upper_bound_of_a_range():
    """The IGP publishes the event's MAXIMUM intensity; a range is the span
    observers reported, so the upper bound answers "how hard did this shake"."""
    assert parse_intensity("II-III Chupaca") == (3.0, "II-III")
    assert parse_intensity("III San Juan") == (3.0, "III")
    assert parse_intensity("IV-V Yauca") == (5.0, "IV-V")
    assert parse_intensity("VI Ica") == (6.0, "VI")
    # unreadable yields nothing rather than a guess
    assert parse_intensity("") is None
    assert parse_intensity("sin datos") is None
    assert parse_intensity(None) is None


def test_peru_is_stamped_from_the_region_tail_accents_included():
    """`app.countries.resolve` cannot read the Spanish reference (measured:
    "17 km al S de Chupaca, Chupaca - Junin" returns None). The region tail
    names one of Peru's 24 departments or Callao on all 591 events."""
    for event in by_code().values():
        assert event.country == "Peru"
    # and the accented ones are not a special case
    assert by_code()["2026-0590"].place.endswith("Junín")


def test_a_foreign_reference_is_not_stamped_peru():
    """A national feed is not a claim about the country (lesson 16). Anything
    the region table does not recognise falls through to the pipeline."""
    payload = IGP_JSON.replace(
        "61 km al E de Zarumilla, Zarumilla - Tumbes",
        "61 km al SE de Machala, El Oro - Ecuador",
    )
    source = IgpSource(window_days=400.0)
    events = {e.source_id: e for e in source.parse_payload(json.loads(payload), now=NOW)}
    assert events["2026-0589"].country is None
    assert events["2026-0589"].place.endswith("Ecuador")


def test_an_out_of_range_coordinate_costs_its_position_not_the_batch():
    """Lesson 15, and the model now RAISES on an out-of-range coordinate."""
    payload = IGP_JSON.replace('"latitud":"-9.88"', '"latitud":"3237.5"')
    source = IgpSource(window_days=400.0)
    events = source.parse_payload(json.loads(payload), now=NOW)
    assert len(events) == 4
    bad = next(e for e in events if e.source_id == "2026-0591")
    assert bad.lat is None
    assert bad.lon == -76.42
    assert bad.magnitude == 3.5


def test_a_row_with_no_usable_time_is_dropped_rather_than_guessed():
    payload = IGP_JSON.replace('"hora_utc":"1970-01-01T10:02:31.000Z"', '"hora_utc":null')
    source = IgpSource(window_days=400.0)
    events = source.parse_payload(json.loads(payload), now=NOW)
    assert len(events) == 3
    assert all(e.source_id != "2026-0591" for e in events)


def test_the_new_year_is_not_a_blind_spot():
    """The feed is addressed BY YEAR and a year with no earthquakes yet
    answers 404. An event just before midnight on 31 December UTC also still
    lives in the old list while the clock says the new year."""
    assert years_to_poll(datetime(2026, 8, 26, tzinfo=UTC)) == [2026]
    assert years_to_poll(datetime(2027, 1, 1, 0, 30, tzinfo=UTC)) == [2027, 2026]
    assert years_to_poll(datetime(2027, 1, 7, 23, 59, tzinfo=UTC)) == [2027, 2026]
    # and the overlap stops costing a second request once the year is running
    assert years_to_poll(datetime(2027, 1, 8, tzinfo=UTC)) == [2027]


def test_a_quake_is_a_point_in_time_never_an_ongoing_alert():
    """`ongoing` would put these in reach of the silence sweep, and the IGP
    list is a catalogue that never stops mentioning an event."""
    for event in by_code().values():
        assert event.ongoing is False
        assert event.expires is None


def test_severity_follows_the_house_scale():
    events = by_code()
    assert events["2026-0589"].severity is Severity.MINOR  # M4.0
    assert events["2026-0591"].severity is Severity.MINOR  # M3.5


def test_the_broken_pdf_link_is_never_shipped():
    """`reporte_acelerometrico_pdf` points at a host that times out (curl exit
    28) while the same host's root answers 200. Same discipline the SSN's dead
    permalink earned: keep the path, ship a link that loads."""
    for event in by_code().values():
        assert event.url == "https://ultimosismo.igp.gob.pe/ultimo-sismo/sismos-reportados"
        assert event.raw["report_pdf"].endswith(".pdf")
        assert "api-acelerometrica" not in (event.url or "")
