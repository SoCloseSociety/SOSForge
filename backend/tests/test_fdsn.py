"""FDSN `format=text` services: NRCan (Canada) and NOA (Greece).

The fixtures are VERBATIM excerpts of the real responses, captured
2026-08-26:

- `NRCAN_TEXT` from
  `https://www.earthquakescanada.nrcan.gc.ca/fdsnws/event/1/query?format=text`
  -- its 8-column header and six of the 309 rows of a 30-day window, chosen
  because each one carries a trap: a bilingual label, a US event inside the
  Canadian feed, a felt marker, an induced event, a quarry blast, and a
  NEGATIVE magnitude.
- `NOA_TEXT` from `https://eida.gein.noa.gr/fdsnws/event/1/query?format=text`
  -- its 14-column header (same declared format, six more columns) and six of
  214 rows: eleven-digit magnitudes, a border region, and two foreign events.
- `NOA_TRUNCATION_TAIL` is what NOA appends to a **200** body when the result
  set is too large. Nothing about it is invented; it is the tail of a real
  60-day request.
"""

from __future__ import annotations

import pytest

from app.models.event import Kind, Severity
from app.sources.fdsn import (
    FdsnTruncated,
    NoaSource,
    NrcanSource,
    parse_fdsn_text,
)

NRCAN_TEXT = (
    "#EventID|Time|Latitude|Longitude|Depth/km|MagType|Magnitude|EventLocationName\n20260801."
    "1257002|2026-08-01T12:57:50.000Z|49.4091|-129.6418|10|mb|4.43|212 km SW of Port Hardy, B"
    "C/212 km SO de Port Hardy, BC\n20260820.1614001|2026-08-20T16:14:41.000Z|59.0342|-136.36"
    "43|1|ML|2.98|138 km NW of Juneau, AK/138 km NO de Juneau, AK\n20260819.0617001|2026-08-1"
    "9T06:17:49.000Z|49.3406|-119.2808|15.83|ML|2.85|29 km SE of Penticton, BC, felt/29 km SE"
    " de Penticton, BC, ressenti\n20260811.1444001|2026-08-11T14:44:24.000Z|54.2798|-117.6811"
    "|5.41|MLy|3.62|Suspected industry-related event, 58 km WSW of Fox Creek, AB/Événement li"
    "é à l'industrie soupconné, 58 km OSO de Fox Creek, AB\n20260731.1603002|2026-07-31T16:03"
    ":19.000Z|44.7172|-63.516|0|MwN|1.61|Blast, 10 km NE of Halifax, NS, felt/Dynamitage, 10 "
    "km NE de Halifax, NS, ressenti\n20260815.0956001|2026-08-15T09:56:16.000Z|47.5635|-70.25"
    "26|8.3|MwN|-0.07|12 km SW of La Malbaie, QC/12 km SO de La Malbaie, QC\n"
)


NOA_TEXT = (
    "#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|ContributorID|MagTy"
    "pe|Magnitude|MagAuthor|EventLocationName|EventType\nnoa2026qrwnd|2026-08-26T10:01:58.141"
    "569|38.050690|24.038544|8.346537272|E.Daskalaki||NOAIG|noa2026qrwnd|MLh|1.008569645|E.Da"
    "skalaki|Aegean Sea|earthquake\nnoa2026qrmmc|2026-08-26T04:57:45.934456|39.710541|20.7884"
    "22|11.19891357|F.Xalaris||NOAIG|noa2026qrmmc|MLh|1.488518193|F.Xalaris|Greece-Albania Bo"
    "rder Region|earthquake\nnoa2026qqita|2026-08-25T13:56:37.065187|40.954285|19.723206|16.8"
    "6088053|A.Rigopoulos||NOAIG|noa2026qqita|MLh|3.541419558|A.Rigopoulos|Albania|earthquake"
    "\nnoa2026qoggp|2026-08-24T10:25:30.827065|39.349823|26.243134|12.36838786|F.Xalaris||NOA"
    "IG|noa2026qoggp|MLh|2.052554738|F.Xalaris|Turkey|earthquake\nnoa2026qrnjy|2026-08-26T05:"
    "25:32.681777|35.018921|24.486694|18.14444987|F.Xalaris||NOAIG|noa2026qrnjy|MLh|2.3181159"
    "68|F.Xalaris|Crete, Greece|earthquake\nnoa2026qrluk|2026-08-26T04:37:07.581689|37.109528"
    "|22.138824|10.54286702|E.Daskalaki||NOAIG|noa2026qrluk|MLh|0.8905484546|E.Daskalaki|Sout"
    "hern Greece|earthquake\n"
)


