"""Non-US alerts: Meteoalarm (Europe) and the WMO CAP aggregate.

Fixtures: verbatim excerpts from the feeds captured on 2026-08-17.
"""

from __future__ import annotations

from copy import deepcopy

from app.models.event import Kind, Severity
from app.sources.alerts_world import (
    WmoCapSource,
    classify_wmo,
    parse_meteoalarm,
    parse_wmo,
    wmo_cap_path,
)
from app.sources.cap_area import WMO_CAP_BASE

# ------------------------------------------------------------------ Meteoalarm

# an active thunderstorm warning, with its two `info` blocks (French then English)
ALERT = {
    "alert": {
        "identifier": "2.49.0.0.250.0.FR.20260812160107.974023",
        "info": [
            {
                "language": "fr-FR",
                "event": "Vigilance jaune orages",
                "headline": "Vigilance jaune orages",
                "severity": "Minor",
                "responseType": ["Monitor"],
                "onset": "2026-08-12T16:01:00+02:00",
                "expires": "2026-08-13T06:00:00+02:00",
                "web": "https://vigilance.meteofrance.fr",
                "area": [
                    {"areaDesc": "Alpes-de-Haute-Provence", "geocode": [{"value": "FR821"}]},
                    {"areaDesc": "Hautes Alpes", "geocode": [{"value": "FR822"}]},
                    {"areaDesc": "Var", "geocode": [{"value": "FR825"}]},
                ],
                "parameter": [
                    {"value": "2; yellow; Moderate", "valueName": "awareness_level"},
                    {"value": "3; Thunderstorm", "valueName": "awareness_type"},
                ],
            },
            {
                "language": "en-GB",
                "event": "Yellow thunderstorm warning",
                "headline": "Yellow thunderstorm warning",
                "severity": "Minor",
                "responseType": ["Monitor"],
                "onset": "2026-08-12T16:01:00+02:00",
                "expires": "2026-08-13T06:00:00+02:00",
                "area": [{"areaDesc": "Alpes-de-Haute-Provence", "geocode": [{"value": "FR821"}]}],
                "parameter": [
                    {"value": "2; yellow; Moderate", "valueName": "awareness_level"},
                    {"value": "3; Thunderstorm", "valueName": "awareness_type"},
                ],
            },
        ],
    }
}

LIFTED = {
    "alert": {
        "identifier": "2.49.0.0.250.0.FR.20260812060108.090023",
        "info": [
            {
                "language": "en-GB",
                "event": "Yellow thunderstorm warning",
                "severity": "Minor",
                "responseType": ["AllClear"],
                "onset": "2026-08-12T06:01:00+02:00",
                "expires": "2026-08-12T06:00:00+02:00",
                "area": [{"areaDesc": "Hérault", "geocode": [{"value": "FR813"}]}],
                "parameter": [
                    {"value": "1; green; Minor", "valueName": "awareness_level"},
                    {"value": "3; Thunderstorm", "valueName": "awareness_type"},
                ],
            }
        ],
    }
}


# Verbatim: the archetype of the 461 warnings that made the whole feed one
# colour. `feeds-poland`, 2026-08-26. Orange, and what the forecaster actually
# writes underneath the colour is "BE PREPARED ... some flooding of properties
# ... possible ... some evacuations MAY be required".
ORANGE_FORECAST = {
    "alert": {
        "identifier": "2.49.0.0.616.0.PL.Gd20260821035709701.PL0201",
        "info": [
            {
                "area": [
                    {
                        "areaDesc": "Dolno\u015bl\u0105skie Province Boles\u0142awiecki County",
                        "geocode": [{"value": "PL0201", "valueName": "EMMA_ID"}],
                    }
                ],
                "category": ["Met"],
                "certainty": "Likely",
                "event": "Orange Rain warning",
                "expires": "2026-08-22T06:00:00+02:00",
                "headline": "Orange Heavy rain and thunderstorm warning for Poland - Dolnoslaskie Province Boleslawiecki County",  # noqa: E501
                "instruction": "BE PREPARED to protect yourself and your property. Some flooding of properties and transport networks are possible.",  # noqa: E501
                "language": "en-GB",
                "onset": "2026-08-21T17:00:00+02:00",
                "parameter": [
                    {"value": "3; orange; Severe", "valueName": "awareness_level"},
                    {"value": "10; Rain", "valueName": "awareness_type"},
                ],
                "responseType": ["None"],
                "senderName": "IMGW-PIB, Marine Meteorological Forecast Office in Gdynia",
                "severity": "Severe",
                "urgency": "Expected",
            }
        ],
    }
}

