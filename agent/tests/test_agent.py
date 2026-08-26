"""What the agent must get right, in the order it matters.

Silence is this thing's default state and its main feature. Every test here
either protects that silence or protects the few moments it must break it.
"""

from __future__ import annotations

import pytest

from sosforge_agent.agent import Agent, RateLimiter, format_event
from sosforge_agent.config import Config
from sosforge_agent.proximity import Decision, felt_radius_km, haversine_km, should_notify

# Somewhere concrete to stand: Tokyo.
HOME_LAT, HOME_LON = 35.68, 139.77


def config(**kw) -> Config:
    base = {"lat": HOME_LAT, "lon": HOME_LON, "label": "home", "url": "ws://x/ws"}
    base.update(kw)
    return Config(**base)


def quake(**kw) -> dict:
    event = {
        "id": "usgs:1",
        "kind": "earthquake",
        "severity": "moderate",
        "magnitude": 5.0,
        "depth_km": 20.0,
        "lat": HOME_LAT,
        "lon": HOME_LON,
        "place": "near Tokyo",
    }
    event.update(kw)
    return event


class Spy:
    def __init__(self):
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, title, body, urgent=False):
        self.calls.append((title, body, urgent))
        return True


class TestItStaysQuiet:
    """The failure mode that destroys this product is not a missed alert. It
    is forty alerts, after which the person disables notifications and is then
    protected by nothing at all, believing they are covered."""

    def test_the_backlog_at_startup_never_rings(self):
        """Connecting means receiving up to 300 events at once. Every one of
        them is history. Ringing for them would be the last time this agent
        ever got to ring."""
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle(
            {
                "type": "snapshot",
                "events": [quake(id=f"e:{i}", severity="extreme") for i in range(300)],
            }
        )
        assert spy.calls == []

    def test_a_revision_of_something_already_announced_stays_silent(self):
        """An early warning is re-issued every second or two as the estimate
        sharpens, and USGS revises magnitudes for hours."""
        spy = Spy()
        agent = Agent(config(), spy)
        message = {"type": "event", "event": quake(severity="severe")}
        assert agent.handle(message) is True
        for _ in range(20):
            agent.handle({"type": "update", "event": quake(severity="severe", magnitude=5.4)})
        assert len(spy.calls) == 1

    def test_a_distant_small_quake_says_nothing(self):
        spy = Spy()
        agent = Agent(config(), spy)
        # M3.1 in California, from Tokyo
        agent.handle(
            {"type": "event", "event": quake(magnitude=3.1, lat=37.8, lon=-122.3, place="Berkeley")}
        )
        assert spy.calls == []

    def test_below_the_chosen_severity_says_nothing(self):
        spy = Spy()
        agent = Agent(config(min_severity="severe"), spy)
        agent.handle({"type": "event", "event": quake(severity="moderate")})
        assert spy.calls == []

    def test_the_hourly_ceiling_holds_during_a_swarm(self):
        spy = Spy()
        agent = Agent(config(max_per_hour=3), spy)
        for i in range(40):
            agent.handle({"type": "event", "event": quake(id=f"swarm:{i}", severity="severe")})
        assert len(spy.calls) == 3

    def test_a_tick_is_not_an_event(self):
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle({"type": "tick", "server_time": "2026-08-26T00:00:00Z"})
        assert spy.calls == []


