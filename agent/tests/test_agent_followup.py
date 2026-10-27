"""What the agent says AFTER it has spoken, and how it measures an area.

Both found by lane N's drills with the real agent and its notifier recorded
(2026-10-27):

* a JMA early warning for Tokyo rang twice (M6.1, then M6.6, "take cover")
  and was then CANCELLED. The agent dropped the id and said nothing: the
  notification telling someone to take cover stood with no follow-up. Same
  for a quake near Tokyo relocated 1000 km away by the agency's revision.
* the agent judged an area warning by its CENTRE only. A UK amber warning
  whose drawn area reaches 232 km from Paris stayed silent on a Paris agent
  with a 300 km reach, while the zoned app (backend/app/scope.py,
  `Zone.touches`) got it.
"""

from __future__ import annotations

from sosforge_agent.agent import Agent, format_event
from sosforge_agent.config import Config
from sosforge_agent.proximity import distance_to_box_km, haversine_km, should_notify

TOKYO = (35.6762, 139.6503)
PARIS = (48.8566, 2.3522)
# the drill's UK amber area (05-relocated-out), west, south, east, north
UK_AREA = (-3.2, 50.4, 0.27, 52.0)


class Recorder:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, bool]] = []

    def __call__(self, title: str, body: str, *, urgent: bool = False) -> bool:
        self.notes.append((title, body, urgent))
        return True


def agent_at(lat: float, lon: float, **kw) -> tuple[Agent, Recorder]:
    recorder = Recorder()
    config = Config(lat=lat, lon=lon, url="ws://x/ws", **kw)
    agent = Agent(config, notifier=recorder)
    agent.handle({"type": "snapshot", "events": [], "server_time": None})
    return agent, recorder


def eew(**kw) -> dict:
    event = {
        "id": "jma_eew:1",
        "kind": "earthquake",
        "severity": "severe",
        "magnitude": 6.1,
        "depth_km": 10.0,
        "lat": 35.9,
        "lon": 140.1,
        "place": "Ibaraki-ken Nambu",
        "preliminary": True,
    }
    event.update(kw)
    return event


class TestAWithdrawnAlertIsFollowedUp:
    def test_a_cancelled_early_warning_is_said_to_be_cancelled_once(self):
        agent, said = agent_at(*TOKYO)
        agent.handle({"type": "event", "event": eew(), "breaking": True})
        agent.handle(
            {
                "type": "update",
                "event": eew(severity="extreme", magnitude=6.6),
                "escalated": True,
            }
        )
        assert len(said.notes) == 2

        agent.handle({"type": "purge", "ids": ["jma_eew:1"], "reason": "cancelled"})
        agent.handle({"type": "purge", "ids": ["jma_eew:1"], "reason": "cancelled"})

        assert len(said.notes) == 3, "the take-cover notification stood with no follow-up"
        title, _body, urgent = said.notes[2]
        assert title.startswith("Cancelled")
        assert "M6.6" in title, "the follow-up must name what it cancels"
        assert not urgent, "a cancellation is good news: no alarm sound"

    def test_each_reason_has_its_own_word(self):
        words = {}
        for reason in ("cancelled", "lifted", "expired", "stale", "out_of_scope", "other"):
            agent, said = agent_at(*TOKYO)
            agent.handle({"type": "event", "event": eew(), "breaking": True})
            agent.handle({"type": "purge", "ids": ["jma_eew:1"], "reason": reason})
            words[reason] = said.notes[-1][0].split(" -- ")[0]
        assert words == {
            "cancelled": "Cancelled",
            "lifted": "Lifted",
            "expired": "Ended",
            "stale": "Ended",
            "out_of_scope": "No longer near you",
            "other": "Withdrawn",
        }

    def test_a_purge_of_something_never_said_stays_silent(self):
        agent, said = agent_at(*TOKYO)
        far = eew(id="usgs:far", lat=-33.4, lon=-70.6, place="Santiago")
        agent.handle({"type": "event", "event": far, "breaking": True})
        agent.handle({"type": "purge", "ids": ["usgs:far", "nws:x"], "reason": "cancelled"})
        assert said.notes == []

    def test_a_quake_relocated_out_of_reach_is_followed_up(self):
        agent, said = agent_at(*TOKYO)
        agent.handle({"type": "event", "event": eew(severity="moderate", magnitude=5.9)})
        moved = eew(severity="moderate", magnitude=5.9, lat=44.5, lon=147.0)
        agent.handle({"type": "update", "event": moved, "escalated": False})
        agent.handle({"type": "update", "event": moved, "escalated": False})

        assert len(said.notes) == 2
        title, body, _ = said.notes[1]
        assert title.startswith("No longer near you")
        assert "km away" in body

    def test_a_revision_still_in_reach_stays_silent(self):
        agent, said = agent_at(*TOKYO)
        agent.handle({"type": "event", "event": eew()})
        agent.handle({"type": "update", "event": eew(magnitude=6.2), "escalated": False})
        assert len(said.notes) == 1

    def test_a_downgrade_below_the_threshold_is_followed_up(self):
        agent, said = agent_at(*TOKYO)
        agent.handle({"type": "event", "event": eew()})
        agent.handle(
            {"type": "update", "event": eew(severity="minor", magnitude=3.9), "escalated": False}
        )
        assert said.notes[-1][0].startswith("Downgraded")


class TestAnAreaIsMeasuredByItsArea:
    def test_the_distance_to_a_box_is_zero_inside_it(self):
        assert distance_to_box_km(51.5, -0.12, UK_AREA) == 0.0

    def test_the_distance_to_a_box_is_to_its_nearest_point(self):
        # Paris to the box's south-east corner region, as the server computes it
        assert 225 < distance_to_box_km(*PARIS, UK_AREA) < 240
        assert haversine_km(*PARIS, 51.2, -1.5) > 300  # the centre is beyond reach

    def test_the_box_wraps_at_the_antimeridian(self):
        # Fiji-Tonga warning drawn across 180: west > east
        box = (177.0, -21.0, -175.0, -15.0)
        assert distance_to_box_km(-18.0, 179.5, box) == 0.0
        assert distance_to_box_km(-18.0, -179.5, box) == 0.0
        assert distance_to_box_km(-18.0, 170.0, box) > 600

    def test_a_uk_amber_area_in_reach_of_paris_is_said(self):
        warning = {
            "id": "meteoalarm:GB_u1",
            "kind": "storm",
            "severity": "moderate",
            "lat": 51.2,
            "lon": -1.5,
            "bbox": list(UK_AREA),
            "place": "South East England",
        }
        decision = should_notify(warning, *PARIS, max_distance_km=300)
        assert decision.notify, decision.reason
        _head, body = format_event(warning, decision)
        assert "area" in body

    def test_an_area_with_no_point_is_still_placed_by_its_box(self):
        warning = {
            "id": "wmo:1",
            "kind": "flood",
            "severity": "severe",
            "bbox": [2.0, 48.5, 2.8, 49.0],
        }
        decision = should_notify(warning, *PARIS, home_country=None)
        assert decision.notify, decision.reason

    def test_a_malformed_box_falls_back_to_the_point(self):
        warning = {"id": "x:1", "kind": "storm", "severity": "severe", "lat": 48.9, "lon": 2.3}
        for bad in ([1, 2, 3], ["a", 1, 2, 3], None, "x"):
            assert should_notify({**warning, "bbox": bad}, *PARIS).notify