# Verbatim: the ONE warning out of the 2622 served by the ten country feeds on
# 2026-08-26 that the issuing agency says it is OBSERVING at the top rank --
# 180 mm already on the ground north of Tarragona.
RED_OBSERVED = {
    "alert": {
        "identifier": "2.49.0.0.724.0.ES.260822032408.694303PRP2220569048",
        "info": [
            {
                "area": [
                    {
                        "areaDesc": "Litoral norte de Tarragona",
                        "geocode": [{"value": "ES191", "valueName": "EMMA_ID"}],
                    }
                ],
                "category": ["Met"],
                "certainty": "Observed",
                "description": "Twelve-hours accumulated precipitation: 180 mm.",
                "effective": "2026-08-22T05:20:58+02:00",
                "event": "Extreme rain warning",
                "expires": "2026-08-22T06:59:59+02:00",
                "headline": "Extreme rain warning. Litoral norte de Tarragona",
                "instruction": "Take precautionary action, remain vigilant and act on advice given by authorities. Extreme or catastrophic damages to people and properties may occur.",  # noqa: E501
                "language": "en-GB",
                "onset": "2026-08-22T05:00:00+02:00",
                "parameter": [
                    {"value": "4; red; Extreme", "valueName": "awareness_level"},
                    {"value": "10; Rain", "valueName": "awareness_type"},
                ],
                "responseType": ["Monitor"],
                "senderName": "AEMET. State Meteorological Agency",
                "severity": "Extreme",
                "urgency": "Immediate",
            }
        ],
    }
}


def test_only_one_event_per_warning_despite_two_language_blocks():
    """Each warning carries its content twice (local language + English).
    Without an explicit choice, each one produced two events."""
    event = parse_meteoalarm(ALERT, "france")
    assert event is not None
    # the English block is preferred: the title must not be in French
    assert event.title == "Yellow thunderstorm warning"


def test_awareness_type_drives_the_kind_not_the_local_label():
    """`event` is written in the country's language ("Vigilance jaune orages"):
    only `awareness_type`, a standard code in English, is usable."""
    event = parse_meteoalarm(ALERT, "france")
    assert event is not None
    assert event.kind is Kind.STORM


def test_awareness_level_is_a_composite_string():
    """ "2; yellow; Moderate" -- the rank is the first field, not the string.

    Yellow is CAP rank Moderate, and this excerpt states no `certainty`, so it
    is read as a forecast: MINOR. Saying nothing about certainty is not a claim
    that the thing is happening, and the conservative reading is the only
    honest one here.
    """
    event = parse_meteoalarm(ALERT, "france")
    assert event is not None
    assert event.severity is Severity.MINOR
    assert event.ongoing is True
    assert event.time.tzinfo is not None  # onset carries a local offset (+02:00)
    assert "Alpes-de-Haute-Provence" in event.place


def test_allclear_is_a_lifted_warning_not_an_alert():
    """Like the "no danger" tsunami bulletins: it is displayed, it does not alert."""
    event = parse_meteoalarm(LIFTED, "france")
    assert event is not None
    assert event.severity is Severity.INFO
    assert event.ongoing is False
    assert event.alert == "lifted"


