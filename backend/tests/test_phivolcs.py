"""PHIVOLCS -- the Philippine national bulletin table.

`PHIVOLCS_ROWS` is a VERBATIM excerpt of
`https://earthquake.phivolcs.dost.gov.ph/`, captured 2026-08-26: six of the
1881 `<tr>` blocks of the August table, byte for byte as served -- inline
styles, tabs, Windows backslashes in the hrefs, doubled spaces and all.

The six were chosen because each one carries a trap the parser has to survive:

1. `2026_0826_1114_B1`   the newest event, and the Philippine-time conversion
2. `2026_0825_1445_B1`   same displayed minute as the next row
3. `2026_0825_144504_B1F` ... and a different earthquake
4. `2026_0825_1252_B1`   400 km offshore, in Indonesian water
5. `2026_0822_231249_B1F` displayed on 23 August, actually 22 August UTC
6. `2026_0818_1350_B2html` an href with the dot before `html` missing
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.event import Kind
from app.sources.phivolcs import (
    PhivolcsSource,
    build_ssl_context,
    bulletin_key,
    pages_to_poll,
    parse_bulletin_time,
    parse_local_time,
)

PHIVOLCS_ROWS = (
    '<tr>\n      <td style="width: 185px; height: 30px; " class="auto-style91">\n      <s'
    'pan class="auto-style7"></span><span class="auto-style70">\n\t\t\t<a href="2026_Ear'
    'thquake_Information\\August\\2026_0826_1114_B1.html">\n\t\t\t<span class="auto-style99'
    '">26 August 2026 - 07:14 PM</span></a></span></td>\n          <td style="width: 77px; '
    'height: 30px; " class="auto-style90" align="center">\n\t\t  21.57</td>\n          <'
    'td style="width: 92px; height: 30px; border-left-style: none; border-right: 1pt solid m'
    "istyrose; border-top: 1pt solid mistyrose; border-bottom: 1pt solid mistyrose; padding: "
    '0.75pt; background: white"\n\n            class="auto-style56" align="center">121.7'
    '5</td>\n          <td style="width: 62px; height: 30px; border-left-style: none; border'
    "-right: 1pt solid mistyrose; border-top: 1pt solid mistyrose; border-bottom: 1pt solid m"
    'istyrose; padding: 0.75pt; background: white"\n\n            class="auto-style56" ali'
    'gn="center">030</td>\n          <td style="width: 52px; height: 30px; border-left-sty'
    "le: none; border-right: 1pt solid mistyrose; border-top: 1pt solid mistyrose; border-bot"
    'tom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\n            class="au'
    'to-style56" align="center">3.4</td>\n          <td style="width: 436px; height: 30px'
    '; " class="auto-style52">\n\t\t  087<span style="font-family: &quot;Times New Roman&'
    "quot;, serif; font-size: 16px; font-style: normal; font-variant-ligatures: normal; font-"
    "variant-caps: normal; letter-spacing: normal; orphans: 2; text-align: start; text-indent"
    ": 0px; text-transform: none; white-space: normal; widows: 2; word-spacing: 0px; -webkit-"
    "text-stroke-width: 0px; background-color: rgb(255, 255, 255); text-decoration-style: ini"
    'tial; text-decoration-color: initial;" class="auto-style89"><span class="auto-style7'
    '8" style="font-size: 10pt;">\n\t\t  km N 07° W of Itbayat (Batanes)</span></span></td'
    '>\n\t\t  </tr>\n<tr>\n      <td style="width: 185px; height: 30px; " class="auto-styl'
    'e91">\n      <span class="auto-style7"></span><span class="auto-style70">\n\t\t\t<a'
    ' href="2026_Earthquake_Information\\August\\2026_0825_1445_B1.html">\n\t\t\t<span clas'
    's="auto-style99">25 August 2026 - 10:45 PM</span></a></span></td>\n          <td style'
    '="width: 77px; height: 30px; " class="auto-style90" align="center">\n\t\t  17.41</'
    'td>\n          <td style="width: 92px; height: 30px; border-left-style: none; border-ri'
    "ght: 1pt solid mistyrose; border-top: 1pt solid mistyrose; border-bottom: 1pt solid mist"
    'yrose; padding: 0.75pt; background: white"\n\n            class="auto-style56" align='
    '"center">122.59</td>\n          <td style="width: 62px; height: 30px; border-left-sty'
    "le: none; border-right: 1pt solid mistyrose; border-top: 1pt solid mistyrose; border-bot"
    'tom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\n            class="au'
    'to-style56" align="center">011</td>\n          <td style="width: 52px; height: 30px;'
    " border-left-style: none; border-right: 1pt solid mistyrose; border-top: 1pt solid misty"
    'rose; border-bottom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\n      '
    '      class="auto-style56" align="center">2.1</td>\n          <td style="width: 436'
    'px; height: 30px; " class="auto-style52">\n\t\t  033<span style="font-family: &quot;'
    "Times New Roman&quot;, serif; font-size: 16px; font-style: normal; font-variant-ligature"
    "s: normal; font-variant-caps: normal; letter-spacing: normal; orphans: 2; text-align: st"
    "art; text-indent: 0px; text-transform: none; white-space: normal; widows: 2; word-spacin"
    "g: 0px; -webkit-text-stroke-width: 0px; background-color: rgb(255, 255, 255); text-decor"
    'ation-style: initial; text-decoration-color: initial;" class="auto-style89"><span cla'
    'ss="auto-style78" style="font-size: 10pt;">\n\t\t  km N 74° E of Divilacan (Isabela)'
    '</span></span></td>\n\t\t  </tr>\n<tr>\n      <td style="width: 185px; height: 30px; "'
    ' class="auto-style91">\n      <span class="auto-style7"></span><span class="auto-st'
    'yle70">\n\t\t\t<a href="2026_Earthquake_Information\\August\\2026_0825_144504_B1F.html'
    '">\n\t\t\t<span class="auto-style99">25 August 2026 - 10:45 PM</span></a></span></td>'
    '\n          <td style="width: 77px; height: 30px; " class="auto-style90" align="cen'
    'ter">\n\t\t  05.39</td>\n          <td style="width: 92px; height: 30px; border-left-s'
    "tyle: none; border-right: 1pt solid mistyrose; border-top: 1pt solid mistyrose; border-b"
    'ottom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\n            class="'
    'auto-style56" align="center">125.27</td>\n          <td style="width: 62px; height: '
    "30px; border-left-style: none; border-right: 1pt solid mistyrose; border-top: 1pt solid "
    'mistyrose; border-bottom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\n '
    '           class="auto-style56" align="center">031</td>\n          <td style="width'
    ": 52px; height: 30px; border-left-style: none; border-right: 1pt solid mistyrose; border"
    "-top: 1pt solid mistyrose; border-bottom: 1pt solid mistyrose; padding: 0.75pt; backgrou"
    'nd: white"\n\n            class="auto-style56" align="center">1.5</td>\n          <'
    'td style="width: 436px; height: 30px; " class="auto-style52">\n\t\t  017<span style='
    '"font-family: &quot;Times New Roman&quot;, serif; font-size: 16px; font-style: normal; '
    "font-variant-ligatures: normal; font-variant-caps: normal; letter-spacing: normal; orpha"
    "ns: 2; text-align: start; text-indent: 0px; text-transform: none; white-space: normal; w"
    "idows: 2; word-spacing: 0px; -webkit-text-stroke-width: 0px; background-color: rgb(255, "
    '255, 255); text-decoration-style: initial; text-decoration-color: initial;" class="aut'
    'o-style89"><span class="auto-style78" style="font-size: 10pt;">\n\t\t  km S 82° W o'
    "f Balut Island (Municipality Of Sarangani) (Davao Occidental)</span></span></td>\n\t\t  "
    '</tr>\n<tr>\n\t\t\t\t\t<td style="width: 185px; height: 30px; " class="auto-style91"'
    '>\n\t\t\t\t\t\t<span class="auto-style7"></span><span class="auto-style70">\n\t\t\t'
    '\t\t\t\t<a href="2026_Earthquake_Information\\August\\2026_0818_1350_B2html">\n\t\t\t'
    '\t\t\t\t\t<span class="auto-style99">18 August 2026 - 09:50 PM</span></a></span>\n\t\t'
    '\t\t\t</td>\n\t\t\t\t\t<td style="width: 77px; height: 30px; " class="auto-style90" '
    'align="center">\n\t\t\t\t\t\t06.36</td>\n\t\t\t\t\t<td style="width: 92px; height: 30'
    "px; border-left-style: none; border-right: 1pt solid mistyrose; border-top: 1pt solid mi"
    'styrose; border-bottom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\t\t'
    '\t\t\t\tclass="auto-style56" align="center">124.69</td>\n\t\t\t\t\t<td style="width'
    ": 62px; height: 30px; border-left-style: none; border-right: 1pt solid mistyrose; border"
    "-top: 1pt solid mistyrose; border-bottom: 1pt solid mistyrose; padding: 0.75pt; backgrou"
    'nd: white"\n\t\t\t\t\t\tclass="auto-style56" align="center">017</td>\n\t\t\t\t\t<td'
    ' style="width: 52px; height: 30px; border-left-style: none; border-right: 1pt solid mis'
    "tyrose; border-top: 1pt solid mistyrose; border-bottom: 1pt solid mistyrose; padding: 0."
    '75pt; background: white"\n\t\t\t\t\t\tclass="auto-style56" align="center">2.6</td>'
    '\n\t\t\t\t\t<td style="width: 436px; height: 30px; " class="auto-style52">\n\t\t\t\t'
    '\t\t006<span\n\t\t\t\t\t\t\tstyle="font-family: &quot;Times New Roman&quot;, serif; fon'
    "t-size: 16px; font-style: normal; font-variant-ligatures: normal; font-variant-caps: nor"
    "mal; letter-spacing: normal; orphans: 2; text-align: start; text-indent: 0px; text-trans"
    "form: none; white-space: normal; widows: 2; word-spacing: 0px; -webkit-text-stroke-width"
    ": 0px; background-color: rgb(255, 255, 255); text-decoration-style: initial; text-decora"
    'tion-color: initial;"\n\t\t\t\t\t\t\tclass="auto-style89"><span class="auto-style78'
    '" style="font-size: 10pt;">\n\t\t\t\t\t\t\t\tkm S 71° W of Surallah (South Cotabato)<'
    '/span></span></td>\n\t\t\t\t</tr>\n<tr>\n      <td style="width: 185px; height: 30px; '
    '" class="auto-style91">\n      <span class="auto-style7"></span><span class="auto-'
    'style70">\n\t\t\t<a href="2026_Earthquake_Information\\August\\2026_0822_231249_B1F.ht'
    'ml">\n\t\t\t<span class="auto-style99">23 August 2026 - 07:12 AM</span></a></span></t'
    'd>\n          <td style="width: 77px; height: 30px; " class="auto-style90" align="c'
    'enter">\n\t\t  05.09</td>\n          <td style="width: 92px; height: 30px; border-left'
    "-style: none; border-right: 1pt solid mistyrose; border-top: 1pt solid mistyrose; border"
    '-bottom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\n            class='
    '"auto-style56" align="center">125.37</td>\n          <td style="width: 62px; height'
    ": 30px; border-left-style: none; border-right: 1pt solid mistyrose; border-top: 1pt soli"
    'd mistyrose; border-bottom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n'
    '\n            class="auto-style56" align="center">034</td>\n          <td style="wi'
    "dth: 52px; height: 30px; border-left-style: none; border-right: 1pt solid mistyrose; bor"
    "der-top: 1pt solid mistyrose; border-bottom: 1pt solid mistyrose; padding: 0.75pt; backg"
    'round: white"\n\n            class="auto-style56" align="center">1.9</td>\n        '
    '  <td style="width: 436px; height: 30px; " class="auto-style52">\n\t\t  037<span sty'
    'le="font-family: &quot;Times New Roman&quot;, serif; font-size: 16px; font-style: norma'
    "l; font-variant-ligatures: normal; font-variant-caps: normal; letter-spacing: normal; or"
    "phans: 2; text-align: start; text-indent: 0px; text-transform: none; white-space: normal"
    "; widows: 2; word-spacing: 0px; -webkit-text-stroke-width: 0px; background-color: rgb(25"
    '5, 255, 255); text-decoration-style: initial; text-decoration-color: initial;" class="'
    'auto-style89"><span class="auto-style78" style="font-size: 10pt;">\n\t\t  km S 09° '
    "W of Balut Island (Municipality Of Sarangani) (Davao Occidental)</span></span></td>\n\t"
    '\t  </tr>\n<tr>\n      <td style="width: 185px; height: 30px; " class="auto-style91"'
    '>\n      <span class="auto-style7"></span><span class="auto-style70">\n\t\t\t<a href'
    '="2026_Earthquake_Information\\August\\2026_0825_1252_B1.html">\n\t\t\t<span class="a'
    'uto-style99">25 August 2026 - 08:52 PM</span></a></span></td>\n          <td style="wi'
    'dth: 77px; height: 30px; " class="auto-style90" align="center">\n\t\t  02.02</td>\n'
    '          <td style="width: 92px; height: 30px; border-left-style: none; border-right: '
    "1pt solid mistyrose; border-top: 1pt solid mistyrose; border-bottom: 1pt solid mistyrose"
    '; padding: 0.75pt; background: white"\n\n            class="auto-style56" align="cen'
    'ter">126.67</td>\n          <td style="width: 62px; height: 30px; border-left-style: n'
    "one; border-right: 1pt solid mistyrose; border-top: 1pt solid mistyrose; border-bottom: "
    '1pt solid mistyrose; padding: 0.75pt; background: white"\n\n            class="auto-st'
    'yle56" align="center">009</td>\n          <td style="width: 52px; height: 30px; bord'
    "er-left-style: none; border-right: 1pt solid mistyrose; border-top: 1pt solid mistyrose;"
    ' border-bottom: 1pt solid mistyrose; padding: 0.75pt; background: white"\n\n           '
    ' class="auto-style56" align="center">3.1</td>\n          <td style="width: 436px; h'
    'eight: 30px; " class="auto-style52">\n\t\t  400<span style="font-family: &quot;Times'
    " New Roman&quot;, serif; font-size: 16px; font-style: normal; font-variant-ligatures: no"
    "rmal; font-variant-caps: normal; letter-spacing: normal; orphans: 2; text-align: start; "
    "text-indent: 0px; text-transform: none; white-space: normal; widows: 2; word-spacing: 0p"
    "x; -webkit-text-stroke-width: 0px; background-color: rgb(255, 255, 255); text-decoration"
    '-style: initial; text-decoration-color: initial;" class="auto-style89"><span class="'
    'auto-style78" style="font-size: 10pt;">\n\t\t  km S 20° E of Balut Island (Municipali'
    "ty Of Sarangani) (Davao Occidental)</span></span></td>\n\t\t  </tr>\n"
)

# The page is a whole month and the source keeps a recent window, so "now" is
# pinned to the day the fixture was captured. A wall-clock default would make
# this file start failing a week after it was written.
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def parse(window_days: float = 400.0):
    return PhivolcsSource(window_days=window_days).parse_payload(PHIVOLCS_ROWS, now=NOW)


def by_id():
    return {e.source_id: e for e in parse()}


def test_the_displayed_time_is_philippine_time_and_the_stored_one_is_utc():
    """The table says `26 August 2026 - 07:14 PM` and says "Philippine Time"
    only in a column header. Stored as 11:14 UTC, which is exactly UTC+8."""
    event = by_id()["2026_0826_1114_B1"]
    assert event.time == datetime(2026, 8, 26, 11, 14, tzinfo=UTC)
    assert event.raw["local_time"] == "26 August 2026 - 07:14 PM"


def test_the_offset_is_proven_on_the_case_that_would_expose_it():
    """A row displayed on 23 August that is really 22 August in UTC. An
    offset applied in the wrong direction, or not at all, lands on the wrong
    DAY here rather than merely the wrong hour."""
    event = by_id()["2026_0822_231249_B1F"]
    assert event.time == datetime(2026, 8, 22, 23, 12, 49, tzinfo=UTC)
    assert event.raw["local_time"].startswith("23 August 2026")


def test_the_bulletin_filename_beats_the_visible_timestamp():
    """It is UTC where the table is local, and it carries SECONDS where the
    table carries only minutes."""
    assert parse_bulletin_time("2026_0825_144504_B1F.html") == datetime(
        2026, 8, 25, 14, 45, 4, tzinfo=UTC
    )
    # the same page also serves the minute-only shape
    assert parse_bulletin_time("2026_0825_1445_B1.html") == datetime(
        2026, 8, 25, 14, 45, tzinfo=UTC
    )
    assert parse_bulletin_time("not-a-bulletin.html") is None


def test_two_earthquakes_in_one_minute_stay_two_earthquakes():
    """The trap that decides the identifier. 43 of the 1881 rows share a
    date-and-minute with another row, so a key built from the visible
    timestamp would silently merge distinct events into one."""
    events = by_id()
    first, second = events["2026_0825_1445_B1"], events["2026_0825_144504_B1F"]
    assert first.raw["local_time"] == second.raw["local_time"] == "25 August 2026 - 10:45 PM"
    assert first.id != second.id
    # and they really are different earthquakes, 1300 km apart
    assert (first.lat, first.lon) == (17.41, 122.59)
    assert (second.lat, second.lon) == (5.39, 125.27)
    assert first.magnitude != second.magnitude


def test_the_same_bulletin_listed_twice_is_one_event():
    """Five rows in 1881 link to a bulletin another row also links to. Keying
    on the filename collapses them, which is what should happen: that is one
    bulletin printed twice, not two earthquakes."""
    doubled = PHIVOLCS_ROWS + PHIVOLCS_ROWS
    events = PhivolcsSource(window_days=400.0).parse_payload(doubled, now=NOW)
    assert len(events) == 6
    assert len({e.id for e in events}) == 6


def test_a_malformed_href_does_not_cost_the_row():
    """`2026_0818_1350_B2html`, with the dot before the extension missing.
    One bad row in 1881 must not lose the event, nor the other 1880."""
    assert bulletin_key("2026_Earthquake_Information\\August\\2026_0818_1350_B2html") == (
        "2026_0818_1350_B2html"
    )
    event = by_id()["2026_0818_1350_B2"]
    assert event.time == datetime(2026, 8, 18, 13, 50, tzinfo=UTC)
    assert event.magnitude == 2.6


def test_windows_backslashes_in_hrefs_are_read():
    """The front page and the monthly archive both write Windows paths, the
    archive with a `\\..\\..\\` prefix on top."""
    assert bulletin_key("2026_Earthquake_Information\\August\\2026_0826_1114_B1.html") == (
        "2026_0826_1114_B1.html"
    )
    assert bulletin_key("\\..\\..\\2026_Earthquake_Information\\July\\2026_0731_1540_B1.html") == (
        "2026_0731_1540_B1.html"
    )
    assert bulletin_key("") is None


def test_the_local_time_fallback_reads_a_twelve_hour_clock():
    """Used only when the filename is unreadable. AM/PM matters: 07:14 PM is
    19:14, and reading it as 07:14 would place the event twelve hours early."""
    assert parse_local_time("26 August 2026 - 07:14 PM") == datetime(
        2026, 8, 26, 11, 14, tzinfo=UTC
    )
    assert parse_local_time("26 August 2026 - 07:14 AM") == datetime(
        2026, 8, 25, 23, 14, tzinfo=UTC
    )
    assert parse_local_time("not a date") is None


def test_depth_arrives_zero_padded_and_magnitude_as_text():
    event = by_id()["2026_0826_1114_B1"]
    assert event.depth_km == 30.0
    assert event.magnitude == 3.4
    assert (event.lat, event.lon) == (21.57, 121.75)
    assert event.kind is Kind.EARTHQUAKE


def test_the_scale_is_not_invented_when_the_table_omits_it():
    """The bulletins say Ms; the table names no scale, so neither do we."""
    for event in parse():
        assert event.mag_type is None


def test_the_place_text_is_unmangled():
    """The location cell arrives with tabs and newlines mid-sentence."""
    event = by_id()["2026_0826_1114_B1"]
    assert event.place == "087 km N 07° W of Itbayat (Batanes)"
    assert "\n" not in event.place and "\t" not in event.place


def test_the_country_comes_from_the_source_not_from_a_bounding_box():
    """PHIVOLCS states a distance from a named Philippine locality, and that
    is what we read. Median 21 km, p90 67 km over 1881 rows."""
    events = by_id()
    assert events["2026_0826_1114_B1"].country == "Philippines"
    assert events["2026_0818_1350_B2"].country == "Philippines"


def test_an_event_400_km_offshore_is_not_claimed_for_the_philippines():
    """Lesson 12. This one sits at 2.02 N, 126.67 E -- Indonesian water. The
    source saying "400 km from Balut Island" is not the source saying
    "in the Philippines", and reading it that way would fly the wrong flag."""
    far = by_id()["2026_0825_1252_B1"]
    assert far.country is None
    assert far.lat == 2.02
    assert far.place.startswith("400 km")


def test_the_window_trims_the_month_long_table():
    """1881 events by the 26th. Emitting the month every cycle would be 1881
    round-trips to re-say what the store already knows (lesson 6)."""
    recent = parse(window_days=2.0)
    assert [e.source_id for e in recent] == [
        "2026_0826_1114_B1",
        "2026_0825_144504_B1F",
        "2026_0825_1445_B1",
        "2026_0825_1252_B1",
    ]


def test_events_come_out_newest_first():
    """Arrival order is an implementation detail of polling; what the user
    reads is a chronology (lesson 5)."""
    events = parse()
    assert [e.time for e in events] == sorted((e.time for e in events), reverse=True)


def test_a_quake_is_a_point_in_time_never_an_ongoing_alert():
    """`ongoing` would put these in reach of the silence sweep, and the table
    drops an event as soon as its month rolls over."""
    for event in parse():
        assert event.ongoing is False
        assert event.expires is None


def test_an_out_of_range_coordinate_costs_its_position_not_the_batch():
    """Lesson 15, and the model RAISES on an out-of-range coordinate: one bad
    row unguarded would take the whole batch down with it."""
    broken = PHIVOLCS_ROWS.replace(">\n\t\t  21.57</td>", ">\n\t\t  3237.5</td>")
    events = PhivolcsSource(window_days=400.0).parse_payload(broken, now=NOW)
    assert len(events) == 6
    bad = next(e for e in events if e.source_id == "2026_0826_1114_B1")
    assert bad.lat is None
    assert bad.lon == 121.75
    assert bad.magnitude == 3.4


def test_the_month_boundary_is_covered_by_the_archive():
    """The front page lists the current PHILIPPINE month, so on 1 September
    the last days of August live only in the archive."""
    mid_month = pages_to_poll(datetime(2026, 8, 26, 12, 0, tzinfo=UTC))
    assert mid_month == ["https://earthquake.phivolcs.dost.gov.ph/"]

    # 1 September 04:00 UTC is already 1 September in Manila
    turn = pages_to_poll(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    assert len(turn) == 2
    assert turn[1].endswith("/EQLatest-Monthly/2026/2026_August.html")

    # a January rollover has to cross the year too
    january = pages_to_poll(datetime(2027, 1, 2, 4, 0, tzinfo=UTC))
    assert january[1].endswith("/EQLatest-Monthly/2026/2026_December.html")

    # and the archive stops being fetched once the month is running
    assert pages_to_poll(datetime(2026, 9, 8, 4, 0, tzinfo=UTC)) == mid_month


def test_the_boundary_is_decided_in_manila_not_in_utc():
    """31 August 20:00 UTC is already 1 September in Manila, and the front
    page has rolled over by then."""
    already_september = pages_to_poll(datetime(2026, 8, 31, 20, 0, tzinfo=UTC))
    assert len(already_september) == 2
    assert already_september[1].endswith("2026_August.html")


def test_tls_verification_stays_on():
    """The server sends its leaf and omits the GlobalSign intermediate, so
    certifi alone cannot build the chain and httpx fails where curl on macOS
    succeeds. The answer is the missing intermediate, never `verify=False`."""
    import ssl

    import certifi

    context = build_ssl_context()
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True

    # exactly one certificate more than certifi ships, and it is the one the
    # server should have sent itself
    plain = ssl.create_default_context(cafile=certifi.where())
    added = [c for c in context.get_ca_certs() if c not in plain.get_ca_certs()]
    assert len(added) == 1
    names = [value for rdn in added[0]["subject"] for _, value in rdn]
    assert "GlobalSign RSA OV SSL CA 2018" in names
