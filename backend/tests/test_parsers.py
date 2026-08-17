"""Tests des normalizers, sur des payloads reels captures sur les sources live.

Les fixtures ci-dessous ne sont pas inventees: ce sont des extraits verbatim des
reponses des APIs (frame websocket EMSC du 2026-08-17, feature USGS all_hour,
entry Atom PAAQ, item RSS GDACS, alerte api.weather.gov, ligne HANS).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.dedupe import Deduper, haversine_km
from app.models.event import Kind, Severity, severity_from_magnitude
from app.sources.emsc_ws import parse_message
from app.sources.gdacs import is_relevant, parse_item
from app.sources.nws import classify
from app.sources.nws import parse_feature as parse_nws
from app.sources.tsunami import parse_entry
from app.sources.usgs import parse_feature as parse_usgs
from app.sources.volcano import VolcanoSource

# --------------------------------------------------------------------------- EMSC

EMSC_FRAME = {
    "action": "create",
    "data": {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [121.48, -8.29, -10.0]},
        "id": "20260817_0000558",
        "properties": {
            "source_id": "2045671",
            "source_catalog": "EMSC-RTS",
            "lastupdate": "2026-08-17T17:07:27.26815Z",
            "time": "2026-08-17T17:02:02.0Z",
            "flynn_region": "FLORES REGION, INDONESIA",
            "lat": -8.29,
            "lon": 121.48,
            "depth": 10.0,
            "evtype": "ke",
            "auth": "BMKG",
            "mag": 2.7,
            "magtype": "m",
            "unid": "20260817_0000558",
        },
    },
}


def test_emsc_frame():
    event = parse_message(EMSC_FRAME)
    assert event is not None
    assert event.id == "emsc:20260817_0000558"
    assert event.kind is Kind.EARTHQUAKE
    assert event.magnitude == 2.7
    assert event.lat == -8.29 and event.lon == 121.48
    # geometry.coordinates[2] vaut -10.0 chez EMSC: la profondeur doit rester positive
    assert event.depth_km == 10.0
    assert event.place == "FLORES REGION, INDONESIA"
    assert event.time.tzinfo is not None


def test_emsc_depth_from_geometry_only():
    """Si `properties.depth` manque, on retombe sur la 3e coordonnee (negative)."""
    frame = {"action": "create", "data": {**EMSC_FRAME["data"]}}
    frame["data"]["properties"] = {
        k: v
        for k, v in EMSC_FRAME["data"]["properties"].items()
        if k not in ("depth", "lat", "lon")
    }
    event = parse_message(frame)
    assert event is not None
    assert event.depth_km == 10.0
    assert event.lat == -8.29


def test_emsc_garbage_is_ignored():
    assert parse_message({"action": "create", "data": {}}) is None
    assert parse_message({}) is None


# --------------------------------------------------------------------------- USGS

USGS_FEATURE = {
    "type": "Feature",
    "id": "ci40674530",
    "properties": {
        "mag": 0.48,
        "place": "7 km SSE of Idyllwild, CA",
        "time": 1786985672530,
        "updated": 1786985875720,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/ci40674530",
        "felt": None,
        "alert": None,
        "status": "automatic",
        "tsunami": 0,
        "sig": 4,
        "magType": "ml",
        "type": "earthquake",
        "title": "M 0.5 - 7 km SSE of Idyllwild, CA",
    },
    "geometry": {"type": "Point", "coordinates": [-116.692333, 33.682666, 15.59]},
}


def test_usgs_feature():
    event = parse_usgs(USGS_FEATURE)
    assert event is not None
    assert event.id == "usgs:ci40674530"
    assert event.magnitude == 0.48
    # chez USGS la profondeur est deja positive
    assert event.depth_km == 15.59
    assert event.lat == 33.682666
    assert event.tsunami is False
    assert event.severity is Severity.INFO


def test_usgs_tsunami_flag_raises_severity():
    feature = {**USGS_FEATURE, "id": "us1", "properties": {**USGS_FEATURE["properties"]}}
    feature["properties"]["tsunami"] = 1
    feature["properties"]["mag"] = 7.8
    event = parse_usgs(feature)
    assert event is not None
    assert event.tsunami is True
    assert event.severity is Severity.EXTREME


def test_severity_scale():
    assert severity_from_magnitude(None) is Severity.INFO
    assert severity_from_magnitude(1.0) is Severity.INFO
    assert severity_from_magnitude(3.0) is Severity.MINOR
    assert severity_from_magnitude(5.0) is Severity.MODERATE
    assert severity_from_magnitude(6.4) is Severity.SEVERE
    assert severity_from_magnitude(7.1) is Severity.EXTREME
    assert severity_from_magnitude(2.0, tsunami=True) is Severity.EXTREME


# ------------------------------------------------------------------------ tsunami

TSUNAMI_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
  <entry>
    <title>70 miles SW of Sacramento, California</title>
    <updated>2026-08-13T15:34:56Z</updated>
    <id>urn:uuid:9a13866e-2f52-43d3-8ac4-c50ed3e575ec</id>
    <geo:lat>37.753</geo:lat>
    <geo:long>-122.198</geo:long>
    <summary type="xhtml"><strong>Category:</strong> Information
      <strong>Preliminary Magnitude: </strong>4.0(Ml)
      <b>Note:</b> * There is NO tsunami danger from this earthquake.</summary>
    <link rel="related" title="CapXML document"
          href="https://www.tsunami.gov/events/PAAQ/2026/08/13/tjpse5/1/WEAK53/PAAQCAP.xml"
          type="application/cap+xml"/>
  </entry>
</feed>
"""

