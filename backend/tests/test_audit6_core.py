"""Sixth audit, critical core -- four ways the feed misreported its own state.

Every case here was reproduced on the production journal of 2026-10-20..26
before it was fixed:

* the Bonin Islands early warning of 2026-10-25 (`jma_eew:20261025111028`)
  went moderate -> severe -> extreme within 131 seconds. Every step after the
  first arrived as an UPDATE, and only a NEW event could ever be `breaking`:
  the escalation to a M7.5 never sounded anywhere;
* two JMA tsunami forecasts lifted weeks earlier were re-ingested as NEW 3914
  times in seven days -- every one of them already expired when it was
  written. The sweep purged them on `expires`, the next poll re-added them;
* a retraction (a cancelled early warning) was never journaled, so every
  restart replayed the withdrawn event back onto the map;
* the tsunami counter only counted events DATED in the last hour, and a JMA
  advisory is dated when it is issued and stays in force for hours.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app import pipeline as pipeline_module
from app.dedupe import Deduper
from app.models.event import Event, Kind, Severity, utcnow
from app.pipeline import Pipeline
from app.store.ring import EventStore


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    messages: list[dict] = []

    async def capture(message: dict) -> None:
        messages.append(message)

    monkeypatch.setattr(pipeline_module.hub, "broadcast", capture)
    return messages


def eew(severity: Severity, magnitude: float, age_s: float, depth: float, **kw) -> Event:
    """The real sequence of `jma_eew:20261025111028`, re-dated to now."""
    base = {
        "id": "jma_eew:20261025111028",
        "source": "jma_eew",
        "source_id": "20261025111028",
        "kind": Kind.EARTHQUAKE,
        "time": utcnow() - timedelta(seconds=age_s),
        "lat": 27.6,
        "lon": 140.1,
        "depth_km": depth,
        "magnitude": magnitude,
        "place": "小笠原諸島西方沖",
        "country": "Japan",
        "severity": severity,
        "preliminary": True,
        "title": "Early warning -- 小笠原諸島西方沖 (shindo 不明)",
    }
    base.update(kw)
    return Event(**base)


def alert(event_id: str, **kw) -> Event:
    base = {
        "id": event_id,
        "source": event_id.split(":", 1)[0],
        "source_id": event_id.split(":", 1)[1],
        "kind": Kind.STORM,
        "time": utcnow() - timedelta(minutes=5),
        "place": "somewhere",
        "severity": Severity.SEVERE,
        "ongoing": True,
        "title": "t",
    }
    base.update(kw)
    return Event(**base)


def updates(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["type"] in ("event", "update")]


# ------------------------------------------------------------------ escalation


class TestAnEscalationSounds:
    """`breaking` is reserved for NEW events, and that is right: it is what
    stops a hundred old GDACS alerts from ringing on the first cycle (lesson
    4). But an early warning keeps its EventID across serials, so the moment
    that matters most -- the estimate jumping to a great quake -- is an
    update. Clients sound on `breaking || escalated`."""

    async def test_the_bonin_warning_escalation_is_flagged(self, sent):
        pipeline = Pipeline(EventStore(persist=False), Deduper())

        await pipeline.emit(eew(Severity.MODERATE, 5.9, 20, 10.0))
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 106, 500.0))
        await pipeline.emit(eew(Severity.EXTREME, 7.4, 131, 530.0))

        messages = updates(sent)
        assert [m["type"] for m in messages] == ["event", "update", "update"]
        assert [m["breaking"] for m in messages] == [True, False, False]
        assert [m["escalated"] for m in messages] == [False, True, True], (
            "the warning reached M7.4 and nothing sounded"
        )

    async def test_a_revision_at_the_same_rank_is_not_an_escalation(self, sent):
        pipeline = Pipeline(EventStore(persist=False), Deduper())
        await pipeline.emit(eew(Severity.EXTREME, 7.4, 131, 530.0))
        await pipeline.emit(eew(Severity.EXTREME, 7.5, 152, 530.0))
        assert [m["escalated"] for m in updates(sent)] == [False, False]

    async def test_a_downgrade_is_not_an_escalation(self, sent):
        pipeline = Pipeline(EventStore(persist=False), Deduper())
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 60, 500.0))
        await pipeline.emit(eew(Severity.MODERATE, 5.8, 90, 240.0))
        assert updates(sent)[-1]["escalated"] is False

    async def test_coming_back_up_to_a_rank_already_held_is_not_news(self, sent):
        """Measured over seven days: The Geysers swarm crossed the 30-quake
        line, fell back, and crossed it again. Compared with the PREVIOUS
        version, every crossing is an escalation: nine alarms for one swarm.
        Compared with the highest rank the event has held: two, and the second
        only because its entry had been purged and re-created in between."""
        pipeline = Pipeline(EventStore(persist=False), Deduper())
        await pipeline.emit(eew(Severity.MODERATE, 5.9, 20, 10.0))
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 40, 500.0))
        await pipeline.emit(eew(Severity.MODERATE, 5.8, 60, 240.0))
        await pipeline.emit(eew(Severity.SEVERE, 6.4, 80, 540.0))
        assert [m["escalated"] for m in updates(sent)] == [False, True, False, False]

    async def test_a_late_revision_of_an_old_event_does_not_ring(self, sent):
        """The same window as `breaking`: USGS reviewing a two-hour-old quake
        upwards is a correction, not an alarm."""
        pipeline = Pipeline(EventStore(persist=False), Deduper())
        await pipeline.emit(eew(Severity.MODERATE, 5.9, 7200, 10.0))
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 7200, 10.0))
        assert updates(sent)[-1]["escalated"] is False

    async def test_a_tsunami_flag_turning_on_is_an_escalation(self, sent):
        pipeline = Pipeline(EventStore(persist=False), Deduper())
        quake = eew(Severity.EXTREME, 7.5, 60, 20.0)
        await pipeline.emit(quake)
        await pipeline.emit(eew(Severity.EXTREME, 7.5, 61, 20.0, tsunami=True))
        assert updates(sent)[-1]["escalated"] is True

    async def test_the_highest_rank_survives_a_restart(self, sent, tmp_path):
        """The journal replays every version, so the peak is rebuilt: a live
        revision after a deploy compares against what was already announced,
        not against a blank slate."""
        first = EventStore(data_dir=tmp_path)
        pipeline = Pipeline(first, Deduper())
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 40, 500.0))
        await pipeline.emit(eew(Severity.MODERATE, 5.8, 60, 240.0))

        restarted = Pipeline(EventStore(data_dir=tmp_path), Deduper())
        for journal in sorted(tmp_path.glob("events-*.jsonl")):
            await restarted.replay(journal)
        sent.clear()
        await restarted.emit(eew(Severity.SEVERE, 6.4, 80, 540.0))
        assert updates(sent)[-1]["escalated"] is False


# --------------------------------------------------------------------- expiry


class TestAnExpiredAlertStaysGone:
    # Verbatim from the production journal (events-2026-10-25.jsonl), `raw`
    # trimmed: lifted weeks before, re-ingested as NEW every five minutes.
    JMA_FORECAST = {
        "id": "jma-tsunami:20261010025608",
        "source": "jma-tsunami",
        "source_id": "20261010025608",
        "kind": "tsunami",
        "time": "2026-10-09T18:37:00Z",
        "lat": 7.5,
        "lon": -80.8,
        "depth_km": None,
        "magnitude": 8.0,
        "mag_type": "Mj",
        "place": "Japan",
        "country": "Japan",
        "country_code": "JP",
        "severity": "info",
        "ongoing": True,
        "expires": "2026-10-10T06:37:00Z",
        "tsunami": True,
        "alert": "tsunami forecast",
        "title": "JMA tsunami forecast for Japan -- epicentre Central America (M 8.0)",
        "url": "https://www.jma.go.jp/bosai/map.html#contents=tsunami",
    }

    async def test_an_alert_already_expired_is_never_ingested(self, sent):
        store = EventStore(persist=False)
        pipeline = Pipeline(store, Deduper())

        await pipeline.emit(Event.model_validate(self.JMA_FORECAST))

        assert store.get(self.JMA_FORECAST["id"]) is None
        assert pipeline.dropped == 1
        assert pipeline.ingested == 0
        assert updates(sent) == []

    async def test_it_no_longer_flaps_between_sweep_and_poll(self, sent):
        """The production loop: poll -> new, sweep -> purge, poll -> new."""
        store = EventStore(persist=False)
        pipeline = Pipeline(store, Deduper())
        for _ in range(3):
            await pipeline.emit(Event.model_validate(self.JMA_FORECAST))
            await pipeline.sweep(6.0)
        assert pipeline.ingested == 0
        assert [m["type"] for m in sent] == []

    async def test_a_revision_that_expires_withdraws_the_stored_alert(self, sent):
        store = EventStore(persist=False)
        pipeline = Pipeline(store, Deduper())
        await pipeline.emit(alert("nws:1", expires=utcnow() + timedelta(hours=1)))

        await pipeline.emit(alert("nws:1", expires=utcnow() - timedelta(minutes=1)))

        assert store.get("nws:1") is None
        assert sent[-1] == {"type": "purge", "ids": ["nws:1"], "reason": "expired"}

    async def test_an_alert_still_in_force_is_untouched(self, sent):
        store = EventStore(persist=False)
        pipeline = Pipeline(store, Deduper())
        await pipeline.emit(alert("nws:2", expires=utcnow() + timedelta(minutes=1)))
        assert store.get("nws:2") is not None


# ------------------------------------------------------------ journal removals


class TestAWithdrawnEventStaysWithdrawnAcrossARestart:
    async def replayed(self, tmp_path) -> EventStore:
        store = EventStore(data_dir=tmp_path)
        pipeline = Pipeline(store, Deduper())
        for journal in sorted(tmp_path.glob("events-*.jsonl")):
            await pipeline.replay(journal)
        return store

    async def test_a_cancelled_early_warning_does_not_come_back(self, sent, tmp_path):
        pipeline = Pipeline(EventStore(data_dir=tmp_path), Deduper())
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 30, 10.0))
        await pipeline.retract("jma_eew:20261025111028", "cancelled")

        store = await self.replayed(tmp_path)

        assert store.get("jma_eew:20261025111028") is None, (
            "the cancelled warning was resurrected by the restart"
        )

    async def test_the_removal_line_says_what_and_why(self, tmp_path):
        pipeline = Pipeline(EventStore(data_dir=tmp_path), Deduper())
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 30, 10.0))
        await pipeline.retract("jma_eew:20261025111028", "cancelled")

        lines = [json.loads(line) for p in tmp_path.glob("*.jsonl") for line in p.open()]
        removal = lines[-1]
        assert removal["action"] == "remove"
        assert removal["id"] == "jma_eew:20261025111028"
        assert removal["reason"] == "cancelled"

    async def test_an_event_issued_again_after_removal_comes_back(self, sent, tmp_path):
        """Order matters: a removal only cancels what came BEFORE it."""
        pipeline = Pipeline(EventStore(data_dir=tmp_path), Deduper())
        await pipeline.emit(alert("jma-tsunami:1", expires=utcnow() + timedelta(hours=9)))
        await pipeline.retract("jma-tsunami:1", "lifted")
        await pipeline.emit(alert("jma-tsunami:1", expires=utcnow() + timedelta(hours=9)))

        store = await self.replayed(tmp_path)
        assert store.get("jma-tsunami:1") is not None

    async def test_a_swept_alert_does_not_come_back_either(self, sent, tmp_path):
        store = EventStore(data_dir=tmp_path)
        pipeline = Pipeline(store, Deduper())
        await pipeline.emit(alert("meteoalarm:x"))
        stored = store.get("meteoalarm:x")
        assert stored is not None
        stored.last_seen = utcnow() - timedelta(hours=7)
        assert [e.id for e in await pipeline.sweep(6.0)] == ["meteoalarm:x"]

        assert (await self.replayed(tmp_path)).get("meteoalarm:x") is None

    async def test_a_journal_from_before_this_change_still_loads(self, sent, tmp_path):
        old = alert("gdacs:TC1", kind=Kind.CYCLONE)
        line = json.dumps({"action": "new", **old.model_dump(mode="json")})
        (tmp_path / f"events-{utcnow():%Y-%m-%d}.jsonl").write_text(line + "\n")

        assert (await self.replayed(tmp_path)).get("gdacs:TC1") is not None

    async def test_a_replay_does_not_journal_its_own_removals(self, sent, tmp_path):
        pipeline = Pipeline(EventStore(data_dir=tmp_path), Deduper())
        await pipeline.emit(eew(Severity.SEVERE, 6.3, 30, 10.0))
        await pipeline.retract("jma_eew:20261025111028", "cancelled")
        before = sum(1 for p in tmp_path.glob("*.jsonl") for _ in p.open())

        await self.replayed(tmp_path)

        assert sum(1 for p in tmp_path.glob("*.jsonl") for _ in p.open()) == before


# ------------------------------------------------------------- tsunami counter


class TestTheTsunamiCounterCountsWhatIsInForce:
    def test_an_advisory_issued_hours_ago_is_still_active(self):
        store = EventStore(persist=False)
        store.upsert(
            alert(
                "jma-tsunami:20261101",
                kind=Kind.TSUNAMI,
                tsunami=True,
                severity=Severity.MODERATE,
                time=utcnow() - timedelta(hours=3),
                expires=utcnow() + timedelta(hours=9),
            )
        )
        assert store.stats()["tsunami_active"] == 1, "a tsunami advisory in force counted as zero"

    def test_an_expired_advisory_is_not(self):
        store = EventStore(persist=False)
        store.upsert(
            alert(
                "jma-tsunami:20261102",
                kind=Kind.TSUNAMI,
                tsunami=True,
                time=utcnow() - timedelta(minutes=30),
                expires=utcnow() - timedelta(minutes=1),
            )
        )
        assert store.stats()["tsunami_active"] == 0

    def test_a_recent_point_in_time_statement_still_counts(self):
        """BMKG's "berpotensi tsunami" rides on the quake itself, which is not
        an ongoing alert: it kept counting for its first hour, and still does."""
        store = EventStore(persist=False)
        store.upsert(
            alert(
                "bmkg:1",
                kind=Kind.EARTHQUAKE,
                tsunami=True,
                ongoing=False,
                time=utcnow() - timedelta(minutes=10),
            )
        )
        assert store.stats()["tsunami_active"] == 1

    def test_an_old_point_in_time_statement_does_not(self):
        store = EventStore(persist=False)
        store.upsert(
            alert(
                "bmkg:2",
                kind=Kind.EARTHQUAKE,
                tsunami=True,
                ongoing=False,
                time=utcnow() - timedelta(hours=3),
            )
        )
        assert store.stats()["tsunami_active"] == 0