def test_an_orange_forecast_does_not_rank_with_a_destructive_earthquake():
    """The measured defect, on the live feed of 2026-08-26: 461 of the 480
    Meteoalarm warnings sat at SEVERE -- the rank this product gives a M6.5 --
    and "Orange Thunderstorm warning" was the single most frequent title in the
    whole feed. Poland alone contributed 331 of them.

    Orange is CAP rank Severe, forecast (`certainty: Likely`), so it lands one
    rung lower: MODERATE, "dangerous, act if you are in the area". Which is
    what the warning itself says in its own instruction field.
    """
    event = parse_meteoalarm(ORANGE_FORECAST, "poland")
    assert event is not None
    assert event.severity is Severity.MODERATE
    assert event.raw["urgency"] == "Expected"
    assert event.raw["certainty"] == "Likely"
    # the colour is not lost, it is simply no longer pretending to be a rank
    assert event.alert == "Severe"


def test_a_red_warning_the_agency_is_watching_land_reaches_the_top():
    """The counterpart, and the reason this is a rule and not a cap on
    Meteoalarm: 180 mm already measured on the ground, `certainty: Observed`.
    The agency is no longer forecasting, so its rank is taken at face value.
    One warning out of the 2622 served that day qualified."""
    event = parse_meteoalarm(RED_OBSERVED, "spain")
    assert event is not None
    assert event.severity is Severity.EXTREME


def test_a_red_warning_still_to_come_is_severe_not_extreme():
    """Same red warning, with the agency forecasting instead of observing."""
    forecast = deepcopy(RED_OBSERVED)
    forecast["alert"]["info"][0]["certainty"] = "Likely"
    event = parse_meteoalarm(forecast, "spain")
    assert event is not None
    assert event.severity is Severity.SEVERE


def test_a_rank_the_forecaster_is_unsure_of_drops_one_further():
    """CAP keeps certainty separate from severity for a reason, and reading
    only the second put "this may happen" on the rung of "this is happening"."""
    unsure = deepcopy(ORANGE_FORECAST)
    unsure["alert"]["info"][0]["certainty"] = "Possible"
    event = parse_meteoalarm(unsure, "poland")
    assert event is not None
    assert event.severity is Severity.MINOR


def test_meteoalarm_garbage_is_ignored():
    assert parse_meteoalarm({}, "france") is None
    assert parse_meteoalarm({"alert": {"identifier": "x", "info": []}}, "france") is None


# ------------------------------------- positions, measured on 2026-08-26

