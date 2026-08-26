"""Fifth audit -- what a hostile or compromised public feed can do to us.

This product renders text and links coming from nineteen feeds it does not
control. None of those feeds is malicious today. That is not a security
property: it is a description of the weather. The question this file answers
is what happens the day one of them is wrong, and the answer must not depend
on a header configured on one server outside this repository.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.event import Event, Kind, Severity, utcnow


def event_with_url(url: str | None) -> Event:
    return Event(
        id="test:1",
        source="test",
        source_id="1",
        kind=Kind.EARTHQUAKE,
        time=utcnow() - timedelta(minutes=1),
        lat=10.0,
        lon=20.0,
        magnitude=5.0,
        place="somewhere",
        severity=Severity.MODERATE,
        title="t",
        url=url,
    )


class TestOnlyRealLinksSurvive:
    """A feed's `url` ends up in an `href` -- in the map popup and in the
    detail panel. Escaping protects the ATTRIBUTE, not the SCHEME: a value of
    `javascript:...` passes every escape untouched and becomes a clickable
    link that runs as the page.

    The live site's Content-Security-Policy blocks it today. That CSP lives in
    an nginx configuration on one VPS, outside this repository: any redeploy
    from this repo, any second host, any container run on its own has no such
    protection. A defence that only exists on one machine is not a defence.
    """

    @pytest.mark.parametrize(
        "hostile",
        [
            "javascript:alert(document.cookie)",
            "JavaScript:alert(1)",
            "  javascript:alert(1)",
            "java\tscript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "blob:https://sosforge.soclose.co/abcd",
        ],
    )
    def test_a_dangerous_scheme_is_dropped(self, hostile: str):
        assert event_with_url(hostile).url is None, f"{hostile!r} reached the page"

    @pytest.mark.parametrize(
        "legitimate",
        [
            "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd",
            "http://www.jma.go.jp/bosai/map.html",
            "https://www.tsunami.gov/events/PAAQ/2026/08/13/tjpse5/1/WEAK53/PAAQCAP.xml",
        ],
    )
    def test_a_real_bulletin_link_is_kept(self, legitimate: str):
        assert event_with_url(legitimate).url == legitimate

    def test_no_url_stays_no_url(self):
        assert event_with_url(None).url is None

    def test_a_relative_link_is_dropped_too(self):
        """Nothing in this product serves its own pages from a feed value, so
        a relative URL is either a bug or an attempt."""
        assert event_with_url("//evil.example/x").url is None
        assert event_with_url("/admin").url is None


class TestTheRawFeedPayloadStaysServerSide:
    """`/api/events/{id}` used to return `event.raw`: the verbatim object the
    source sent, unbounded and unsanitised.

    It is the one place where feed-controlled data reaches a client WITHOUT
    passing through normalisation -- every guarantee the pipeline offers is
    bypassed. It is also unbounded: a GDACS event carries every episode, a
    JMA list every entry.
    """

    def test_the_endpoint_does_not_serve_the_verbatim_source_object(self):
        from fastapi.testclient import TestClient

        from app.main import app, store

        event = event_with_url("https://example.org/x")
        event.raw = {"secret_looking": "x" * 5000, "nested": {"payload": "<script>"}}
        store.upsert(event)

        with TestClient(app) as client:
            body = client.get(f"/api/events/{event.id}").json()

        assert body["found"] is True
        assert "raw" not in body, "the verbatim source payload is still exposed"
        assert body["event"]["id"] == event.id
