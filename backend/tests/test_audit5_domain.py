"""Fifth audit -- where the product is technically working and scientifically wrong.

These are not crashes. Every one of them ships a confident statement that the
underlying data does not support, which on an emergency product is the more
expensive kind of bug: it spends the user's trust, and trust is what makes the
next alert work.
"""

from __future__ import annotations

from datetime import timedelta

from app.models.event import (
    _LADDER,
    Severity,
    severity_for_cap,
    severity_for_quake,
    severity_from_magnitude,
    utcnow,
)
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


class TestOneLadderForEveryHazard:
    """The ladder is one summary judgment -- how much harm should a reader
    expect to people -- and it was not comparable across hazard families.

    Measured on the live feed of 2026-08-26 (1576 events): 480 were Meteoalarm,
    461 of them at SEVERE, the rank of a M6.5 earthquake. The single most
    frequent title in the whole product was "Orange Thunderstorm warning" (216).
    Every one of the 233 EXTREME events came from a weather forecast; not one
    was an earthquake, a tsunami or an eruption.

    `severity_for_cap` is the same shape as `severity_for_quake`: the source's
    own number is the base, and the field that knows more than we do about
    whether the harm is REAL is allowed to move it.
    """

    def test_a_forecast_sits_one_rung_below_the_same_thing_observed(self):
        assert severity_for_cap("Extreme", "Immediate", "Likely") is Severity.SEVERE
        assert severity_for_cap("Extreme", "Immediate", "Observed") is Severity.EXTREME
        assert severity_for_cap("Severe", "Expected", "Likely") is Severity.MODERATE
        assert severity_for_cap("Severe", "Expected", "Observed") is Severity.SEVERE

    def test_a_rank_the_agency_is_unsure_of_drops_one_further(self):
        assert severity_for_cap("Severe", "Future", "Possible") is Severity.MINOR
        # The TOP rank stops at SEVERE, deliberately: see
        # TestATopRankNeverFallsBelowSevere below. This line used to assert
        # MODERATE, and MODERATE put a national RED warning below a routine
        # orange one marked Observed.
        assert severity_for_cap("Extreme", "Future", "Unlikely") is Severity.SEVERE

    def test_advance_notice_is_not_punished(self):
        """Lesson 14: a weather warning is PUBLISHED BEFORE it starts, and that
        is the whole point of it. Urgency answers "how soon", severity answers
        "how bad": collapsing them re-creates the flattening this rule exists to
        undo. A red warning for tonight ranks exactly like a red warning for
        this afternoon."""
        assert severity_for_cap("Extreme", "Future", "Likely") is severity_for_cap(
            "Extreme", "Immediate", "Likely"
        )

    def test_a_danger_that_is_over_stops_being_ranked(self):
        assert severity_for_cap("Extreme", "Past", "Observed") is Severity.MINOR

    def test_nothing_promotes_above_the_agencys_own_rank(self):
        """No CAP field says who is exposed, so there is no honest way to call
        a warning worse than the agency called it. `severity_for_quake` may
        promote because PAGER knows the exposed population; nothing here does."""
        for urgency in ("immediate", "expected", "future", "past", None):
            for certainty in ("observed", "likely", "possible", None):
                assert severity_for_cap("Moderate", urgency, certainty) is not Severity.SEVERE, (
                    f"moderate promoted by {urgency}/{certainty}"
                )

    def test_an_absent_rank_says_nothing_rather_than_something_wrong(self):
        assert severity_for_cap(None) is Severity.INFO
        assert severity_for_cap("Unknown", "Immediate", "Observed") is Severity.INFO
        assert severity_for_cap("nonsense") is Severity.INFO

    def test_the_orange_warning_no_longer_outranks_the_shallow_quake(self):
        """The whole point, stated as the comparison a reader actually makes.
        The Polish orange rain warning is verbatim from `feeds-poland`."""
        orange = severity_for_cap("Severe", "Expected", "Likely")
        shallow_m6 = severity_for_quake(6.0, depth_km=10.0)
        assert _LADDER.index(orange) < _LADDER.index(shallow_m6)