# Verbatim: the one warning of the ten Meteoalarm country feeds that carries a
# geometry. The UK Met Office fills `area[].polygon` -- a LIST of CAP rings,
# `lat,lon` pairs, latitude first. The other 28896 area blocks captured that
# morning carry an EMMA_ID and no shape at all.
UK_DRAWN = {
    "alert": {
        "identifier": "2.49.0.0.826.0.GB_260825083123_cef44eaa.v1.0.T",
        "info": [
            {
                "language": "en-GB",
                "event": "Yellow thunderstorm warning",
                "headline": "A small risk of flooding and disruption from thunderstorms on Wednesday",  # noqa: E501
                "severity": "Moderate",
                "responseType": ["Prepare"],
                "onset": "2026-08-26T02:00:00+00:00",
                "expires": "2026-08-26T21:00:00+00:00",
                "web": "https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings",
                "area": [
                    {
                        "areaDesc": "East Midlands | East of England | London & South East England | North West England | South West England | Wales | West Midlands | Yorkshire & Humber",  # noqa: E501
                        "polygon": [
                            "51.1285,-0.4452 51.1284,-0.4934 51.1298,-0.5406 51.1325,-0.5868 51.1363,-0.6323 51.1411,-0.677 51.1468,-0.721 51.1531,-0.7644 51.1599,-0.8073 51.1671,-0.8497 51.1744,-0.8918 51.1818,-0.9335 51.189,-0.975 51.1959,-1.0163 51.2023,-1.0575 51.208,-1.0945 51.2137,-1.1305 51.2196,-1.1659 51.2257,-1.2007 51.2318,-1.235 51.238,-1.2692 51.2443,-1.3032 51.2507,-1.3373 51.2572,-1.3717 51.2638,-1.4064 51.2704,-1.4416 51.2771,-1.4775 51.2839,-1.5143 51.2908,-1.5521 51.2988,-1.5991 51.3066,-1.6489 51.3142,-1.7009 51.3219,-1.7545 51.3296,-1.8091 51.3374,-1.8641 51.3454,-1.9189 51.3538,-1.9729 51.3626,-2.0255 51.3719,-2.0762 51.3818,-2.1242 51.3923,-2.169 51.4036,-2.2101 51.4158,-2.2468 51.4241,-2.2683 51.4324,-2.2878 51.4407,-2.3055 51.4492,-2.3217 51.4578,-2.3367 51.4667,-2.3509 51.4759,-2.3645 51.4855,-2.3779 51.4956,-2.3914 51.5062,-2.4052 51.5174,-2.4198 51.5292,-2.4353 51.5417,-2.4521 51.5551,-2.4705 51.5821,-2.5092 51.6116,-2.5524 51.6434,-2.5992 51.6774,-2.6491 51.7133,-2.7011 51.751,-2.7545 51.7905,-2.8086 51.8314,-2.8626 51.8738,-2.9158 51.9173,-2.9673 51.962,-3.0164 52.0075,-3.0623 52.0539,-3.1043 52.1009,-3.1417 52.1619,-3.184 52.2302,-3.2269 52.3042,-3.2692 52.3823,-3.3103 52.4631,-3.3493 52.5449,-3.3852 52.6263,-3.4172 52.7058,-3.4445 52.7819,-3.4661 52.8531,-3.4813 52.9179,-3.4891 52.9749,-3.4886 53.0226,-3.4791 53.0596,-3.4596 53.0726,-3.4473 53.0836,-3.4332 53.0928,-3.4172 53.1006,-3.3996 53.107,-3.3805 53.1125,-3.36 53.1173,-3.3384 53.1215,-3.3157 53.1256,-3.292 53.1297,-3.2676 53.134,-3.2426 53.139,-3.2171 53.1447,-3.1913 53.1515,-3.1652 53.1631,-3.1218 53.1749,-3.0733 53.1868,-3.0208 53.199,-2.965 53.2115,-2.9069 53.2242,-2.8473 53.2373,-2.7872 53.2508,-2.7273 53.2647,-2.6686 53.279,-2.6119 53.2939,-2.5582 53.3092,-2.5083 53.3252,-2.4631 53.3417,-2.4234 53.3538,-2.3985 53.3662,-2.3753 53.3789,-2.3536 53.3919,-2.3334 53.4052,-2.3145 53.4186,-2.2967 53.4323,-2.2798 53.4461,-2.2638 53.4601,-2.2484 53.4741,-2.2335 53.4883,-2.219 53.5025,-2.2047 53.5168,-2.1905 53.5311,-2.1761 53.5454,-2.1632 53.5607,-2.1521 53.5767,-2.1423 53.5931,-2.1336 53.6099,-2.1256 53.6266,-2.1179 53.6431,-2.1101 53.6592,-2.1019 53.6746,-2.0929 53.6891,-2.0827 53.7025,-2.071 53.7145,-2.0574 53.725,-2.0415 53.7336,-2.0231 53.7414,-1.9989 53.7476,-1.9714 53.7524,-1.941 53.7558,-1.9082 53.758,-1.8735 53.759,-1.8373 53.7588,-1.8001 53.7577,-1.7624 53.7555,-1.7247 53.7526,-1.6874 53.7488,-1.6511 53.7443,-1.6161 53.7392,-1.5829 53.7336,-1.5521 53.7274,-1.5246 53.7201,-1.4982 53.7118,-1.4728 53.7025,-1.4482 53.6924,-1.4243 53.6816,-1.4009 53.6701,-1.3778 53.6581,-1.3548 53.6457,-1.3318 53.6329,-1.3086 53.6198,-1.2851 53.6066,-1.261 53.5933,-1.2362 53.5801,-1.2106 53.5627,-1.1767 53.5443,-1.1419 53.5251,-1.1062 53.5051,-1.0699 53.4845,-1.0331 53.4634,-0.9959 53.442,-0.9586 53.4204,-0.9212 53.3986,-0.8839 53.3768,-0.8468 53.3552,-0.8102 53.3339,-0.7741 53.3129,-0.7388 53.2925,-0.7043 53.274,-0.6733 53.2555,-0.6429 53.237,-0.613 53.2185,-0.5834 53.2001,-0.5542 53.1817,-0.5252 53.1634,-0.4963 53.1452,-0.4676 53.1271,-0.4388 53.109,-0.4099 53.0912,-0.3809 53.0734,-0.3517 53.0558,-0.3221 53.0384,-0.2922 53.0212,-0.2624 53.0039,-0.2321 52.9865,-0.2015 52.969,-0.1707 52.9516,-0.1397 52.9343,-0.1086 52.9172,-0.0776 52.9005,-0.0466 52.8841,-0.0159 52.8683,0.0146 52.8529,0.0447 52.8383,0.0743 52.8243,0.1033 52.8112,0.1317 52.8014,0.1529 52.7914,0.1734 52.7813,0.1935 52.7714,0.2132 52.7617,0.2326 52.7523,0.2519 52.7434,0.2711 52.735,0.2904 52.7274,0.3099 52.7206,0.3297 52.7148,0.35 52.7101,0.3707 52.7065,0.3921 52.7043,0.4143 52.7039,0.4401 52.7057,0.4663 52.7095,0.4929 52.715,0.52 52.7218,0.5475 52.7295,0.5755 52.738,0.6039 52.7469,0.6328 52.7558,0.662 52.7644,0.6917 52.7725,0.7218 52.7796,0.7524 52.7855,0.7833 52.7898,0.8147 52.7943,0.8524 52.7993,0.8924 52.8046,0.9342 52.81,0.9774 52.815,1.0214 52.8194,1.0659 52.823,1.1102 52.8253,1.1541 52.8261,1.197 52.8251,1.2384 52.822,1.2778 52.8165,1.3149 52.8082,1.349 52.797,1.3799 52.7771,1.4162 52.7513,1.4506 52.7202,1.4828 52.6847,1.5127 52.6454,1.54 52.6032,1.5646 52.5588,1.5863 52.513,1.6048 52.4666,1.62 52.4203,1.6317 52.375,1.6396 52.3314,1.6435 52.2903,1.6434 52.2525,1.6389 52.2195,1.63 52.187,1.616 52.1548,1.5975 52.123,1.575 52.0916,1.549 52.0606,1.5202 52.0299,1.489 51.9996,1.456 51.9697,1.4217 51.9401,1.3867 51.9108,1.3516 51.882,1.3167 51.8534,1.2828 51.8252,1.2503 51.7985,1.2193 51.772,1.1868 51.7458,1.153 51.7198,1.1182 51.6942,1.0828 51.6689,1.047 51.644,1.011 51.6195,0.9752 51.5954,0.9398 51.5717,0.9052 51.5484,0.8715 51.5257,0.8391 51.5035,0.8083 51.4818,0.7793 51.466,0.7596 51.4502,0.7419 51.4347,0.7257 51.4193,0.7106 51.4042,0.6963 51.3893,0.6823 51.3747,0.6681 51.3604,0.6535 51.3465,0.6379 51.333,0.6211 51.3199,0.6025 51.3073,0.5817 51.2951,0.5584 51.2834,0.5321 51.2657,0.4839 51.2486,0.4282 51.2323,0.3661 51.2168,0.2987 51.2022,0.2269 51.1887,0.1518 51.1763,0.0744 51.1651,-0.0043 51.1552,-0.0832 51.1468,-0.1613 51.1397,-0.2376 51.1343,-0.311 51.1305,-0.3806 51.1285,-0.4452"  # noqa: E501
                        ],
                    }
                ],
                "parameter": [
                    {"value": "2; Yellow; Moderate", "valueName": "awareness_level"},
                    {"value": "3; Thunderstorm", "valueName": "awareness_type"},
                ],
            }
        ],
    }
}