class TestItSpeaksWhenItMust:
    def test_a_quake_under_your_feet_rings(self):
        spy = Spy()
        agent = Agent(config(), spy)
        assert agent.handle({"type": "event", "event": quake(magnitude=5.5)}) is True
        title, body, _ = spy.calls[0]
        assert "M5.5" in title
        assert "0 km from you" in body

    def test_a_big_distant_quake_still_rings_because_a_big_one_reaches(self):
        """The reason a fixed radius is wrong: a M7.8 four hundred kilometres
        away is felt, and on a coast it is the thing you needed to hear."""
        spy = Spy()
        agent = Agent(config(), spy)
        # M7.8 off Honshu, roughly 400 km from Tokyo
        assert (
            agent.handle(
                {
                    "type": "event",
                    "event": quake(magnitude=7.8, severity="extreme", lat=39.0, lon=142.5),
                }
            )
            is True
        )
        assert spy.calls[0][2] is True, "an extreme event must be urgent"

    def test_a_zone_alert_in_your_country_rings_when_asked_for(self):
        """Weather warnings are issued for named zones, not points, and some
        arrive with no geometry anywhere in the payload. Country is then the
        only locator they carry -- useful in a small country, useless in a
        large one, so it is opt-in (see config.zone_alerts)."""
        spy = Spy()
        agent = Agent(config(country_code="JP", zone_alerts=True), spy)
        assert (
            agent.handle(
                {
                    "type": "event",
                    "event": quake(
                        kind="storm",
                        severity="extreme",
                        lat=None,
                        lon=None,
                        magnitude=None,
                        place="Kanto region",
                        country_code="JP",
                    ),
                }
            )
            is True
        )

    def test_a_zone_alert_somewhere_else_does_not(self):
        """This test exists because the rule it guards was WRONG when written.

        The first version said "no coordinates and extreme -> notify". Replayed
        against a real day of the feed from Tokyo, it rang for Saskatoon,
        Chesapeake Bay and Potter County, Texas: severe zone alerts with no
        geometry are common, and a rule with no notion of WHERE cannot be
        anything but a firehose.
        """
        spy = Spy()
        agent = Agent(config(country_code="JP"), spy)
        agent.handle(
            {
                "type": "event",
                "event": quake(
                    kind="storm",
                    severity="extreme",
                    lat=None,
                    lon=None,
                    magnitude=None,
                    place="Potter, TX",
                    country_code="US",
                ),
            }
        )
        assert spy.calls == []

    def test_without_a_declared_country_zone_alerts_stay_silent(self):
        """Refusing to guess: an alert we cannot place, for a user we cannot
        place, is not something to wake anyone for."""
        spy = Spy()
        agent = Agent(config(country_code=None), spy)
        agent.handle(
            {
                "type": "event",
                "event": quake(
                    kind="storm",
                    severity="extreme",
                    lat=None,
                    lon=None,
                    magnitude=None,
                    place="somewhere",
                    country_code="US",
                ),
            }
        )
        assert spy.calls == []

    def test_a_preliminary_solution_says_so(self):
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle({"type": "event", "event": quake(magnitude=6.9, preliminary=True)})
        assert "preliminary" in spy.calls[0][1]

    def test_a_purged_event_can_ring_again_if_it_returns(self):
        """A withdrawn alert that is re-issued is news again."""
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle({"type": "event", "event": quake(severity="severe")})
        agent.handle({"type": "purge", "ids": ["usgs:1"], "reason": "cancelled"})
        agent.handle({"type": "event", "event": quake(severity="severe")})
        assert len(spy.calls) == 2


class TestZoneAlertsAreOffUntilAsked:
    """Measured on one real day of the feed: with them on, Los Angeles would
    have been notified 63 times -- marine warnings in Michigan, Florida and the
    Carolinas -- and Paris 2 genuinely local ones. Severity separates nothing:
    all 63 were "extreme" as well. So the default is silence, and `check`
    prints the reader's own number before they change it."""

    def test_by_default_a_positionless_alert_says_nothing(self):
        spy = Spy()
        agent = Agent(config(country_code="US"), spy)
        agent.handle(
            {
                "type": "event",
                "event": quake(
                    kind="storm",
                    severity="extreme",
                    lat=None,
                    lon=None,
                    magnitude=None,
                    place="Chesapeake Bay",
                    country_code="US",
                ),
            }
        )
        assert spy.calls == []

    def test_positioned_alerts_are_unaffected_by_that_setting(self):
        """The setting must only touch what cannot be placed -- everything with
        coordinates is still judged on distance."""
        spy = Spy()
        agent = Agent(config(country_code="US"), spy)
        assert agent.handle({"type": "event", "event": quake(magnitude=5.5)}) is True


class TestTheDistanceRules:
    def test_the_felt_radius_grows_with_magnitude(self):
        assert felt_radius_km(3.0) < felt_radius_km(5.0) < felt_radius_km(7.0)

    def test_a_micro_quake_reaches_almost_nobody(self):
        assert felt_radius_km(2.5) < 25

    def test_a_great_quake_reaches_across_a_sea(self):
        assert felt_radius_km(8.0) > 900

    def test_a_deep_quake_is_felt_wider(self):
        assert felt_radius_km(6.0, depth_km=300) > felt_radius_km(6.0, depth_km=10)

    def test_the_distance_itself_is_right(self):
        # Tokyo -> Osaka, about 400 km
        assert 390 < haversine_km(35.68, 139.77, 34.69, 135.50) < 420

    def test_the_verdict_carries_its_reason(self):
        decision = should_notify(quake(magnitude=2.0, lat=0.0, lon=0.0), HOME_LAT, HOME_LON)
        assert decision.notify is False
        assert "km" in decision.reason or "threshold" in decision.reason


class TestTheRateLimiter:
    def test_it_forgets_after_an_hour(self):
        limiter = RateLimiter(max_per_hour=2)
        assert limiter.allow(now=0) is True
        assert limiter.allow(now=10) is True
        assert limiter.allow(now=20) is False
        assert limiter.allow(now=3700) is True


class TestTheMessageItself:
    def test_it_names_the_hazard_the_place_and_the_distance(self):
        title, body = format_event(
            quake(magnitude=6.1, place="Off the coast of Honshu"),
            Decision(True, 120.0, "close"),
        )
        assert "M6.1" in title
        assert "Off the coast of Honshu" in body
        assert "120 km from you" in body

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("tsunami", "🌊"), ("volcano", "🌋"), ("wildfire", "🔥"), ("cyclone", "🌀")],
    )
    def test_every_hazard_is_recognisable_before_it_is_read(self, kind, expected):
        title, _ = format_event(quake(kind=kind, magnitude=None), Decision(True, 5.0, "x"))
        assert expected in title