class TestATopRankNeverFallsBelowSevere:
    """A national service publishing RED is at the top of its own scale, and
    "take action now" is what red means in every European country's public
    communication.

    The forecast demotion is right for the gap between EXTREME and SEVERE: a
    forecast of harm is not a measurement of harm. Carrying it one step further
    is not. Dropping a red warning to MODERATE puts it BELOW a routine orange
    one that happens to be marked Observed -- an inversion a reader would
    notice, and rightly distrust, the moment they checked the national site
    next to ours.
    """

    def test_a_red_forecast_with_weak_certainty_stops_at_severe(self):
        from app.models.event import severity_for_cap

        assert severity_for_cap("Extreme", urgency="Expected", certainty="Possible") is (
            Severity.SEVERE
        )
        assert severity_for_cap("Extreme", urgency="Expected", certainty="Unlikely") is (
            Severity.SEVERE
        )

    def test_a_red_that_is_being_observed_is_still_extreme(self):
        from app.models.event import severity_for_cap

        assert severity_for_cap("Extreme", certainty="Observed") is Severity.EXTREME

    def test_the_floor_does_not_lift_the_ranks_below_it(self):
        """Only the top rank has this floor. An orange forecast that is merely
        possible must still be allowed to fall -- that is the whole point of
        the recalibration, and 461 alerts depended on it."""
        from app.models.event import severity_for_cap

        assert severity_for_cap("Severe", certainty="Possible") is Severity.MINOR
        assert severity_for_cap("Moderate", certainty="Possible") is Severity.INFO

    def test_a_red_whose_danger_has_passed_is_not_held_up_by_the_floor(self):
        """`Past` means it is over. A floor that kept a finished warning at
        SEVERE would be the freshness lie wearing another costume."""
        from app.models.event import severity_for_cap

        assert severity_for_cap("Extreme", urgency="Past", certainty="Observed") is Severity.MINOR


class TestABulletinWithNothingToSayIsNotAnEmergency:
    """The Kazakh wall, closed by measurement rather than by taste.

    75 of the 103 alerts at the WMO aggregate's top rank were one member's
    routine forest-fire-danger bulletins. Demoting every forecast a further
    rung would have removed them -- and taken India's "Extremely Heavy Rain"
    over Uttar Pradesh with them. That is lesson 19: never narrow a
    classification without measuring what the narrowing costs.

    The discriminator that does separate them was measured on 70 real CAP
    documents at the top two ranks, and it is consistent PER ISSUER, not per
    alert: India 17/17 carry actionable text, Kazakhstan 0/14, issuer 066
    0/8. A CAP document with neither an instruction nor a description is a
    statement about conditions. It tells nobody to do anything, because there
    is nothing to do yet.
    """

    def test_an_alert_with_nothing_to_do_drops_a_rung(self):
        from app.models.event import severity_for_cap

        assert severity_for_cap("Extreme", certainty="Likely", actionable=False) is (
            Severity.MODERATE
        )

    def test_the_same_alert_with_an_instruction_keeps_its_rank(self):
        from app.models.event import severity_for_cap

        assert severity_for_cap("Extreme", certainty="Likely", actionable=True) is Severity.SEVERE

    def test_it_never_demotes_something_being_observed(self):
        """A measurement outranks the absence of paperwork: if the agency says
        it is happening now, a missing instruction block does not make it less
        true."""
        from app.models.event import severity_for_cap

        assert severity_for_cap("Extreme", certainty="Observed", actionable=False) is (
            Severity.EXTREME
        )

    def test_not_knowing_is_not_the_same_as_knowing_there_is_nothing(self):
        """Most sources never tell us either way -- the WMO text is only
        available once its CAP document has been fetched, which happens a few
        cycles later. Until then the rank must stand: demoting on absence of
        knowledge would rank an alert by how recently we met it."""
        from app.models.event import severity_for_cap

        assert severity_for_cap("Extreme", certainty="Likely") is Severity.SEVERE
        assert severity_for_cap("Extreme", certainty="Likely", actionable=None) is Severity.SEVERE
