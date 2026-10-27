"""tools/responsive.py drives Chromes that are its own, and leaves nothing.

PR #5 review, findings 3 and 4: every viewport left its 10-13 MB Chrome
profile in $TMPDIR (188 of them, 2.5 GB, on the reviewer's machine), and the
DevTools ports were fixed (9500 + index), so two overlapping runs attached to
each other's tabs and both measured the wrong viewport.

These run a real headless Chrome: a fake one would prove nothing about which
port Chrome actually binds or what it writes in its profile.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def responsive():
    path = ROOT / "tools" / "responsive.py"
    spec = importlib.util.spec_from_file_location("sosforge_responsive", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not Path(module.CHROME).exists():
        pytest.skip("no Chrome on this machine")
    yield module
    sys.modules.pop(spec.name, None)


def browser_of(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as resp:
        return json.load(resp)["webSocketDebuggerUrl"]


def test_overlapping_chromes_each_drive_their_own_tab_and_leave_no_profile(responsive):
    async def scenario():
        first, second = responsive.Chrome(), responsive.Chrome()
        try:
            page_a, page_b = await asyncio.gather(first.open(), second.open())
            assert first.port and second.port and first.port != second.port
            # the page each one drives belongs to the browser on its own port
            assert f":{first.port}/" in page_a and f":{second.port}/" in page_b
            assert browser_of(first.port) != browser_of(second.port)
            assert first.profile.is_dir() and second.profile.is_dir()
        finally:
            first.close()
            second.close()
        return first, second

    first, second = asyncio.run(scenario())
    assert not first.profile.exists() and not second.profile.exists()
    assert first.proc is not None and first.proc.poll() is not None
    assert second.proc is not None and second.proc.poll() is not None


def test_a_held_port_9500_no_longer_matters(responsive):
    """The old run's port, held by someone else, cannot be attached to."""
    import socket

    with socket.socket() as squatter:
        # if something else already holds 9500, the point stands either way
        with contextlib.suppress(OSError):
            squatter.bind(("127.0.0.1", 9500))
            squatter.listen()

        async def scenario():
            chrome = responsive.Chrome()
            try:
                await chrome.open()
                return chrome
            finally:
                chrome.close()

        chrome = asyncio.run(scenario())
    assert chrome.port != 9500
    assert not chrome.profile.exists()


def test_the_profile_is_removed_when_the_measure_fails(responsive, monkeypatch):
    seen = []
    real_init = responsive.Chrome.__init__

    def remember(self):
        real_init(self)
        seen.append(self)

    def broken(*args, **kwargs):
        raise OSError("DevTools refused")

    monkeypatch.setattr(responsive.Chrome, "__init__", remember)
    # Chrome is up and its port read; the failure comes after that
    monkeypatch.setattr(responsive.websockets, "connect", broken)
    with pytest.raises(OSError, match="DevTools refused"):
        asyncio.run(responsive.measure("http://127.0.0.1:9/", 390, 844, 1.0))
    assert seen and seen[0].port, "Chrome did start"
    assert not seen[0].profile.exists()
    assert seen[0].proc is not None and seen[0].proc.poll() is not None


def test_place_mode_seeds_the_scope_the_store_reads(responsive):
    place = responsive.parse_place("9.51,100.01,Ko Samui, Surat Thani")
    assert place == {"lat": 9.51, "lon": 100.01, "name": "Ko Samui, Surat Thani", "country": None}
    seed = responsive.place_seed(place)
    prefix = "localStorage.setItem('sosforge.scope', "
    assert seed.startswith(prefix) and seed.endswith(")")
    # the stored value is the JSON string frontend/src/store.ts loadScope() parses
    saved = json.loads(json.loads(seed[len(prefix) : -1]))
    assert saved == {"scope": "place", "asked": True, "place": place}


def test_place_mode_requires_the_report_to_open_inside_the_viewport(responsive):
    m = {
        "gotData": True,
        "overflowX": 0,
        "offenders": [],
        "regions": {"feed": {"h": 400}, "map": {"h": 300, "w": 300}, "strip": {"w": 300}},
        "itemsAboveFold": 3,
        "items": 5,
        "cardOverflow": [],
        "mapOffset": None,
        "reach": {"filters": {"ok": True}, "panel": {"ok": True}},
    }
    assert responsive.verdict("phone", 390, m) == []
    assert responsive.verdict("phone", 390, m, place=True) == ["the place report did not open"]
    m["reach"]["place"] = {"ok": False, "bottom": 900}
    assert responsive.verdict("phone", 390, m, place=True) == [
        "the place report opens outside the viewport: {'ok': False, 'bottom': 900}"
    ]


def test_near_mode_reads_a_position(responsive):
    assert responsive.parse_near("48.85,2.35") == (48.85, 2.35)
    with pytest.raises(ValueError):
        responsive.parse_near("48.85")


def test_place_mode_requires_the_button_not_to_cover_the_place(responsive):
    """At 320x568 and 200 % text the place report's button covered the
    place the map flew to (review of the place report): checked from where
    the map says the place landed, not from the map's centre."""
    m = {
        "gotData": True,
        "overflowX": 0,
        "offenders": [],
        "regions": {"feed": {"h": 400}, "map": {"h": 140, "w": 320}, "strip": {"w": 300}},
        "itemsAboveFold": 0,
        "items": 5,
        "cardOverflow": [],
        "mapOffset": None,
        "canvas": [320, 140],
        "reach": {"filters": {"ok": True}, "panel": {"ok": True}, "place": {"ok": True}},
    }
    button = {"left": 12, "top": 113, "right": 252, "bottom": 200}
    m["focus"] = {
        "x": 160,
        "y": 171,
        "inMap": True,
        "button": button,
        "iconOnly": False,
        "covered": True,
    }
    assert responsive.verdict("phone", 320, m, 2.0, place=True) == [
        f"the place report's button {button} covers the place the map flew to, at 160,171"
    ]
    m["focus"] = {**m["focus"], "y": 206, "iconOnly": True, "covered": False}
    assert responsive.verdict("phone", 320, m, 2.0, place=True) == []
    m["focus"] = {**m["focus"], "y": 900, "inMap": False}
    assert responsive.verdict("phone", 320, m, 2.0, place=True) == [
        "the place landed off the map, at 160,900"
    ]
    m["focus"] = None
    assert responsive.verdict("phone", 320, m, 2.0, place=True) == [
        "the map never said where the place landed (no data-focus-at)"
    ]
    # outside place mode, or with no WebGL map, there is nothing to judge
    assert responsive.verdict("phone", 320, m, 2.0) == []
    m["canvas"] = None
    assert responsive.verdict("phone", 320, m, 2.0, place=True) == []


def test_the_usage_line_names_every_flag(responsive):
    """--near reached argparse and the prose, not the Usage line."""
    usage = responsive.__doc__.split("Usage:", 1)[1].split("Exit code", 1)[0]
    flags = [
        option
        for action in responsive.build_parser()._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    ]
    assert "--near" in flags and len(flags) >= 8
    assert [flag for flag in flags if f"[{flag}" not in usage] == []