NOA_TRUNCATION_TAIL = (
    "Error 413: Request Entity Too Large\n\nThe result set of your request exceeds the config"
    "ured maximum number of objects (5000). Refine your request parameters.\n\nUsage details "
    "are available from /fdsnws/event/1/\n\nRequest:\n/fdsnws/event/1/query?starttime=2026-06"
    "-26T00:00:00&format=text&orderby=time\n\nRequest Submitted:\n2026-08-26T10:41:15.938554"
    "\n\nService Version:\n1.2.4\n"
)


def nrcan():
    return {e.source_id: e for e in NrcanSource().parse_payload(NRCAN_TEXT)}


def noa():
    return {e.source_id: e for e in NoaSource().parse_payload(NOA_TEXT)}


# ------------------------------------------------------- the shared text parser


def test_the_table_is_read_by_its_header_not_by_column_position():
    """The reason this parser exists. Both services declare `format=text` and
    NRCan serves 8 columns where NOA serves 14. Indexing by position would
    read NRCan's magnitude (index 6) out of NOA's `MagAuthor`."""
    ca = parse_fdsn_text(NRCAN_TEXT)
    gr = parse_fdsn_text(NOA_TEXT)
    assert len(ca[0]) == 8
    assert len(gr[0]) == 14
    # Same meaning, different index, and both come out right.
    assert ca[0]["Magnitude"] == "4.43"
    assert gr[0]["Magnitude"] == "1.008569645"
    assert "MagAuthor" not in ca[0]


def test_a_row_that_does_not_match_the_header_is_skipped_not_guessed():
    """Guessing which column slipped is how a magnitude lands in the depth
    field. The other rows must survive it."""
    broken = NRCAN_TEXT.replace(
        "20260820.1614001|2026-08-20T16:14:41.000Z|59.0342|-136.3643|1|ML|2.98|",
        "20260820.1614001|2026-08-20T16:14:41.000Z|59.0342|1|ML|2.98|",
    )
    rows = parse_fdsn_text(broken)
    assert len(rows) == 5
    assert all(r["EventID"] != "20260820.1614001" for r in rows)


def test_a_truncated_body_raises_instead_of_being_ingested():
    """NOA answers **HTTP 200** and then appends `Error 413` to the body when
    the result set is too big. `raise_for_status()` never fires, so a client
    that did not look would ingest a silently partial list forever and report
    itself healthy. On this product that is the worst failure mode there is."""
    with pytest.raises(FdsnTruncated):
        parse_fdsn_text(NOA_TEXT + NOA_TRUNCATION_TAIL)


def test_the_truncation_check_runs_before_any_row_is_kept():
    """A partial batch is not a batch to salvage: half a feed presented as a
    whole one is exactly the lie the product forbids."""
    with pytest.raises(FdsnTruncated):
        NoaSource().parse_payload(NOA_TEXT + NOA_TRUNCATION_TAIL)


# --------------------------------------------------------------------- NRCan


def test_nrcan_timestamps_are_utc_and_carry_their_marker():
    events = nrcan()
    quake = events["20260801.1257002"]
    assert quake.time.isoformat() == "2026-08-01T12:57:50+00:00"