def test_the_one_producer_that_draws_its_warnings_is_placed():
    event = parse_meteoalarm(UK_DRAWN, "united-kingdom")
    assert event is not None
    # centre of the published ring, over the English Midlands
    assert event.lat is not None and event.lon is not None
    assert round(event.lat, 3) == 52.479
    assert round(event.lon, 3) == -0.911


def test_an_emma_id_is_not_a_position():
    """Lesson 15. EMMA_ID / NUTS3 / WARNCELLID name an area whose shape
    Meteoalarm does not publish anywhere: resolving them would mean guessing,
    and a warning in the wrong province is worse than one with no marker."""
    event = parse_meteoalarm(ALERT, "france")
    assert event is not None
    assert event.lat is None and event.lon is None


def test_a_broken_ring_leaves_the_warning_unplaced_it_does_not_move_it():
    broken = deepcopy(UK_DRAWN)
    broken["alert"]["info"][0]["area"][0]["polygon"] = ["3237.5,13040.7 3237.6,13040.8"]
    event = parse_meteoalarm(broken, "united-kingdom")
    assert event is not None
    assert event.lat is None and event.lon is None


# ------------------------------------------------------------------------- WMO

WMO_ITEM = {
    "id": "IN-1786996079872015_69",
    "event": "Moderate Rain",
    "headline": "Moderate Rain is very likely to continue",
    "sent": "2026-08-17 20:17:19",
    "expires": "2026-08-17 23:15:00",
    "areaDesc": "Gomati, Sepahijala, South Tripura, West Tripura",
    "mid": "066",
    "s": 3,
    "u": 3,
    "c": 3,
    "url": "in-ndma-xx/2026/08/17/20/17/19-77728c723.xml",
    "effective": "2026-08-17 20:15:00",
}


