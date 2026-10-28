"""The groups of a raw METAR the AWC cache CSV gets wrong.

Measured on the captured `metars.cache.csv.gz` (5237 rows): the CSV writes
`wind_dir_degrees` 0 for 1076 calm and 277 VRB reports -- read as a number,
"from north"; it converts MPS winds to whole knots (URMM `11010G15MPS`
becomes 19/29 kt); it turns a non-US `9999` (10 km or more) into "6+" statute
miles, which reads as "at least 9.7 km"; and converting its `altim_in_hg`
29.85 gives 1010.8 hPa where the report says Q1011. So wind, gust,
visibility and QNH come from `raw_text`.

Only the body counts: the FIRST wind group is the observation, and a gust
inside TEMPO / BECMG / RMK is a forecast or a remark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

KT_TO_MS = 0.514444
KMH_TO_MS = 1 / 3.6
SM_TO_KM = 1.609344
INHG_TO_HPA = 33.8639

_STOP = frozenset({"TEMPO", "BECMG", "RMK", "NOSIG", "FM", "PROB30", "PROB40", "TL", "AT"})
_WIND = re.compile(
    r"^(?P<dir>\d{3}|VRB)(?P<speed>\d{2,3})(?:G(?P<gust>\d{2,3}))?(?P<unit>KT|MPS|KMH)$"
)
_VIS_M = re.compile(r"^(?P<m>\d{4})(?:NDV|[NSEW]{1,2})?$")
_VIS_SM = re.compile(r"^(?P<p>[PM])?(?P<v>\d{1,2}(?:/\d{1,2})?)SM$")
_Q = re.compile(r"^Q(?P<hpa>\d{4})$")
_A = re.compile(r"^A(?P<inhg>\d{4})$")


@dataclass
class Groups:
    wind_ms: float | None = None
    wind_from_deg: int | None = None
    calm: bool = False
    wind_variable: bool = False
    gust_ms: float | None = None
    visibility_km: float | None = None
    visibility_at_least: bool = False
    qnh_hpa: float | None = None
    qnh_derived: bool = False


def _speed(value: str, unit: str) -> float:
    n = int(value)
    if unit == "MPS":
        return float(n)
    if unit == "KMH":
        return n * KMH_TO_MS
    return n * KT_TO_MS


def body(raw: str) -> list[str]:
    """The groups of the observation itself: after the station and time,
    before any trend or remark."""
    out = []
    for token in raw.split():
        if token in _STOP or token.startswith(("FM", "TL")) and token[2:].isdigit():
            break
        out.append(token)
    return out


def parse(raw: str) -> Groups:
    g = Groups()
    tokens = body(raw)
    wind_seen = vis_seen = False
    for i, token in enumerate(tokens):
        if not wind_seen:
            m = _WIND.match(token)
            if m:
                wind_seen = True
                speed = _speed(m["speed"], m["unit"])
                g.wind_ms = round(speed, 1)
                if m["gust"]:
                    g.gust_ms = round(_speed(m["gust"], m["unit"]), 1)
                if m["dir"] == "VRB":
                    g.wind_variable = True
                elif int(m["dir"]) == 0 and int(m["speed"]) == 0:
                    g.calm = True
                else:
                    g.wind_from_deg = int(m["dir"])
                continue
        if not vis_seen:
            if token == "CAVOK":
                vis_seen = True
                g.visibility_km, g.visibility_at_least = 10.0, True
                continue
            m = _VIS_M.match(token)
            if m and wind_seen:
                vis_seen = True
                metres = int(m["m"])
                if metres == 9999:
                    g.visibility_km, g.visibility_at_least = 10.0, True
                else:
                    g.visibility_km = round(metres / 1000, 1)
                continue
            m = _VIS_SM.match(token)
            if m:
                vis_seen = True
                whole = 0
                # "1 3/4SM": the whole part is the previous token
                if "/" in m["v"] and i > 0 and tokens[i - 1].isdigit() and len(tokens[i - 1]) == 1:
                    whole = int(tokens[i - 1])
                miles = float(whole + Fraction(m["v"]))
                g.visibility_km = round(miles * SM_TO_KM, 1)
                # US practice: 10SM is reported for "10 or more"; P6SM is "more than 6"
                g.visibility_at_least = m["p"] == "P" or (miles >= 10 and m["p"] != "M")
                continue
        m = _Q.match(token)
        if m:
            g.qnh_hpa, g.qnh_derived = float(int(m["hpa"])), False
            continue
        m = _A.match(token)
        if m and g.qnh_hpa is None:
            g.qnh_hpa = round(int(m["inhg"]) / 100 * INHG_TO_HPA, 1)
            g.qnh_derived = True
    return g