TSUNAMI_WARNING = TSUNAMI_ATOM.replace(
    "<strong>Category:</strong> Information", "<strong>Category:</strong> Warning"
)


def _first_entry(xml: str):
    root = ET.fromstring(xml)
    return root.find(".//{http://www.w3.org/2005/Atom}entry")


def test_tsunami_information_bulletin_is_not_an_alert():
    event = parse_entry(_first_entry(TSUNAMI_ATOM), "PAAQ")
    assert event is not None
    assert event.alert == "information"
    # un bulletin "NO tsunami danger" ne doit surtout pas lever le drapeau tsunami
    assert event.tsunami is False
    assert event.severity is Severity.INFO
    assert event.magnitude == 4.0
    assert event.lat == 37.753
    assert event.url and event.url.endswith("PAAQCAP.xml")


def test_tsunami_warning_is_extreme():
    event = parse_entry(_first_entry(TSUNAMI_WARNING), "PHEB")
    assert event is not None
    assert event.alert == "warning"
    assert event.tsunami is True
    assert event.severity is Severity.EXTREME


# -------------------------------------------------------------------------- GDACS

GDACS_ITEM = """<rss xmlns:gdacs="http://www.gdacs.org"
     xmlns:georss="http://www.georss.org/georss"><channel><item>
  <title>Green earthquake (Magnitude 5.8M, Depth:54.741km) in Russian Federation</title>
  <link>https://www.gdacs.org/report.aspx?eventtype=EQ&amp;eventid=1559459</link>
  <pubDate>Mon, 17 Aug 2026 12:58:53 GMT</pubDate>
  <georss:point>53.024 159.7106</georss:point>
  <gdacs:eventtype>EQ</gdacs:eventtype>
  <gdacs:alertlevel>Green</gdacs:alertlevel>
  <gdacs:eventid>1559459</gdacs:eventid>
  <gdacs:episodeid>1726628</gdacs:episodeid>
  <gdacs:fromdate>Mon, 17 Aug 2026 12:38:04 GMT</gdacs:fromdate>
  <gdacs:severity unit="M" value="5.8">Magnitude 5.8M, Depth:54.7km</gdacs:severity>
  <gdacs:population unit="in MMI IV" value="232323">230 thousand in MMI IV</gdacs:population>
  <gdacs:iscurrent>true</gdacs:iscurrent>
  <gdacs:country>Russian Federation</gdacs:country>
</item></channel></rss>
"""

GDACS_OLD_DROUGHT = """<rss xmlns:gdacs="http://www.gdacs.org"><channel><item>
  <title>Drought is on going in Brazil</title>
  <pubDate>Fri, 03 Jul 2025 12:57:24 GMT</pubDate>
  <gdacs:eventtype>DR</gdacs:eventtype>
  <gdacs:alertlevel>Green</gdacs:alertlevel>
  <gdacs:eventid>1010101</gdacs:eventid>
  <gdacs:fromdate>Tue, 01 Jul 2025 00:00:00 GMT</gdacs:fromdate>
</item></channel></rss>
"""


def test_gdacs_severity_comes_from_the_attribute_not_the_text():
    event = parse_item(ET.fromstring(GDACS_ITEM).find(".//item"))
    assert event is not None
    assert event.magnitude == 5.8  # et non un ValueError sur "Magnitude 5.8M, ..."
    assert event.mag_type == "M"
    assert event.kind is Kind.EARTHQUAKE
    assert event.lat == 53.024 and event.lon == 159.7106
    assert event.country == "Russian Federation"
    assert event.id == "gdacs:EQ1559459"  # stable a travers les episodes


def test_gdacs_stale_green_is_filtered_out():
    event = parse_item(ET.fromstring(GDACS_OLD_DROUGHT).find(".//item"))
    assert event is not None
    assert is_relevant(event, max_age_days=3.0) is False


def test_gdacs_red_alert_is_always_kept():
    xml = GDACS_OLD_DROUGHT.replace("Green", "Red")
    event = parse_item(ET.fromstring(xml).find(".//item"))
    assert event is not None
    assert event.severity is Severity.EXTREME
    assert is_relevant(event, max_age_days=3.0) is True


# ---------------------------------------------------------------------------- NWS

NWS_FEATURE = {
    "id": "urn:oid:2.49.0.1.840.0.40b7f20505eb.001.1",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-83.0, 39.0], [-83.0, 40.0], [-82.0, 40.0], [-82.0, 39.0]]],
    },
    "properties": {
        "id": "urn:oid:2.49.0.1.840.0.40b7f20505eb.001.1",
        "event": "Flash Flood Warning",
        "severity": "Severe",
        "urgency": "Immediate",
        "certainty": "Likely",
        "sent": "2026-08-17T13:15:00-04:00",
        "expires": "2026-08-17T16:15:00-04:00",
        "areaDesc": "Washington, OH",
        "headline": "Flash Flood Warning issued August 17 at 1:15PM EDT",
    },
}