# Verbatim items of https://severeweather.wmo.int/json/wmo_all.json, 2026-08-26.
# `s` = 4 is Extreme, `s` = 1 is Minor: proven against the `<severity>` element
# of the CAP document each of them links.
EXTREME_ITEM = {
    "id": "IN-1787725701486038_45",
    "event": "Squally",
    "headline": "Squally weather with strong surface wind 40-50 Kmph gusting to 60 kmph very likely along and off Andaman and Nicobar coast. Sea conditions are very likely to be rough. Fishermen are advised not to venture into the Andaman sea till 30-08-2026.",  # noqa: E501
    "sent": "2026-08-26 06:57:28",
    "expires": "2026-08-26 12:56:00",
    "areaDesc": "Nicobars, North And Middle Andaman, South Andamans districts of Andaman and Nicobar Islands",  # noqa: E501
    "mid": "066",
    "ra": "2",
    "s": 4,
    "u": 4,
    "c": 3,
    "url": "in-ndma-xx/2026/08/26/06/57/28-4634ce879c99a01b0c238649533aa637.xml",
    "effective": "2026-08-26 06:56:00",
}

MINOR_ITEM = {
    "id": "13013241600000_20260826160913",
    "event": "Flash flood events",
    "headline": "Yuanshi County Meteorological Observatory updates blue warning for flash flood disasters [Level IV/General]",  # noqa: E501
    "sent": "2026-08-26 08:09:13",
    "expires": "2026-08-27 08:09:13",
    "areaDesc": "Yuanshi County",
    "mid": "001",
    "ra": "2",
    "s": 1,
    "u": 0,
    "c": 0,
    "url": "cn-cma-xx/2026/08/26/08/09/13-53fc51b1ff8ae9a952e60bcf476e803.xml",
    "effective": "2026-08-26 08:09:13",
}

