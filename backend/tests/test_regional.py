"""Regional agency tests, on real payloads captured on 2026-08-17."""

from __future__ import annotations

from datetime import UTC

from app.models.event import Kind, Severity
from app.sources.regional import (
    BmkgSource,
    GeonetSource,
    IngvSource,
    JmaSource,
    parse_iso6709,
)

# ---------------------------------------------------------------------------- JMA

JMA_ROWS = [
    {
        "ctt": "20260817231159",
        "eid": "20260817230907",
        "rdt": "2026-08-17T23:11:00+09:00",
        "ttl": "震源・震度情報",
        "at": "2026-08-17T23:09:00+09:00",
        "anm": "熊本県熊本地方",
        "en_anm": "Kumamoto Region, Kumamoto Prefecture",
        "cod": "+32.5+130.6-10000/",
        "mag": "3.2",
        "maxi": "2",
    },
    {
        # intensity alert issued before location: no epicentre, no magnitude
        "eid": "20260817230000",
        "ttl": "震度速報",
        "at": "2026-08-17T23:00:00+09:00",
        "maxi": "4",
    },
]


def test_iso6709_depth_is_metres_and_negative():
    lat, lon, depth = parse_iso6709("+32.5+130.6-10000/")
    assert (lat, lon) == (32.5, 130.6)
    assert depth == 10.0  # 10000 m below sea level -> 10 km

    lat, lon, depth = parse_iso6709("+32.5+130.6+0/")
    assert depth == 0.0
    assert parse_iso6709(None) == (None, None, None)
    assert parse_iso6709("anything at all") == (None, None, None)


def test_jma_parses_and_converts_to_utc():
    events = JmaSource().parse_payload(JMA_ROWS)
    assert len(events) == 1  # the 震度速報 is discarded, it has no epicentre

    event = events[0]
    assert event.id == "jma:20260817230907"
    assert event.kind is Kind.EARTHQUAKE
    assert event.magnitude == 3.2
    assert event.depth_km == 10.0
    # 23:09 Japan time (+09:00) = 14:09 UTC
    assert event.time.astimezone(UTC).hour == 14
    assert event.place == "Kumamoto Region, Kumamoto Prefecture"
    assert event.alert == "shindo 2"


def test_a_strong_shindo_outranks_a_modest_magnitude():
    """An M4.5 felt at shindo 6+ does damage: severity must follow the felt
    intensity, not only the released energy."""
    row = {**JMA_ROWS[0], "eid": "x1", "mag": "4.5", "maxi": "6+"}
    event = JmaSource().parse_payload([row])[0]
    assert event.severity is Severity.EXTREME  # severity_from_magnitude(4.5) = moderate


def test_a_weak_shindo_does_not_lower_a_big_magnitude():
    row = {**JMA_ROWS[0], "eid": "x2", "mag": "7.4", "maxi": "1"}
    event = JmaSource().parse_payload([row])[0]
    assert event.severity is Severity.EXTREME


# --------------------------------------------------------------------------- BMKG

BMKG_PAYLOAD = {
    "Infogempa": {
        "gempa": [
            {
                "Tanggal": "17 Agu 2026",
                "Jam": "17:42:42 WIB",
                "DateTime": "2026-08-17T10:42:42+00:00",
                "Coordinates": "-7.85,120.47",
                "Magnitude": "5.0",
                "Kedalaman": "10 km",
                "Wilayah": "84 km TimurLaut RUTENG-MANGGARAI-NTT",
                "Potensi": "Tidak berpotensi tsunami",
            }
        ]
    }
}


def test_bmkg_no_tsunami_potential():
    event = BmkgSource().parse_payload(BMKG_PAYLOAD)[0]
    assert event.magnitude == 5.0
    assert event.lat == -7.85 and event.lon == 120.47
    assert event.depth_km == 10.0
    assert event.tsunami is False
    assert event.severity is Severity.MODERATE


def test_bmkg_tsunami_potential_is_an_alert():
    """This is BMKG's only value over EMSC: the official Indonesian tsunami
    flag, published before the PTWC bulletins."""
    payload = {
        "Infogempa": {
            "gempa": [
                {
                    **BMKG_PAYLOAD["Infogempa"]["gempa"][0],
                    "DateTime": "2026-08-17T11:00:00+00:00",
                    "Magnitude": "7.1",
                    "Potensi": "Berpotensi tsunami di wilayah NTT",
                }
            ]
        }
    }
    event = BmkgSource().parse_payload(payload)[0]
    assert event.tsunami is True
    assert event.severity is Severity.EXTREME


def test_bmkg_empty_payload():
    assert BmkgSource().parse_payload({}) == []


# ------------------------------------------------------------------------- GeoNet

GEONET_PAYLOAD = {
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [175.646270752, -39.527153015]},
            "properties": {
                "publicID": "2026p617265",
                "time": "2026-08-17T09:19:19.434Z",
                "depth": 10.199224472045898,
                "magnitude": 3.442854385179525,
                "mmi": 4,
                "locality": "20 km north-west of Taihape",
                "quality": "best",
            },
        }
    ]
}


def test_geonet_depth_is_a_property_not_a_coordinate():
    event = GeonetSource().parse_payload(GEONET_PAYLOAD)[0]
    assert event.id == "geonet:2026p617265"
    assert event.lat == -39.527153015 and event.lon == 175.646270752
    # GeoNet geometry has only 2 components: reading coords[2] would give None
    assert event.depth_km is not None and round(event.depth_km, 1) == 10.2
    assert event.magnitude == 3.4
    assert event.alert == "MMI 4"


# --------------------------------------------------------------------------- INGV

INGV_PAYLOAD = {
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [13.1832, 42.8835, 8.4]},
            "properties": {
                "eventId": 46919052,
                "time": "2026-08-17T16:26:45.570000",
                "author": "SURVEY-INGV",
                "magType": "ML",
                "mag": 1.9,
                "place": "3 km NW Cagnano Amiterno (AQ)",
            },
        }
    ]
}


def test_ingv_naive_timestamp_is_treated_as_utc():
    event = IngvSource().parse_payload(INGV_PAYLOAD)[0]
    assert event.id == "ingv:46919052"
    assert event.magnitude == 1.9
    assert event.depth_km == 8.4
    # INGV publishes in UTC but without a timezone suffix: untreated, the
    # datetime would be naive and the age computations would blow up
    assert event.time.tzinfo is not None
    assert event.time.hour == 16
    assert event.mag_type == "ML"


def test_regional_sources_ignore_malformed_rows():
    """A broken entry must never interrupt the rest of the batch."""
    assert JmaSource().parse_payload([{"eid": "x", "cod": "broken", "at": "not a date"}]) == []
    assert GeonetSource().parse_payload({"features": [{"properties": {}}]}) == []
    assert IngvSource().parse_payload({"features": [{"properties": {"eventId": 1}}]}) == []