def test_the_bilingual_label_is_not_shipped_twice():
    """`253 km SSW of Port Hardy, BC/253 km SO de Port Hardy, BC` is ONE
    place, written twice. Measured over 309 events: exactly one slash each."""
    quake = nrcan()["20260801.1257002"]
    assert quake.place == "212 km SW of Port Hardy, BC"
    assert "/" not in quake.place
    # the French half is not lost, it is just not the label
    assert "212 km SO de Port Hardy" in quake.raw["location_bilingual"]


def test_a_us_event_in_the_canadian_feed_is_not_stamped_canada():
    """Lesson 16, in a second feed. NRCan relays AK, WA and OH events; a
    Canadian flag on an earthquake near Cleveland is false information.
    `app.countries.resolve` reads "138 km NW of Juneau, AK" as US on its own,
    so leaving it None is not a loss."""
    juneau = nrcan()["20260820.1614001"]
    assert juneau.country is None
    assert juneau.place.endswith(", AK")
    # and a real Canadian one IS stamped
    assert nrcan()["20260801.1257002"].country == "Canada"


def test_a_quarry_blast_does_not_go_out_as_an_earthquake():
    """NRCan's 8-column header has no EventType: "Blast" only exists in the
    location text. 49 of 309 rows over 30 days are non-tectonic."""
    blast = nrcan()["20260731.1603002"]
    assert blast.kind is Kind.OTHER
    assert blast.raw["explosion"] is True
    assert blast.place.startswith("Blast,")
    # it is not dropped either: people felt it and will look for it
    assert blast.raw["felt"] is True


def test_induced_seismicity_stays_an_earthquake_and_says_so():
    """An M3.6 near Fox Creek shakes houses whatever caused it. Hiding it
    would be narrowing detection at a cost nobody measured (lesson 19)."""
    induced = nrcan()["20260811.1444001"]
    assert induced.kind is Kind.EARTHQUAKE
    assert induced.raw["induced"] is True
    assert induced.alert == "induced"
    assert induced.magnitude == 3.6


def test_the_felt_marker_is_read_and_stripped_from_the_place():
    felt = nrcan()["20260819.0617001"]
    assert felt.raw["felt"] is True
    assert felt.alert == "felt"
    # ", felt" is a flag, not part of the place name
    assert felt.place == "29 km SE of Penticton, BC"
    assert felt.country == "Canada"


def test_a_negative_magnitude_is_real_and_survives():
    """`-0.07`, 12 km SW of La Malbaie, QC. A guard that rejected it would
    silently drop the smallest events of the most instrumented Canadian
    seismic zone."""
    tiny = nrcan()["20260815.0956001"]
    assert tiny.magnitude == -0.1
    assert tiny.severity is Severity.INFO
    assert tiny.country == "Canada"


def test_the_dead_permalink_is_never_shipped():
    """`index-en.php?tpl_region=canada&id=<id>` answers 200 and looks like a
    per-event page. It is not: the id is echoed into the French language-switch
    href and nowhere else. Verified live."""
    for event in nrcan().values():
        assert event.url == "https://www.earthquakescanada.nrcan.gc.ca/recent/index-en.php"
        assert "id=" not in event.url


def test_a_quake_is_a_point_in_time_never_an_ongoing_alert():
    """`ongoing` would put these in reach of the silence sweep, and both feeds
    stop listing an event as soon as it leaves the requested window."""
    for event in list(nrcan().values()) + list(noa().values()):
        assert event.ongoing is False
        assert event.expires is None


# ----------------------------------------------------------------------- NOA


def test_noa_timestamps_have_no_offset_and_are_utc():
    """The shape that costs six hours elsewhere in this product. Proven, not
    assumed: matching the whole live feed against USGS + EMSC at every offset
    from -8 h to +8 h peaks at exactly 0 h. Greece is UTC+3 in summer, so a
    local-time feed would have peaked at -3."""
    quake = noa()["noa2026qrwnd"]
    assert quake.time.isoformat() == "2026-08-26T10:01:58.141569+00:00"
    assert quake.time.utcoffset().total_seconds() == 0


