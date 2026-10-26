"""`/api/events` v2: zone parameters, the `since` cursor for the phone's
15-minute poll, ETag / 304, and `/api/version`."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.event import Severity, utcnow

TOKYO = (35.6762, 139.6503)
YOKOHAMA = (35.4437, 139.6380)
SAPPORO = (43.0618, 141.3545)
LOS_ANGELES = (34.0522, -118.2437)
NEAR_TOKYO = "lat=35.6762&lon=139.6503&radius_km=300"


@pytest.fixture
def client(v2):
    return TestClient(v2.main.app)


def emit(v2, event) -> None:
    asyncio.run(v2.pipeline.emit(event))


class TestLegacy:
    def test_without_new_parameters_the_body_is_unchanged(self, v2, client, ev):
        emit(v2, ev("a", *TOKYO))
        response = client.get("/api/events")
        assert set(response.json()) == {"count", "server_time", "events"}
        # no ETag on the legacy read: a browser revalidating it would hand the
        # page a cached server_time, and every age on screen is computed from it
        assert "etag" not in response.headers


class TestZone:
    def test_lat_lon_radius_filter(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA))
        emit(v2, ev("s", *SAPPORO))
        emit(v2, ev("n", None, None))
        body = client.get(f"/api/events?{NEAR_TOKYO}").json()
        assert [e["id"] for e in body["events"]] == ["test:y"]
        assert body["unplaced"] == {"dropped": 1, "delivered": 0}
        assert body["scope"]["zones"][0]["radius_km"] == 300

    @pytest.mark.parametrize(
        "query",
        [
            "lat=35&lon=139",
            "lat=35&radius_km=10",
            "lat=95&lon=0&radius_km=10",
            "lat=0&lon=0&radius_km=0",
        ],
    )
    def test_an_incomplete_or_invalid_triple_is_a_400_with_a_reason(self, client, query):
        response = client.get(f"/api/events?{query}")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "bad_zones"

    def test_the_websocket_scope_parameters_work_here_too(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA, severity=Severity.SEVERE))
        emit(v2, ev("m", *YOKOHAMA, severity=Severity.MINOR))
        body = client.get("/api/events?zones=35.6762,139.6503,300&min_severity=severe").json()
        assert [e["id"] for e in body["events"]] == ["test:y"]


class TestCursor:
    def full_read(self, client, query=NEAR_TOKYO):
        response = client.get(f"/api/events?{query}&since=")
        assert response.status_code == 200
        body = response.json()
        assert body["reset"] is True and body["reset_reason"] == "no_cursor"
        return body, response.headers["etag"]

    def test_a_revised_event_comes_back_through_the_cursor(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA, severity=Severity.MODERATE))
        body, _ = self.full_read(client)
        assert [e["revision"] for e in body["events"]] == [0]

        emit(v2, ev("y", *YOKOHAMA, severity=Severity.SEVERE))  # a revision
        delta = client.get(f"/api/events?{NEAR_TOKYO}&since={body['cursor']}").json()
        assert delta["reset"] is False
        assert [(e["id"], e["revision"], e["severity"]) for e in delta["events"]] == [
            ("test:y", 1, "severe")
        ]
        assert delta["removed"] == []

    def test_nothing_changed_is_a_304(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA))
        body, _ = self.full_read(client)
        url = f"/api/events?{NEAR_TOKYO}&since={body['cursor']}"
        first = client.get(url)
        assert first.json()["events"] == [] and first.json()["removed"] == []
        # the client's own cursor comes back, so the next poll is the same URL
        assert first.json()["cursor"] == body["cursor"]
        second = client.get(url, headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == first.headers["etag"]
        assert second.headers["cache-control"] == "no-cache"

    def test_a_change_elsewhere_on_the_planet_is_still_a_304(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA))
        body, _ = self.full_read(client)
        url = f"/api/events?{NEAR_TOKYO}&since={body['cursor']}"
        etag = client.get(url).headers["etag"]
        emit(v2, ev("la", *LOS_ANGELES))
        emit(v2, ev("la", *LOS_ANGELES, severity=Severity.EXTREME))
        assert client.get(url, headers={"If-None-Match": etag}).status_code == 304

    def test_a_revision_breaks_the_304(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA))
        body, _ = self.full_read(client)
        url = f"/api/events?{NEAR_TOKYO}&since={body['cursor']}"
        etag = client.get(url).headers["etag"]
        emit(v2, ev("y", *YOKOHAMA, severity=Severity.EXTREME))
        response = client.get(url, headers={"If-None-Match": etag})
        assert response.status_code == 200
        assert [e["id"] for e in response.json()["events"]] == ["test:y"]

    def test_a_full_read_unchanged_is_a_304_too(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA))
        _, etag = self.full_read(client)
        response = client.get(
            f"/api/events?{NEAR_TOKYO}&since=", headers={"If-None-Match": f"W/{etag}"}
        )
        assert response.status_code == 304

    def test_an_event_relocated_out_of_the_zone_is_a_removal(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA))
        body, _ = self.full_read(client)
        emit(v2, ev("y", *SAPPORO))
        delta = client.get(f"/api/events?{NEAR_TOKYO}&since={body['cursor']}").json()
        assert delta["events"] == [] and delta["removed"] == ["test:y"]

    def test_a_cancelled_event_is_a_removal(self, v2, client, ev):
        emit(v2, ev("eew", *YOKOHAMA))
        body, _ = self.full_read(client)
        asyncio.run(v2.pipeline.retract("test:eew", "cancelled"))
        delta = client.get(f"/api/events?{NEAR_TOKYO}&since={body['cursor']}").json()
        assert delta["removed"] == ["test:eew"]

    def test_an_alert_that_comes_back_expired_is_a_removal(self, v2, client, ev):
        # Lanes B and F meet here: the pipeline now retracts a stored alert
        # when its source re-lists it past `expires` (audit 6, #6). The phone's
        # background poll must hear about it, or its notification stays up.
        emit(v2, ev("adv", *YOKOHAMA, expires=utcnow() + timedelta(hours=1)))
        body, _ = self.full_read(client)
        emit(v2, ev("adv", *YOKOHAMA, expires=utcnow() - timedelta(minutes=1)))
        delta = client.get(f"/api/events?{NEAR_TOKYO}&since={body['cursor']}").json()
        assert delta["events"] == [] and delta["removed"] == ["test:adv"]

    def test_changes_the_client_never_held_are_not_listed_as_removals(self, v2, client, ev):
        body, _ = self.full_read(client)
        emit(v2, ev("la", *LOS_ANGELES))
        emit(v2, ev("la", *LOS_ANGELES, severity=Severity.SEVERE))
        asyncio.run(v2.pipeline.retract("test:la", "cancelled"))
        delta = client.get(f"/api/events?{NEAR_TOKYO}&since={body['cursor']}").json()
        assert delta["events"] == [] and delta["removed"] == []

    def test_an_event_moving_into_the_zone_is_an_upsert(self, v2, client, ev):
        emit(v2, ev("m", *SAPPORO))
        body, _ = self.full_read(client)
        assert body["events"] == []
        emit(v2, ev("m", *YOKOHAMA))
        delta = client.get(f"/api/events?{NEAR_TOKYO}&since={body['cursor']}").json()
        assert [e["id"] for e in delta["events"]] == ["test:m"]

    def test_too_many_changes_is_a_reset_not_a_truncated_delta(self, v2, client, ev):
        body, _ = self.full_read(client, "limit=1&min_severity=info")
        emit(v2, ev("a", *TOKYO))
        emit(v2, ev("b", *SAPPORO))
        delta = client.get(f"/api/events?limit=1&min_severity=info&since={body['cursor']}").json()
        assert delta["reset"] is True and delta["reset_reason"] == "too_many_changes"
        assert len(delta["events"]) == 1

    def test_a_cursor_from_a_previous_process_is_a_reset(self, v2, client, ev):
        emit(v2, ev("y", *YOKOHAMA))
        body = client.get(f"/api/events?{NEAR_TOKYO}&since=00000000:5").json()
        assert body["reset"] is True and body["reset_reason"] == "unknown_cursor"
        assert [e["id"] for e in body["events"]] == ["test:y"]

    def test_a_malformed_cursor_is_refused(self, client):
        response = client.get("/api/events?since=not-a-cursor")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "bad_since"

    def test_the_cursor_works_without_a_zone(self, v2, client, ev):
        body = client.get("/api/events?since=").json()
        emit(v2, ev("x", *LOS_ANGELES))
        delta = client.get(f"/api/events?since={body['cursor']}").json()
        assert [e["id"] for e in delta["events"]] == ["test:x"]
        assert delta["scope"] is None


def test_version_lists_the_v2_features(client):
    body = client.get("/api/version").json()
    assert body["api"] == 2
    for feature in ("events_cursor", "ws_zones", "guidance", "escalated", "snapshot_pinned"):
        assert feature in body["features"]