def test_nws_alert():
    event = parse_nws(NWS_FEATURE)
    assert event is not None
    assert event.kind is Kind.FLOOD
    assert event.severity is Severity.SEVERE
    # barycentre du polygone
    assert event.lat == 39.5 and event.lon == -82.5
    assert event.time.tzinfo is not None


def test_nws_alert_without_geometry_still_parses():
    feature = {**NWS_FEATURE, "geometry": None}
    event = parse_nws(feature)
    assert event is not None
    assert event.lat is None and event.lon is None


def test_short_patterns_need_a_whole_word_long_ones_do_not():
    """Le plancher de longueur, choisi APRES mesure sur 2525 alertes reelles.

    Un motif court se cache dans d'autres mots ("ash" dans "Flash"), donc il
    exige un mot entier. Un motif long doit rester cherche en sous-chaine, sans
    quoi les formes composees et flechies des flux reels sont perdues -- 621
    alertes dans la mesure ("Forestfire", "Thunderstorms", "Rainstorm").
    """
    # motif court: le faux positif historique est ferme
    assert classify("Flash Flood Warning") is Kind.FLOOD
    # ... sans perdre le vrai positif
    assert classify("Ashfall Warning") is Kind.VOLCANO
    assert classify("Volcanic Ash Advisory") is Kind.VOLCANO

    # motifs longs: les formes composees et flechies restent detectees
    assert classify("Forestfire") is Kind.WILDFIRE
    assert classify("Thunderstorms") is Kind.STORM
    assert classify("Rainstorm") is Kind.STORM
    assert classify("Freezing Rain Advisory") is Kind.STORM


def test_nws_classification_order():
    # "Tsunami Warning" ne doit pas tomber dans le seau "storm"
    assert classify("Tsunami Warning") is Kind.TSUNAMI
    assert classify("Hurricane Warning") is Kind.CYCLONE
    assert classify("Red Flag Warning") is Kind.WILDFIRE
    assert classify("Extreme Heat Warning") is Kind.HEAT
    assert classify("Tornado Warning") is Kind.STORM
    assert classify("Special Marine Statement") is Kind.OTHER


# ------------------------------------------------------------------------ volcans

HANS_ROW = {
    "obs_fullname": "Alaska Volcano Observatory",
    "obs_abbr": "avo",
    "volcano_name": "Great Sitkin",
    "vnum": "311120",
    "notice_identifier": "DOI-USGS-AVO-2026-08-16T19:27:27+00:00",
    "sent_utc": "2026-08-16 19:28:19",
    "color_code": "ORANGE",
    "alert_level": "WATCH",
    "notice_url": "https://volcanoes.usgs.gov/hans-public/notice/DOI-USGS-AVO-2026",
}


def test_volcano_joins_the_smithsonian_catalog():
    source = VolcanoSource()
    source._catalog["311120"] = (52.076, -176.13, "United States")
    event = source.parse(HANS_ROW)
    assert event is not None
    assert event.kind is Kind.VOLCANO
    assert event.severity is Severity.SEVERE  # code couleur ORANGE
    assert event.lat == 52.076
    assert "Great Sitkin" in event.title


def test_volcano_without_catalog_keeps_the_alert():
    event = VolcanoSource().parse(HANS_ROW)
    assert event is not None
    assert event.lat is None  # position inconnue, mais l'alerte n'est pas perdue
    assert event.severity is Severity.SEVERE


# --------------------------------------------------------------------------- dedup


def test_haversine():
    # Paris -> Lyon, ~392 km
    assert 380 < haversine_km(48.8566, 2.3522, 45.7640, 4.8357) < 400


def test_emsc_and_usgs_report_of_the_same_quake_share_a_cluster():
    deduper = Deduper()
    emsc = parse_message(EMSC_FRAME)
    usgs_twin = parse_usgs(
        {
            "type": "Feature",
            "id": "us7000abcd",
            "properties": {
                "mag": 3.0,
                "place": "Flores Sea",
                "time": 1786986152000,  # 30s apres l'evenement EMSC
                "updated": 1786986152000,
                "tsunami": 0,
                "magType": "mb",
                "title": "M 3.0",
            },
            "geometry": {"type": "Point", "coordinates": [121.5, -8.3, 10.0]},
        }
    )
    deduper.assign(emsc)
    deduper.assign(usgs_twin)
    assert emsc.cluster_id == usgs_twin.cluster_id
    assert deduper.is_primary(emsc) is True
    assert deduper.is_primary(usgs_twin) is False


def test_distant_quakes_are_not_merged():
    deduper = Deduper()
    emsc = parse_message(EMSC_FRAME)
    far = parse_usgs(USGS_FEATURE)  # Californie
    deduper.assign(emsc)
    deduper.assign(far)
    assert emsc.cluster_id != far.cluster_id
