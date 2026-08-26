"""Fifth audit -- where the product is technically working and scientifically wrong.

These are not crashes. Every one of them ships a confident statement that the
underlying data does not support, which on an emergency product is the more
expensive kind of bug: it spends the user's trust, and trust is what makes the
next alert work.
"""

from __future__ import annotations

from datetime import timedelta

from app.models.event import Severity, severity_from_magnitude, utcnow
from app.sources.usgs import parse_feature


def usgs_feature(**props) -> dict:
    """Shaped exactly like a real all_hour.geojson feature."""
    base = {
        "mag": 6.5,
        "place": "195 km SSE of Sand Point, Alaska",
        "time": int((utcnow() - timedelta(minutes=2)).timestamp() * 1000),
        "updated": int(utcnow().timestamp() * 1000),
        "tsunami": 0,
        "status": "automatic",
        "alert": None,
        "magType": "mww",
        "sig": 650,
    }
    base.update(props)
    return {
        "type": "Feature",
        "id": "us7000test",
        "properties": base,
        "geometry": {"type": "Point", "coordinates": [-160.5, 54.6, 25.0]},
    }


class TestTheUsgsTsunamiFlagIsNotATsunami:
    """USGS documents this field as: "set to 1 for large events in oceanic
    regions... The existence or value of this flag does not indicate if a
    tsunami actually did or will exist."

    The product read it as a tsunami. It raised `severity` straight to
    EXTREME, lit the full-width red TSUNAMI ALERT banner in five languages,
    played the alarm, and counted into the `tsunami_active` KPI -- for any
    M6.5 in the open ocean, while PTWC was simultaneously publishing "no
    tsunami danger". A tsunami statement can only come from a warning centre,
    which is exactly what `sources/tsunami.py` already ingests, correctly,
    including the "Information = no danger" nuance.

    Two false alarms and nobody believes the banner on the day it is real.
    """

    def test_the_flag_does_not_declare_a_tsunami(self):
        event = parse_feature(usgs_feature(tsunami=1, mag=6.5))
        assert event is not None
        assert event.tsunami is False, "USGS raised a tsunami alert on its own authority"

    def test_the_flag_does_not_force_the_severity_to_extreme(self):
        event = parse_feature(usgs_feature(tsunami=1, mag=6.5))
        assert event is not None
        assert event.severity is Severity.SEVERE, "an M6.5 was promoted to EXTREME by the flag"

    def test_the_flag_is_kept_where_it_belongs(self):
        """It is still information -- an oceanic event large enough that a
        centre may speak. It belongs in `raw`, not in the banner."""
        event = parse_feature(usgs_feature(tsunami=1))
        assert event is not None
        assert event.raw.get("tsunami_flag") is True

    def test_a_real_centre_bulletin_still_declares_one(self):
        """The counterpart: nothing here weakens a real warning."""
        assert severity_from_magnitude(2.0, tsunami=True) is Severity.EXTREME


class TestDepthChangesWhatAMagnitudeMeans:
    """A M7.2 at 600 km under the Sea of Okhotsk is felt as a long sway a
    thousand kilometres away and damages nothing. A M6.2 at 10 km under a city
    is a mass-casualty event. The product ranked the first as EXTREME and the
    second below it, on magnitude alone -- while carrying `depth_km` in the
    event and never reading it.

    Deep-focus M7s are not rare: Fiji-Tonga, Vrancea and Okhotsk produce them
    routinely, and each one lit the product's loudest alarm.
    """

    def test_a_very_deep_large_quake_is_not_extreme(self):
        deep = usgs_feature(mag=7.2)
        deep["geometry"]["coordinates"] = [150.0, 54.0, 600.0]
        event = parse_feature(deep)
        assert event is not None
        assert event.depth_km == 600.0
        assert event.severity is not Severity.EXTREME, "a 600 km deep quake sounded the top alarm"

    def test_a_shallow_large_quake_is_still_extreme(self):
        shallow = usgs_feature(mag=7.2)
        shallow["geometry"]["coordinates"] = [150.0, 54.0, 12.0]
        event = parse_feature(shallow)
        assert event is not None
        assert event.severity is Severity.EXTREME

    def test_the_impact_estimate_wins_over_the_magnitude(self):
        """USGS PAGER already folds in depth AND exposed population, and the
        product ingested it into `alert` without ever using it. A red PAGER on
        a M6.2 means a catastrophe is being estimated right now."""
        event = parse_feature(usgs_feature(mag=6.2, alert="red"))
        assert event is not None
        assert event.severity is Severity.EXTREME

    def test_a_green_pager_does_not_demote_a_big_one(self):
        event = parse_feature(usgs_feature(mag=7.4, alert="green"))
        assert event is not None
        assert event.severity is Severity.EXTREME


class TestPreliminaryIsSaidOutLoud:
    """The first automatic solution of a large quake is routinely off by up to
    a magnitude unit and tens of kilometres. Every agency labels it. USGS ships
    `status: automatic | reviewed` in the same payload the product downloads,
    and the product dropped it -- so a 90-second-old automatic M6.8 rendered
    exactly like a next-day reviewed Mww 6.1.

    Users screenshot the wrong number, and the later revision then reads as the
    tracker being sloppy rather than as seismology working normally.
    """

    def test_an_automatic_solution_says_so(self):
        event = parse_feature(usgs_feature(status="automatic"))
        assert event is not None
        assert event.preliminary is True

    def test_a_reviewed_solution_says_so(self):
        event = parse_feature(usgs_feature(status="reviewed"))
        assert event is not None
        assert event.preliminary is False


class TestFeltReportsAndShakingReachTheBrowser:
    """`mmi` (measured/estimated shaking), `cdi` (what people reported) and
    `felt` (how many reported) are in every feature already fetched, and were
    all discarded. They answer the only question the person who just felt it is
    actually asking, which magnitude does not: how strong was it HERE, and did
    anyone else feel it.
    """

    def test_shaking_and_felt_counts_survive_normalisation(self):
        event = parse_feature(usgs_feature(mmi=6.1, cdi=5.8, felt=1243))
        assert event is not None
        assert event.intensity_mmi == 6.1
        assert event.felt_reports == 1243

    def test_their_absence_is_not_a_zero(self):
        """No report is not "nobody felt it": most of the planet has no
        reporters. A zero would be a claim we cannot make."""
        event = parse_feature(usgs_feature())
        assert event is not None
        assert event.intensity_mmi is None
        assert event.felt_reports is None