def test_eleven_digits_of_magnitude_are_not_eleven_digits_of_precision():
    """NOA publishes `1.008569645` and `8.346537272`. Passing those on would
    have made the UI look like it was inventing precision no seismograph has."""
    quake = noa()["noa2026qrwnd"]
    assert quake.magnitude == 1.0
    assert quake.depth_km == 8.3


def test_greece_is_stamped_only_when_the_label_names_greece_alone():
    events = noa()
    assert events["noa2026qrluk"].country == "Greece"  # "Southern Greece"
    assert events["noa2026qrnjy"].country == "Greece"  # "Crete, Greece"


def test_a_border_region_belongs_to_nobody():
    """Lesson 12. The epicentre of a `Greece-Albania Border Region` event is
    honestly on either side of it, so the UI shows a globe rather than picking."""
    border = noa()["noa2026qrmmc"]
    assert border.country is None
    assert border.place == "Greece-Albania Border Region"


def test_a_foreign_event_in_the_greek_feed_is_left_to_the_pipeline():
    """NOA's catalogue carries Albania and Turkey. Neither is Greece, and
    `app.countries.resolve` reads both from the same text on its own."""
    events = noa()
    assert events["noa2026qqita"].country is None
    assert events["noa2026qqita"].place == "Albania"
    assert events["noa2026qoggp"].country is None
    assert events["noa2026qoggp"].place == "Turkey"


def test_it_carries_the_micro_quakes_the_global_catalogues_drop():
    """The whole point of the source. Measured on 2026-08-26 over 3.5 days,
    198 of NOA's 214 events were in neither USGS nor EMSC."""
    events = noa()
    assert min(e.magnitude for e in events.values()) == 0.9
    assert all(e.kind is Kind.EARTHQUAKE for e in events.values())
    assert events["noa2026qrwnd"].mag_type == "MLh"


def test_both_feeds_come_out_newest_first():
    """Arrival order is an implementation detail of polling; what the user
    reads is a chronology (lesson 5)."""
    for source, text in ((NrcanSource(), NRCAN_TEXT), (NoaSource(), NOA_TEXT)):
        events = source.parse_payload(text)
        assert [e.time for e in events] == sorted((e.time for e in events), reverse=True)


def test_an_out_of_range_coordinate_costs_its_position_not_the_batch():
    """Lesson 15, and the model now RAISES on an out-of-range coordinate: one
    bad row unguarded would take the whole batch down with it."""
    broken = NOA_TEXT.replace("|38.050690|24.038544|", "|3237.5|24.038544|")
    events = NoaSource().parse_payload(broken)
    assert len(events) == 6
    bad = next(e for e in events if e.source_id == "noa2026qrwnd")
    assert bad.lat is None
    assert bad.lon == 24.038544
    assert bad.magnitude == 1.0


def test_the_window_is_recomputed_every_cycle_and_never_uses_a_limit():
    """NOA refuses the request outright without `starttime` (bare Apache 400),
    and a fixed `limit` would silently hide the newest events the day a feed
    gets busy."""
    url = NoaSource(window_hours=24.0).build_url()
    assert "starttime=" in url
    assert "format=text" in url
    assert "limit" not in url


def test_an_empty_window_is_not_a_failure():
    """NRCan answers `204 No Content` with an empty body when nothing was
    published in the window -- which, given its review delay, is its normal
    state for several hours a day. Zero events is zero events, not an error."""
    assert parse_fdsn_text("") == []
    assert NrcanSource().parse_payload("") == []


def test_the_nrcan_window_clears_its_measured_publication_lag():
    """The window filters on EVENT time while the lag delays PUBLICATION, so a
    window near the size of the lag shows only the thin band between the two.
    Measured lag on 2026-08-26: 19.6 h. At 24 h this source would have shown
    about four hours' worth of events and silently dropped the rest."""
    assert NrcanSource().window_hours >= 72.0
    # NOA is genuinely real-time and does not need the margin
    assert NoaSource().window_hours == 24.0
