"""The change log behind `/api/events?since=`: what a polling client could
have held at its cursor, and what happened to it since."""

from __future__ import annotations

from app.changelog import KNOWN, NEW, UNKNOWN, ChangeLog


def msg(
    kind: str, event_id: str, lat: float = 0.0, lon: float = 0.0, severity: str = "moderate"
) -> dict:
    return {
        "type": kind,
        "event": {"id": event_id, "lat": lat, "lon": lon, "severity": severity, "kind": "storm"},
    }


def test_a_tick_is_not_a_change():
    log = ChangeLog()
    log.record({"type": "tick"})
    assert log.head == 0


def test_the_first_entry_after_the_cursor_says_what_the_client_saw():
    log = ChangeLog()
    log.record(msg("event", "a", lat=1))
    cursor = log.cursor()
    log.record(msg("update", "a", lat=2))
    log.record(msg("update", "a", lat=3))
    log.record(msg("event", "b"))
    delta = log.since(cursor)
    assert delta.reset is None
    by_id = {t.event_id: t for t in delta.touched}
    assert by_id["a"].before == KNOWN
    assert by_id["a"].prev is not None and by_id["a"].prev.lat == 1
    assert not by_id["a"].removed
    assert by_id["b"].before == NEW


def test_a_purge_after_the_cursor_is_a_removal_with_its_last_state():
    log = ChangeLog()
    log.record(msg("event", "a", lat=5))
    cursor = log.cursor()
    log.record({"type": "purge", "ids": ["a"], "reason": "cancelled"})
    (touched,) = log.since(cursor).touched
    assert touched.removed and touched.prev is not None and touched.prev.lat == 5


def test_an_update_of_an_id_never_seen_is_unknown_not_new():
    log = ChangeLog()
    cursor = log.cursor()
    log.record(msg("update", "quiet"))
    (touched,) = log.since(cursor).touched
    assert touched.before == UNKNOWN


def test_seeding_makes_restored_events_known():
    from datetime import timedelta

    from app.models.event import Event, utcnow

    log = ChangeLog()
    log.seed(
        [
            Event(
                id="t:1",
                source="t",
                source_id="1",
                time=utcnow() - timedelta(minutes=1),
                lat=7,
                lon=8,
            )
        ]
    )
    cursor = log.cursor()
    log.record(msg("update", "t:1", lat=9))
    (touched,) = log.since(cursor).touched
    assert touched.before == KNOWN and touched.prev.lat == 7


def test_a_cursor_from_another_process_is_a_reset():
    log = ChangeLog()
    other = ChangeLog()
    assert log.since(other.cursor()).reset == "unknown_cursor"
    assert log.since(f"{log.epoch}:99").reset == "unknown_cursor"  # from the future
    assert log.since("garbage").reset == "unknown_cursor"


def test_a_cursor_older_than_the_window_is_expired():
    log = ChangeLog(maxlen=3)
    cursor = log.cursor()
    for i in range(5):
        log.record(msg("event", f"e{i}"))
    assert log.since(cursor).reset == "expired"
    assert log.since(log.cursor()).reset is None


def test_cursor_shape():
    log = ChangeLog()
    assert ChangeLog.well_formed(log.cursor())
    assert not ChangeLog.well_formed("abc")
    assert not ChangeLog.well_formed(f"{log.epoch}:-1")
