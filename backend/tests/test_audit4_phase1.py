"""Phase 1 -- the store could add and update, but never remove.

The websocket protocol had four messages: `snapshot`, `event`, `update`,
`tick`. Nothing said "this is over". Every defect in this file follows from
that one hole, so every test here is about something DISAPPEARING correctly.

Each test failed before the fix that follows it. That is the point: a test
written after the fix proves the code runs, not that the bug is gone.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.dedupe import Deduper
from app.hub import Client, hub
from app.models.event import Event, Kind, Severity, utcnow
from app.pipeline import Pipeline
from app.store.ring import EventStore


def quake(event_id: str, *, minutes_ago: float = 1.0, lat: float = 10.0, **kw) -> Event:
    return Event(
        id=event_id,
        source=kw.pop("source", "test"),
        source_id=event_id,
        kind=kw.pop("kind", Kind.EARTHQUAKE),
        time=utcnow() - timedelta(minutes=minutes_ago),
        lat=lat,
        lon=20.0,
        magnitude=kw.pop("magnitude", 5.0),
        place="somewhere",
        severity=kw.pop("severity", Severity.MODERATE),
        title="t",
        **kw,
    )


async def drain(client: Client) -> list[dict]:
    import json

    out = []
    while not client.queue.empty():
        out.append(json.loads(client.queue.get_nowait()))
    return out


@pytest.fixture
async def listener():
    """A registered client, i.e. an open tab."""
    client = Client("test-tab")
    await hub.register(client)
    yield client
    await hub.unregister(client)


class TestPurgeReachesOpenTabs:
    """1.1 -- an alert removed server-side stayed on every open map.

    `prune_stale` removed and logged; the browsers were never told. A tab open
    since the morning kept a dissipated cyclone and a lifted tsunami warning on
    screen, indefinitely. That is precisely the product's cardinal rule
    ("the feed must never lie about its own freshness") broken in the one
    direction nobody checks: not stale data shown as fresh, but dead data shown
    as live.
    """

    async def test_a_swept_alert_is_announced_to_the_browsers(self, listener):
        store = EventStore(maxlen=50, persist=False)
        pipeline = Pipeline(store, Deduper())
        dead = quake("test:dissipated", ongoing=True, kind=Kind.CYCLONE, magnitude=None)
        dead.last_seen = utcnow() - timedelta(hours=48)
        store.upsert(dead)
        await drain(listener)

        removed = await pipeline.sweep(max_silence_hours=6.0)

        assert [e.id for e in removed] == ["test:dissipated"]
        messages = await drain(listener)
        purges = [m for m in messages if m["type"] == "purge"]
        assert purges, "the tab was never told the cyclone is gone"
        assert purges[0]["ids"] == ["test:dissipated"]
        assert purges[0]["reason"] == "stale"

    async def test_an_expired_warning_leaves_when_it_expires_not_six_hours_later(self):
        """The NWS publishes an explicit expiry. Honouring it beats guessing.

        A tornado warning that expired twenty minutes ago is over -- waiting
        for six hours of source silence to remove it means six hours of a red
        polygon over a county where nothing is happening.
        """
        store = EventStore(maxlen=50, persist=False)
        expired = quake("nws:expired", kind=Kind.STORM, magnitude=None, ongoing=True)
        expired.expires = utcnow() - timedelta(minutes=20)
        expired.last_seen = utcnow()  # the source still lists it
        store.upsert(expired)

        removed = store.prune_stale(max_silence_hours=6.0)

        assert [e.id for e in removed] == ["nws:expired"]

    async def test_a_running_warning_is_not_purged(self):
        store = EventStore(maxlen=50, persist=False)
        running = quake("nws:running", kind=Kind.STORM, magnitude=None, ongoing=True)
        running.expires = utcnow() + timedelta(hours=2)
        running.last_seen = utcnow()
        store.upsert(running)

        assert store.prune_stale(max_silence_hours=6.0) == []


class TestClusterPromotionIsBroadcast:
    """1.2 -- the promotion happened in memory and nowhere else.

    `_gc` promotes a survivor when a cluster's representative is evicted, so
    the API is right. But `primary_only` clients had already been told the
    survivor was a duplicate, and were never told otherwise: on an open tab the
    quake simply vanished. The fix was correct and invisible.
    """

    async def test_the_promoted_survivor_is_pushed_as_an_update(self, listener):
        store = EventStore(maxlen=3, persist=False)
        pipeline = Pipeline(store, Deduper())
        primary = quake("a:1")
        duplicate = quake("b:1")
        duplicate.cluster_id = "a:1"
        primary.cluster_id = "a:1"
        store.upsert(primary)
        store.upsert(duplicate)
        await drain(listener)

        # the ring holds 3: two more appends evict `a:1` and only `a:1`
        for i in range(2):
            await pipeline.emit(quake(f"c:{i}", lat=40.0 + i))

        assert store.get("b:1") is not None
        assert store.get("b:1").cluster_id == "b:1", "the survivor was not promoted"
        updates = [m for m in await drain(listener) if m["type"] == "update"]
        promoted = [m for m in updates if m["event"]["id"] == "b:1"]
        assert promoted, "the tab still believes b:1 is a duplicate and hides it"
        assert promoted[-1]["primary"] is True
        assert promoted[-1]["breaking"] is False, "a promotion is not breaking news"


class TestCancelledEarlyWarning:
    """1.4 -- a cancelled EEW stayed on the map, red, forever.

    A Japanese early warning is issued on the first seconds of P-wave data.
    False positives happen and JMA cancels them. The source correctly stopped
    republishing the alert -- and stopping to publish is exactly how you say
    nothing at all to a store that can only add.

    This is the worst case in the product: the one alert type that asks people
    to take cover, left standing after being withdrawn.
    """

    def test_a_cancellation_is_reported_not_swallowed(self):
        from app.sources.eew import JmaEewSource

        source = JmaEewSource()
        source.parse_payload(
            {
                "EventID": "20240101120000",
                "Issue": {"Status": "発表", "Source": "気象庁"},
                "OriginTime": "2024-01-01 12:00:00",
                "Hypocenter": "石川県能登地方",
                "Magunitude": 6.1,
                "MaxIntensity": "5-",
                "Title": "緊急地震速報（警報）",
            }
        )
        assert source.retractions == []

        events = source.parse_payload(
            {
                "EventID": "20240101120000",
                "Issue": {"Status": "キャンセル", "Source": "気象庁"},
                "OriginTime": "2024-01-01 12:00:00",
                "Hypocenter": "石川県能登地方",
                "Magunitude": 6.1,
                "MaxIntensity": "5-",
                "Title": "緊急地震速報（キャンセル）",
            }
        )
        assert events == []
        assert source.retractions == ["jma_eew:20240101120000"]

    async def test_a_retraction_removes_the_event_and_tells_the_tabs(self, listener):
        store = EventStore(maxlen=50, persist=False)
        pipeline = Pipeline(store, Deduper())
        await pipeline.emit(quake("jma_eew:x", severity=Severity.SEVERE))
        await drain(listener)

        await pipeline.retract("jma_eew:x", "cancelled")

        assert store.get("jma_eew:x") is None
        purges = [m for m in await drain(listener) if m["type"] == "purge"]
        assert purges and purges[0]["ids"] == ["jma_eew:x"]
        assert purges[0]["reason"] == "cancelled"

    async def test_retracting_something_absent_says_nothing(self, listener):
        """No phantom purge: a tab that never had the event must not be woken."""
        pipeline = Pipeline(EventStore(maxlen=50, persist=False), Deduper())
        await pipeline.retract("jma_eew:never-existed", "cancelled")
        assert [m for m in await drain(listener) if m["type"] == "purge"] == []


class TestOngoingIsDeclaredWhereItIsKnown:
    """1.3 -- two sources publish only live alerts and never said so.

    `/alerts/active` returns, by construction, only alerts in force. The
    tsunami centres publish a bulletin that runs until it is superseded. Both
    left `ongoing` at its default False, so the ingestion horizon treated a
    tornado warning like a three-day-old quake, and the stale sweep -- which
    only touches ongoing events -- never looked at them.
    """

    # Verbatim excerpt of a real /alerts/active feature, captured 2026-08-18.
    # It is here for one reason: `expires` is 2026-08-17T19:15 -- ten hours in
    # the past -- while `ends` is 2026-08-18T05:00. NWS still serves it as
    # active, and it is: the advisory runs, only the MESSAGE announcing it has
    # expired. 82 of the 295 alerts live on the feed that day were in exactly
    # this state.
    SMALL_CRAFT_ADVISORY = {
        "id": (
            "https://api.weather.gov/alerts/urn:oid:"
            "2.49.0.1.840.0.ead3c03d4bb4a8edec57af6a3f0193ccef879286.003.1"
        ),
        "geometry": None,
        "properties": {
            "id": "urn:oid:2.49.0.1.840.0.ead3c03d4bb4a8edec57af6a3f0193ccef879286.003.1",  # noqa: E501
            "areaDesc": "Northern Lynn Canal",
            "sent": "2026-08-17T08:27:00-08:00",
            "effective": "2026-08-17T08:27:00-08:00",
            "onset": "2026-08-17T08:00:00-08:00",
            "expires": "2026-08-17T19:15:00-08:00",
            "ends": "2026-08-18T05:00:00-08:00",
            "status": "Actual",
            "messageType": "Alert",
            "severity": "Minor",
            "certainty": "Likely",
            "urgency": "Expected",
            "event": "Small Craft Advisory",
            "headline": (
                "Small Craft Advisory issued August 17 at 8:27AM AKDT "
                "until August 18 at 5:00AM AKDT by NWS Juneau AK"
            ),
            "senderName": "NWS Juneau AK",
        },
    }

    def test_an_active_nws_alert_is_ongoing_and_carries_its_end(self):
        from app.sources.nws import parse_feature

        event = parse_feature(self.SMALL_CRAFT_ADVISORY)
        assert event is not None
        assert event.ongoing is True
        assert event.expires is not None
        assert event.expires.tzinfo is not None

    def test_the_end_of_the_alert_wins_over_the_validity_of_its_message(self):
        """CAP `expires` is when the MESSAGE stops being valid, not when the
        alert stops. NWS `ends` is the event's end.

        Reading `expires` looks right and is measurably wrong: on the live feed
        of 2026-08-18, 82 of 295 active alerts had an `expires` already in the
        past and an `ends` still in the future. Purging on `expires` would have
        wiped 28% of the running US warnings off the map, advisories included.
        """
        from datetime import datetime

        from app.sources.nws import parse_feature

        event = parse_feature(self.SMALL_CRAFT_ADVISORY)
        assert event is not None
        assert event.expires == datetime.fromisoformat("2026-08-18T05:00:00-08:00")

    def test_without_ends_the_message_validity_is_all_we_have(self):
        """23 of those 295 alerts publish no `ends` at all."""
        from datetime import datetime

        from app.sources.nws import parse_feature

        feature = {
            **self.SMALL_CRAFT_ADVISORY,
            "properties": {**self.SMALL_CRAFT_ADVISORY["properties"], "ends": None},
        }
        event = parse_feature(feature)
        assert event is not None
        assert event.expires == datetime.fromisoformat("2026-08-17T19:15:00-08:00")

    def test_a_tsunami_warning_is_ongoing_but_an_information_bulletin_is_not(self):
        """Fixtures reused verbatim from tests/test_parsers.py, which holds real
        tsunami.gov responses. The category lives in the summary HTML, and an
        invented summary proves nothing about a parser calibrated on the real
        markup -- my first attempt at this test used one, and passed the
        `Warning` case straight into the `information` branch without noticing.
        """
        from test_parsers import TSUNAMI_ATOM, TSUNAMI_WARNING, _first_entry

        from app.sources.tsunami import parse_entry

        warning = parse_entry(_first_entry(TSUNAMI_WARNING), "PHEB")
        assert warning is not None
        assert warning.alert == "warning"
        assert warning.ongoing is True, "a warning in force must be sweepable"

        info = parse_entry(_first_entry(TSUNAMI_ATOM), "PAAQ")
        assert info is not None
        assert info.alert == "information"
        assert info.ongoing is False, "a 'no danger' statement is not an alert in force"


class TestOneBadEventCannotKillABatch:
    """1.6 -- `emit` raising took the whole polling cycle with it.

    Sources loop `for event in events: await emit(event)`. Anything raising
    inside the pipeline -- a broadcast failing, a country lookup on a strange
    string -- aborted the loop and dropped every remaining event of that cycle,
    then counted as a source failure and triggered the backoff. One malformed
    row silenced a source for a minute.
    """

    async def test_a_failing_broadcast_does_not_abort_the_rest(self, monkeypatch):
        store = EventStore(maxlen=50, persist=False)
        pipeline = Pipeline(store, Deduper())
        calls = {"n": 0}

        async def explode(message):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("socket went away mid-broadcast")

        monkeypatch.setattr(hub, "broadcast", explode)

        for i in range(3):
            await pipeline.emit(quake(f"batch:{i}", lat=10.0 + i * 5))

        assert store.get("batch:1") is not None
        assert store.get("batch:2") is not None
        assert pipeline.failed == 1

    async def test_cancellation_is_never_swallowed(self):
        """Shutdown must stay instant: CancelledError is not an error."""
        import asyncio

        pipeline = Pipeline(EventStore(maxlen=50, persist=False), Deduper())

        async def cancel(message):
            raise asyncio.CancelledError

        original = hub.broadcast
        hub.broadcast = cancel  # type: ignore[method-assign]
        try:
            with pytest.raises(asyncio.CancelledError):
                await pipeline.emit(quake("cancel:1"))
        finally:
            hub.broadcast = original  # type: ignore[method-assign]


class TestReplayGoesThroughThePipeline:
    """1.5 -- the journal was written straight into the store.

    `load_backlog` called `store.upsert` directly, so replayed events skipped
    dedup entirely. The deduper's window started empty at every restart: the
    EMSC solution restored from the journal and the USGS solution arriving two
    minutes later were no longer recognised as the same quake. Every restart
    injected a fresh crop of duplicates into the feed.
    """

    async def test_a_restored_event_is_known_to_the_deduper(self, tmp_path):
        import json

        store = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
        deduper = Deduper()
        pipeline = Pipeline(store, deduper)
        journal = tmp_path / "replay.jsonl"
        emsc = quake("emsc:1", minutes_ago=0.5, lat=35.0, magnitude=5.4)
        journal.write_text(json.dumps({"action": "new", **emsc.model_dump(mode="json")}) + "\n")

        assert await pipeline.replay(journal) == 1

        usgs = quake("usgs:1", minutes_ago=0.5, lat=35.02, magnitude=5.5, source="usgs")
        usgs.lon = 20.01
        await pipeline.emit(usgs)

        assert store.get("usgs:1").cluster_id == "emsc:1", (
            "the restored event was invisible to the deduper: a restart makes duplicates"
        )

    async def test_replay_does_not_wake_the_tabs(self, tmp_path, listener):
        import json

        store = EventStore(maxlen=50, data_dir=tmp_path, persist=False)
        pipeline = Pipeline(store, Deduper())
        journal = tmp_path / "replay.jsonl"
        journal.write_text(
            json.dumps({"action": "new", **quake("emsc:2").model_dump(mode="json")}) + "\n"
        )

        await pipeline.replay(journal)

        assert await drain(listener) == []


class TestEvictionStaysCheap:
    """1.7 -- `_gc` scanned the whole buffer on every single insert.

    Once the ring is full -- which is the steady state, permanently, after the
    first hour -- every append triggered a full scan of 5000 events plus a
    5000-entry set build, under the lock. At twenty sources that is a constant
    tax on the one code path that must never be slow.

    A full append only ever evicts ONE event: the one at the head. Knowing
    which one turns the whole thing into a dict pop.
    """

    async def test_the_index_never_outgrows_the_ring(self):
        store = EventStore(maxlen=20, persist=False)
        for i in range(200):
            store.upsert(quake(f"e:{i}", lat=(i % 90) - 45.0))
        assert len(store._by_id) == 20
        assert {e.id for e in store._ring} == set(store._by_id)

    async def test_eviction_does_not_rescan_the_whole_ring(self):
        store = EventStore(maxlen=400, persist=False)
        scans = {"n": 0}
        real_gc = store._promote_orphans

        def counting_gc(*a, **kw):
            scans["n"] += 1
            return real_gc(*a, **kw)

        store._promote_orphans = counting_gc  # type: ignore[method-assign]
        for i in range(1200):
            store.upsert(quake(f"f:{i}", lat=(i % 90) - 45.0))

        # 800 appends past the cap, none of them evicting a cluster primary:
        # not one of them should have needed a full-ring pass.
        assert scans["n"] == 0, f"{scans['n']} full scans for zero orphaned clusters"


class TestAnExtendedWarningLearnsItsNewEnd:
    """Found while verifying phase 1 against the live feed, not from the audit.

    The store had NWS alerts with `expires: None` long after the fix shipped.
    The cause is the content fingerprint: it compares magnitude, position,
    depth, place, severity and the tsunami flag. Not the end of the alert.

    So a warning re-issued with a later `ends` -- which is how NWS extends a
    tornado warning, and it happens constantly -- was seen as identical and the
    old end was kept. The alert would then be purged while still in force. The
    reverse is worse: a warning cut short keeps its original end and stays on
    the map after the danger is gone.
    """

    def test_a_new_end_is_a_revision_not_a_repeat(self):
        store = EventStore(maxlen=50, persist=False)
        first = quake("nws:warning", kind=Kind.STORM, magnitude=None, ongoing=True)
        first.expires = utcnow() + timedelta(minutes=30)
        store.upsert(first)

        extended = quake("nws:warning", kind=Kind.STORM, magnitude=None, ongoing=True)
        extended.expires = first.expires + timedelta(minutes=45)
        stored, action = store.upsert(extended)

        assert action == "update", "an extended warning was taken for a repeat"
        assert stored.expires == extended.expires
        assert store.get("nws:warning").expires == extended.expires

    def test_an_unchanged_alert_is_still_a_repeat(self):
        """The counterpart: re-reading the same alert must stay a noop, or
        every polling cycle would rebroadcast the whole US warning set."""
        store = EventStore(maxlen=50, persist=False)
        alert = quake("nws:steady", kind=Kind.STORM, magnitude=None, ongoing=True)
        alert.expires = utcnow() + timedelta(hours=1)
        store.upsert(alert)

        same = quake("nws:steady", kind=Kind.STORM, magnitude=None, ongoing=True)
        same.expires = alert.expires
        _, action = store.upsert(same)

        assert action == "noop"

    def test_an_alert_that_becomes_ongoing_is_a_revision(self):
        store = EventStore(maxlen=50, persist=False)
        store.upsert(quake("gdacs:1", kind=Kind.CYCLONE, magnitude=None, ongoing=False))
        _, action = store.upsert(quake("gdacs:1", kind=Kind.CYCLONE, magnitude=None, ongoing=True))
        assert action == "update"


class TestAnAlertThatItsSourceDroppedIsOver:
    """Also found against the live store, not in the audit.

    At the time of writing the running instance held 210 severe thunderstorm
    warnings, and not one of them was still in `/alerts/active`. NWS had
    dropped them hours earlier -- they were over. They stayed on the map
    because the sweep only ever looked at events flagged `ongoing`, and those
    210 had been ingested before that flag was set on NWS alerts.

    The flag was the wrong thing to key on. What matters is the NATURE of the
    event. An earthquake is a point in time: its source stops listing it the
    moment it leaves the publication window, and that says nothing about the
    quake. Everything else here is an interval -- a warning, an eruption, a
    fire, a geomagnetic storm -- and a source that stops publishing an interval
    event has said it ended.
    """

    def test_a_warning_no_source_mentions_anymore_is_removed(self):
        store = EventStore(maxlen=50, persist=False)
        # ingested before `ongoing` was set on this source: the flag is False,
        # and the alert is nonetheless over
        dropped = quake("nws:dropped", kind=Kind.STORM, magnitude=None, ongoing=False)
        dropped.last_seen = utcnow() - timedelta(hours=9)
        store.upsert(dropped)

        assert [e.id for e in store.prune_stale(6.0)] == ["nws:dropped"]

    def test_a_quake_is_not_removed_for_going_quiet(self):
        """The distinction that makes the rule safe. A quake that scrolled out
        of the USGS window is still a quake that happened -- removing it would
        cut the history down to a few hours while the UI offers 24."""
        store = EventStore(maxlen=50, persist=False)
        old = quake("usgs:old")
        old.last_seen = utcnow() - timedelta(hours=9)
        store.upsert(old)

        assert store.prune_stale(6.0) == []

    def test_a_swarm_that_stopped_is_still_removed(self):
        """The exception to the exception: swarm and aftershock entries are
        earthquakes by kind but intervals by nature, and they say so with
        `ongoing`. They must keep retiring."""
        store = EventStore(maxlen=50, persist=False)
        swarm = quake("swarm:x", ongoing=True)
        swarm.last_seen = utcnow() - timedelta(hours=9)
        store.upsert(swarm)

        assert [e.id for e in store.prune_stale(6.0)] == ["swarm:x"]