# Same feed, same shape, and the CAP path under the OTHER name.
CAPURL_ITEM = {
    "id": "2.49.0.0.398.0-20260826-074701-0469728-00-EN",
    "event": "Forestfire",
    "headline": "Forestfire",
    "sent": "2026-08-26 07:47:01",
    "expires": "2026-08-27 15:00:00",
    "areaDesc": "Nauyrzym district (Kostanay Region)",
    "mid": "070",
    "ra": "2",
    "s": 4,
    "u": 2,
    "c": 3,
    "capURL": "kz-kazhydromet-en/2026/08/26/07/47/01-8038108399991106b5a555b130f4d263.xml",
    "effective": "",
}


def test_wmo_ranks_grow_with_the_severity_they_do_not_shrink():
    """This was read backwards ("1 = Extreme"), and the cost was the whole
    source: it kept the 246 Minor alerts, published them as EXTREME, and threw
    away the 91 Extreme and 353 Severe ones.

    The mapping below is not a reading of the documentation, it is the result
    of comparing `s` with the `<severity>` element of 100 CAP documents drawn
    from every rank of the 2026-08-26 aggregate: 0 Unknown, 1 Minor, 2
    Moderate, 3 Severe, 4 Extreme, 99 agreements out of 100."""
    event = parse_wmo(WMO_ITEM)
    assert event is not None
    # Ranks still GROW with `s`. Where each one lands on this product's ladder
    # is one rung lower than the aggregate's own word for it, because every one
    # of these is a national agency FORECASTING (see the next test).
    assert event.severity is Severity.MODERATE  # s = 3, forecast
    assert parse_wmo(EXTREME_ITEM).severity is Severity.SEVERE  # s = 4, forecast
    assert parse_wmo(MINOR_ITEM).severity is Severity.INFO  # s = 1

    unknown = parse_wmo({**WMO_ITEM, "id": "X-2", "s": 0})
    assert unknown is not None and unknown.severity is Severity.INFO

    # ... and an agency that says it is WATCHING the thing keeps its own rank.
    observed = parse_wmo({**EXTREME_ITEM, "id": "X-3", "c": 4})
    assert observed is not None and observed.severity is Severity.EXTREME


def test_urgency_and_certainty_are_ranked_the_same_way():
    """Measured on the same 100 documents: u 1=Past 2=Future 3=Expected
    4=Immediate, c 2=Possible 3=Likely 4=Observed."""
    assert parse_wmo(EXTREME_ITEM).raw["urgency"] == "immediate"  # u = 4
    assert parse_wmo(WMO_ITEM).raw["urgency"] == "expected"  # u = 3
    assert parse_wmo(EXTREME_ITEM).raw["certainty"] == "likely"  # c = 3
    assert parse_wmo(MINOR_ITEM).raw["urgency"] is None  # u = 0, unknown


def test_a_national_extreme_is_not_this_products_extreme():
    """Measured on the aggregate of 2026-08-26: 103 alerts at `s` = 4, and 75
    of them are ONE member's routine forest-fire-danger bulletins (070,
    Kazhydromet -- CAPURL_ITEM below is one of them, verbatim). They were the
    entire top of this product's feed, above every earthquake on the planet.

    Not one of those 103 claimed `c` = 4 (Observed). The aggregate mixes some
    thirty national scales that CAP itself never promised were comparable, so
    the one thing worth reading off them is the distinction the producers DO
    all encode: are you watching this, or forecasting it.
    """
    kazakh_fire_danger = parse_wmo(CAPURL_ITEM)
    assert kazakh_fire_danger is not None
    assert kazakh_fire_danger.severity is Severity.SEVERE  # was EXTREME
    assert kazakh_fire_danger.raw["certainty"] == "likely"  # never "observed"


