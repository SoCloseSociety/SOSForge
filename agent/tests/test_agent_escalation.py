"""The two ways the agent stayed silent when it should have spoken.

1. Escalation. An id was marked announced the moment it was JUDGED, whatever
   the verdict, and nothing announced could ever ring again. The Bonin Islands
   early warning of 2026-10-25 went moderate -> severe -> extreme within 131
   seconds under one EventID: an agent set to `severe` heard nothing, and one
   set to `moderate` heard a M5.9 and never the M7.5.
2. Outages. EVERY snapshot was treated as history, not only the first. A
   snapshot is what the server sends on each reconnect, so an event that
   landed during a 30-second drop arrived in it and was swallowed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sosforge_agent.agent import Agent
from sosforge_agent.config import Config

# Chichijima, Ogasawara: about 210 km from the epicentre of that warning
HOME_LAT, HOME_LON = 27.09, 142.19


class Spy:
    def __init__(self):
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, title, body, urgent=False):
        self.calls.append((title, body, urgent))
        return True


def config(**kw) -> Config:
    base = {"lat": HOME_LAT, "lon": HOME_LON, "label": "home", "url": "ws://x/ws"}
    base.update(kw)
    return Config(**base)


def iso(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def warning(severity: str, magnitude: float, **kw) -> dict:
    event = {
        "id": "jma_eew:20261025111028",
        "kind": "earthquake",
        "severity": severity,
        "magnitude": magnitude,
        "depth_km": 530.0,
        "lat": 27.6,
        "lon": 140.1,
        "place": "小笠原諸島西方沖",
        "time": iso(60),
        "preliminary": True,
    }
    event.update(kw)
    return event


def update(event: dict, escalated: bool) -> dict:
    return {"type": "update", "event": event, "breaking": False, "escalated": escalated}


class TestAnEscalationRingsAgain:
    def test_the_bonin_warning_rings_at_each_step_up(self):
        spy = Spy()
        agent = Agent(config(), spy)

        assert agent.handle({"type": "event", "event": warning("moderate", 5.9)}) is True
        assert agent.handle(update(warning("severe", 6.3), escalated=True)) is True
        assert agent.handle(update(warning("extreme", 7.4), escalated=True)) is True

        assert len(spy.calls) == 3
        assert "M7.4" in spy.calls[-1][0]
        assert spy.calls[-1][2] is True, "the extreme step must be urgent"

    def test_below_the_threshold_first_does_not_mean_silent_forever(self):
        spy = Spy()
        agent = Agent(config(min_severity="severe"), spy)

        assert agent.handle({"type": "event", "event": warning("moderate", 5.9)}) is False
        assert agent.handle(update(warning("severe", 6.3), escalated=True)) is True

    def test_each_rank_rings_once(self):
        """An early warning is re-issued every second or two: twenty serials
        at the same rank are one notification."""
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle({"type": "event", "event": warning("severe", 6.3)})
        for magnitude in (6.4, 6.3, 6.4, 6.5):
            agent.handle(update(warning("severe", magnitude), escalated=False))
        agent.handle(update(warning("extreme", 7.4), escalated=True))
        for magnitude in (7.5, 7.5, 7.4):
            agent.handle(update(warning("extreme", magnitude), escalated=False))
        assert len(spy.calls) == 2

    def test_back_up_to_a_rank_already_announced_is_silent(self):
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle({"type": "event", "event": warning("severe", 6.3)})
        agent.handle(update(warning("moderate", 5.8), escalated=False))
        agent.handle(update(warning("severe", 6.4), escalated=False))
        assert len(spy.calls) == 1

    def test_a_late_upward_revision_the_server_does_not_call_an_escalation_is_silent(self):
        """The server only flags a rise inside its breaking window. USGS
        reviewing a two-hour-old quake upwards is a correction, not news --
        and the agent used to say nothing about it either."""
        spy = Spy()
        agent = Agent(config(min_severity="severe"), spy)
        agent.handle({"type": "event", "event": warning("moderate", 5.9, time=iso(7200))})
        agent.handle(update(warning("severe", 6.3, time=iso(7200)), escalated=False))
        assert spy.calls == []

    def test_an_older_server_without_the_flag_keeps_the_old_silence(self):
        spy = Spy()
        agent = Agent(config(min_severity="severe"), spy)
        agent.handle({"type": "event", "event": warning("moderate", 5.9)})
        agent.handle({"type": "update", "event": warning("severe", 6.3), "breaking": False})
        assert spy.calls == []


class TestAnOutageDoesNotSwallowEvents:
    def history(self) -> dict:
        return {
            "type": "snapshot",
            "server_time": iso(0),
            "events": [warning("extreme", 7.0, id=f"old:{i}", time=iso(3600)) for i in range(5)],
        }

    def test_an_event_that_landed_during_the_outage_rings_on_reconnect(self):
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle(self.history())

        # connection drops; a nearby M6.6 lands; the agent reconnects
        reconnect = self.history()
        reconnect["events"].insert(0, warning("severe", 6.6, id="usgs:outage", time=iso(20)))
        assert agent.handle(reconnect) is True

        assert len(spy.calls) == 1
        assert "M6.6" in spy.calls[0][0]

    def test_the_first_snapshot_is_still_history(self):
        spy = Spy()
        agent = Agent(config(), spy)
        assert agent.handle(self.history()) is False
        assert spy.calls == []

    def test_a_reconnect_does_not_replay_what_was_already_known(self):
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle(self.history())
        agent.handle({"type": "event", "event": warning("severe", 6.3, id="live:1")})
        agent.handle({"type": "event", "event": warning("minor", 3.1, id="live:2")})

        reconnect = self.history()
        reconnect["events"] += [
            warning("severe", 6.3, id="live:1"),
            warning("minor", 3.1, id="live:2"),
        ]
        agent.handle(reconnect)

        assert len(spy.calls) == 1, "a reconnect rang again for something already judged"

    def test_an_escalation_during_the_outage_rings_on_reconnect(self):
        spy = Spy()
        agent = Agent(config(), spy)
        agent.handle(self.history())
        agent.handle({"type": "event", "event": warning("moderate", 5.9, time=iso(60))})

        reconnect = self.history()
        reconnect["events"].insert(0, warning("extreme", 7.5, time=iso(130)))
        assert agent.handle(reconnect) is True
        assert "M7.5" in spy.calls[-1][0]

    def test_an_old_rise_seen_through_a_reconnect_is_a_correction(self):
        spy = Spy()
        agent = Agent(config(min_severity="severe"), spy)
        agent.handle(self.history())
        agent.handle({"type": "event", "event": warning("moderate", 5.9, time=iso(7200))})

        reconnect = self.history()
        reconnect["events"].insert(0, warning("severe", 6.3, time=iso(7200)))
        agent.handle(reconnect)
        assert spy.calls == []

    def test_the_hourly_ceiling_still_holds_after_a_long_outage(self):
        spy = Spy()
        agent = Agent(config(max_per_hour=3), spy)
        agent.handle(self.history())
        reconnect = self.history()
        reconnect["events"] = [
            warning("severe", 6.0, id=f"burst:{i}", time=iso(30)) for i in range(20)
        ]
        agent.handle(reconnect)
        assert len(spy.calls) == 3
