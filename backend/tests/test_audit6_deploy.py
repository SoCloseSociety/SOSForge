"""Sixth audit -- the per-address websocket cap was a cap on the whole site.

`ConnectionQuota` keys on `ws.client.host`. In production that host is the
web container's nginx, for every visitor: the container nginx sent no
X-Forwarded-For on `/ws`, and uvicorn trusted forwarded headers from
127.0.0.1 only (its default), never from the compose network. So the
"24 connections per address" ceiling meant 24 live tabs for the entire
planet, and the 25th visitor got a 1013.

These tests read the deployment files themselves, so the fix cannot drift
away from what is actually shipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import uvicorn
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "deploy" / "nginx.conf"
DOCKERFILE = ROOT / "backend" / "Dockerfile"

# The production compose network, measured on the helper VPS (2026-10-26):
# sosforge_default = 172.28.0.0/16, web container 172.28.0.3, gateway
# 172.28.0.1 (the address the host nginx reaches the published port from).
WEB_CONTAINER = "172.28.0.3"
GATEWAY = "172.28.0.1"


def location(name: str) -> str:
    text = NGINX.read_text()
    start = text.index(f"location {name} {{")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"unterminated location {name}")


def directives(block: str) -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in block.splitlines())
    return [" ".join(line.split()) for line in lines if line]


@pytest.mark.parametrize("path", ["/ws", "/api/"])
def test_the_container_nginx_forwards_the_client_address(path):
    block = directives(location(path))
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in block
    assert "proxy_set_header X-Real-IP $remote_addr;" in block


def forwarded_allow_ips() -> str | None:
    match = re.search(r'FORWARDED_ALLOW_IPS="([^"]+)"', DOCKERFILE.read_text())
    return match.group(1) if match else None


def test_uvicorn_trusts_the_compose_network_and_nothing_wider():
    trusted = forwarded_allow_ips()
    assert trusted, "the image does not tell uvicorn which proxy to believe"
    assert "*" not in trusted, "trusting everyone lets any client pick its own address"
    cmd = next(line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD"))
    assert '"--proxy-headers"' in cmd


class TestTheQuotaKeysOnTheVisitor:
    @pytest.fixture
    def client(self, monkeypatch) -> TestClient:
        """The app exactly as the image serves it: uvicorn's own wiring, with
        the trust list the Dockerfile sets, reached from the web container."""
        from app import main
        from app.hub import ConnectionQuota

        monkeypatch.setattr(main, "ws_quota", ConnectionQuota(per_ip=1))
        trusted = forwarded_allow_ips()
        if trusted:
            monkeypatch.setenv("FORWARDED_ALLOW_IPS", trusted)
        else:
            monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
        config = uvicorn.Config(main.app, proxy_headers=True, log_config=None)
        config.load()
        return TestClient(config.loaded_app, client=(WEB_CONTAINER, 40000))

    @staticmethod
    def via_both_proxies(visitor: str) -> dict[str, str]:
        # host nginx appends the visitor, the container nginx appends the
        # address it was reached from
        return {"X-Forwarded-For": f"{visitor}, {GATEWAY}"}

    def test_two_visitors_behind_the_same_proxy_each_get_their_own_slot(self, client):
        with client.websocket_connect("/ws", headers=self.via_both_proxies("203.0.113.5")) as a:
            assert a.receive_json()["type"] == "snapshot"
            with client.websocket_connect(
                "/ws", headers=self.via_both_proxies("198.51.100.7")
            ) as b:
                assert b.receive_json()["type"] == "snapshot", (
                    "a second visitor was refused: the cap is keyed on the proxy"
                )

    def test_one_visitor_still_hits_its_own_ceiling(self, client):
        with client.websocket_connect("/ws", headers=self.via_both_proxies("203.0.113.5")) as a:
            assert a.receive_json()["type"] == "snapshot"
            with pytest.raises(WebSocketDisconnect) as refused:  # noqa: SIM117
                with client.websocket_connect(
                    "/ws", headers=self.via_both_proxies("203.0.113.5")
                ) as again:
                    again.receive_json()
            assert refused.value.code == 1013

    def test_a_visitor_cannot_claim_someone_elses_address(self, client):
        """The left-most entry is whatever the browser sent. uvicorn walks the
        list from the right and stops at the first address it does not
        trust, which is the one the host nginx actually saw."""
        spoofed = {"X-Forwarded-For": f"198.51.100.7, 203.0.113.5, {GATEWAY}"}
        with client.websocket_connect("/ws", headers=self.via_both_proxies("203.0.113.5")) as a:
            assert a.receive_json()["type"] == "snapshot"
            with pytest.raises(WebSocketDisconnect):  # noqa: SIM117
                with client.websocket_connect("/ws", headers=spoofed) as again:
                    again.receive_json()