def test_the_filter_keeps_the_top_tiers_not_the_bottom_ones():
    """`wmo_max_severity_rank` counts tiers down from Extreme, which is what
    its author meant. Read as a value of `s` it did the exact opposite."""
    payload = {"items": [MINOR_ITEM, WMO_ITEM, EXTREME_ITEM]}

    top = WmoCapSource(max_severity_rank=1).select(payload)
    assert [i["s"] for i in top] == [4]

    two = WmoCapSource(max_severity_rank=2).select(payload)
    assert sorted(i["s"] for i in two) == [3, 4]

    everything = WmoCapSource(max_severity_rank=4).select(payload)
    assert len(everything) == 3


def test_the_cap_link_is_named_capurl_on_almost_half_the_items():
    """1247 items name it `url`, **977 name it `capURL`**, and only `url` was
    read -- so those 977 lost their link to the authoritative document as well
    as any hope of a position."""
    assert wmo_cap_path(CAPURL_ITEM) == CAPURL_ITEM["capURL"]
    assert wmo_cap_path(WMO_ITEM) == WMO_ITEM["url"]
    assert wmo_cap_path({"id": "x"}) is None

    event = parse_wmo(CAPURL_ITEM)
    assert event is not None
    assert event.url == WMO_CAP_BASE + CAPURL_ITEM["capURL"]


def test_the_aggregate_alone_places_nothing():
    """Not one of the 2224 items carries a coordinate. Whatever position an
    alert gets comes from its CAP document."""
    event = parse_wmo(EXTREME_ITEM)
    assert event is not None
    assert event.lat is None and event.lon is None


def test_a_position_read_from_the_cap_reaches_the_event():
    event = parse_wmo(EXTREME_ITEM, (11.7, 92.7))
    assert event is not None
    assert (event.lat, event.lon) == (11.7, 92.7)


def test_the_source_places_what_its_cache_already_knows():
    source = WmoCapSource(max_severity_rank=1)
    source.areas._positions[EXTREME_ITEM["url"]] = (11.7, 92.7)
    (event,) = source.parse_payload({"items": [EXTREME_ITEM, MINOR_ITEM]})
    assert (event.lat, event.lon) == (11.7, 92.7)


def test_wmo_timestamps_have_no_timezone_and_are_utc():
    event = parse_wmo(WMO_ITEM)
    assert event is not None
    assert event.time.tzinfo is not None
    assert event.time.hour == 20


def test_wmo_links_back_to_the_source_cap():
    """The aggregate's timestamps have shown gaps against the source CAP: the
    link to the CAP must remain reachable."""
    event = parse_wmo(WMO_ITEM)
    assert event is not None
    assert event.url is not None
    assert event.url.startswith("https://severeweather.wmo.int/v2/cap-alerts/")


def test_wmo_country_comes_from_the_id_prefix():
    """It is the feed's only country indication, and it is enough for the flag."""
    assert parse_wmo(WMO_ITEM).country_code == "IN"
    assert parse_wmo({**WMO_ITEM, "id": "CN-42"}).country_code == "CN"
    # an identifier without a country prefix must not invent a flag
    assert parse_wmo({**WMO_ITEM, "id": "12345-x"}).country_code is None


def test_wmo_classification():
    assert classify_wmo("Tsunami Warning") is Kind.TSUNAMI
    assert classify_wmo("Tropical Cyclone Warning") is Kind.CYCLONE
    assert classify_wmo("Moderate Rain") is Kind.STORM
    assert classify_wmo("Flash Flood") is Kind.FLOOD
    assert classify_wmo("Heat Wave") is Kind.HEAT
    assert classify_wmo("Something Unheard Of") is Kind.OTHER


def test_wmo_garbage_is_ignored():
    assert parse_wmo({}) is None
    assert parse_wmo({"id": "x", "sent": "not a date"}) is None
