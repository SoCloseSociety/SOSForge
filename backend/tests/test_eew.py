"""Alerte precoce japonaise et couverture chinoise (relais Wolfx).

Fixtures: extraits verbatim des flux captures le 2026-08-17.
"""

from __future__ import annotations

from app.models.event import Kind, Severity
from app.sources.eew import CencSource, JmaEewSource

EEW = {
    "Title": "緊急地震速報（予報）",
    "Issue": {"Source": "東京", "Status": "通常"},
    "EventID": "20260817223317",
    "Serial": 4,
    "AnnouncedTime": "2026/08/17 22:34:00",
    "OriginTime": "2026/08/17 22:33:12",
    "Hypocenter": "日向灘",
    "Latitude": 32.0,
    "Longitude": 131.9,
    "Magunitude": 3.6,
    "Depth": 20,
    "MaxIntensity": "2",
    "isFinal": False,
}

CENC = {
    "No1": {
        "type": "reviewed",
        "EventID": "CD.20260817142722.7",
        "time": "2026-08-17 14:15:53",
        "ReportTime": "2026-08-17 14:27:34",
        "location": "甘肃陇南市礼县",
        "placeName": "甘肃陇南市礼县",
        "magnitude": "2.5",
        "depth": "10",
        "latitude": "33.92",
        "longitude": "104.95",
        "intensity": "4",
    }
}


def test_jma_time_is_tokyo_time_without_offset():
    """Le piege principal de ce module: la JMA horodate en heure du Japon SANS
    indiquer le decalage. Un parse naif daterait l'alerte de l'heure du serveur,
    soit neuf heures d'ecart sur une information qui se compte en secondes."""
    event = JmaEewSource().parse_payload(EEW)[0]
    assert event.time.hour == 13 and event.time.minute == 33  # 22:33 JST = 13:33 UTC
    assert event.time.tzinfo is not None


def test_jma_magnitude_key_is_misspelled_in_the_api():
    event = JmaEewSource().parse_payload(EEW)[0]
    assert event.magnitude == 3.6  # champ "Magunitude", pas "Magnitude"
    assert event.lat == 32.0 and event.depth_km == 20
    assert event.country_code == "JP"


def test_a_cancelled_early_warning_disappears():
    """Une detection precoce est souvent un faux positif, et la source l'annule.
    Une alerte annulee qui resterait affichee serait pire que pas d'alerte."""
    cancelled = {**EEW, "Issue": {"Source": "東京", "Status": "キャンセル"}}
    assert JmaEewSource().parse_payload(cancelled) == []


def test_expected_shaking_outranks_a_modest_magnitude():
    """Une alerte precoce sert a se mettre a l'abri: c'est l'intensite attendue
    au sol qui compte, pas l'estimation de magnitude des premieres secondes."""
    strong = {**EEW, "MaxIntensity": "6+"}
    assert JmaEewSource().parse_payload(strong)[0].severity is Severity.EXTREME

    # un bulletin d'ALERTE (警報) vaut au moins "fort", meme a magnitude basse
    warning = {**EEW, "Title": "緊急地震速報（警報）", "Magunitude": 4.0}
    assert JmaEewSource().parse_payload(warning)[0].severity is Severity.SEVERE


def test_jma_garbage_is_ignored():
    assert JmaEewSource().parse_payload({}) == []
    assert JmaEewSource().parse_payload([]) == []
    assert JmaEewSource().parse_payload({"EventID": "x", "OriginTime": "hier"}) == []


def test_cenc_payload_is_a_dict_not_a_list():
    """Indexe No1, No2... Iterer sur les cles rendrait les chaines "No1"."""
    events = CencSource().parse_payload(CENC)
    assert len(events) == 1

    event = events[0]
    assert event.kind is Kind.EARTHQUAKE
    assert event.magnitude == 2.5  # tous les nombres arrivent en chaine
    assert event.lat == 33.92
    assert event.country_code == "CN"
    # 14:15 heure de Pekin = 06:15 UTC
    assert event.time.hour == 6 and event.time.minute == 15


def test_cenc_garbage_is_ignored():
    assert CencSource().parse_payload({}) == []
    assert CencSource().parse_payload({"No1": "pas un objet"}) == []
    assert CencSource().parse_payload({"No1": {"EventID": "x", "time": "jamais"}}) == []
