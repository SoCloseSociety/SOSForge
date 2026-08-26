"""Does this event concern the person sitting at this machine?

The whole value of a local agent is that it can be quiet. A tracker that
notifies on everything is a tracker whose notifications get turned off, and
then it is worth less than nothing -- it is a false sense of coverage.

So the rule here is deliberately asymmetric: err toward silence for small
things, and toward speaking for the few that could actually hurt someone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def felt_radius_km(magnitude: float | None, depth_km: float | None = None) -> float:
    """How far away this quake is plausibly worth telling someone about.

    A fixed radius is wrong in both directions: 100 km makes a M2.5 shout at a
    city that never felt it, and makes a M7.8 silent for the coast 300 km away
    that has minutes to act. This grows with magnitude, roughly like the felt
    area does.

    Depth matters too, in the opposite way people expect: a deep quake is felt
    over a WIDER area and less strongly. We widen the radius for depth, and
    the severity ladder upstream is what keeps a deep event from screaming.
    """
    if magnitude is None:
        return 120.0
    # anchored on rough felt-report distances: M3 ~ 30 km, M5 ~ 200 km,
    # M6 ~ 400 km, M7 ~ 800 km, M8 ~ 1500 km
    radius = 10.0 * (2.6 ** max(magnitude - 2.0, 0.0))
    if depth_km is not None and depth_km > 70:
        radius *= 1.4
    return min(radius, 2000.0)


@dataclass(frozen=True)
class Decision:
    notify: bool
    distance_km: float | None
    reason: str


# Hazards that are dangerous where they ARE, and where they are heading.
# A cyclone 400 km away tomorrow is your problem tonight.
WIDE_HAZARDS = {"cyclone", "tsunami"}
LOCAL_HAZARDS = {"flood", "wildfire", "storm", "heat", "volcano", "drought"}
SEVERITY_RANK = {"info": 0, "minor": 1, "moderate": 2, "severe": 3, "extreme": 4}


def should_notify(
    event: dict,
    home_lat: float,
    home_lon: float,
    *,
    min_severity: str = "moderate",
    max_distance_km: float = 300.0,
    home_country: str | None = None,
) -> Decision:
    """One event, one verdict, with the reason attached.

    The reason is not decoration: when this thing wakes someone at 3 a.m. they
    are owed an explanation, and when it stays silent about something they
    later hear about, we need to be able to say why.
    """
    severity = str(event.get("severity") or "info")
    kind = str(event.get("kind") or "other")
    lat, lon = event.get("lat"), event.get("lon")

    if SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(min_severity, 2):
        return Decision(False, None, f"below the {min_severity} threshold")

    if lat is None or lon is None:
        # A warning with no coordinates -- a US county zone, a national
        # bulletin, most of the WMO aggregate. It cannot be placed, so
        # distance cannot decide.
        #
        # The first version of this rule said "notify if extreme". Replaying a
        # real day of the feed from Tokyo, that rang for Saskatoon, Chesapeake
        # Bay and Potter County, Texas -- because severe zone alerts with no
        # geometry are common and the rule had no notion of WHERE. Country is
        # the only locator such an alert carries, so it is the one we use.
        if not home_country:
            return Decision(False, None, "no position and no home country to compare it to")
        if str(event.get("country_code") or "").upper() != home_country.upper():
            return Decision(False, None, f"no position, and it is not in {home_country}")
        if SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK["severe"]:
            return Decision(True, None, f"severe alert in {home_country}, no position given")
        return Decision(False, None, "no position, not severe enough")

    distance = haversine_km(home_lat, home_lon, float(lat), float(lon))

    if kind == "earthquake":
        limit = felt_radius_km(event.get("magnitude"), event.get("depth_km"))
    elif kind in WIDE_HAZARDS:
        limit = max(max_distance_km, 600.0)
    elif kind in LOCAL_HAZARDS:
        limit = max_distance_km
    else:
        limit = max_distance_km

    if distance <= limit:
        return Decision(True, distance, f"{distance:.0f} km away, within {limit:.0f} km")
    return Decision(False, distance, f"{distance:.0f} km away, beyond {limit:.0f} km")
