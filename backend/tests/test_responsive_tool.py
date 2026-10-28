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


def test_the_smallest_map_is_measured_at_200_percent_text(responsive):
    """568x320 holds the smallest map (99 px): at 200 % text a one-line
    button is half of it, and a late one covered the place there. No
    viewport had that size and text scale."""
    assert ("landscape 568, 200 % text", 568, 320, 2.0) in responsive.VIEWPORTS


def test_version_late_holds_only_the_first_version_request(responsive):
    """--version-late S: the page's first GET /api/version is sent once the
    map says where the place landed, then S seconds later. A later one, and
    every other URL, goes out at once, or the run would measure a page
    starved of its feed instead. A fixed delay raced the map's load: the
    button came before the flight in one language and after it in two."""
    import http.server
    import threading

    class Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- the stdlib's name
            page = self.path == "/"
            body = b'<div class="map-wrap"><div class="map"></div></div>' if page else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if page else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    timings = """(async () => {
      const took = async (url) => {
        const start = performance.now();
        await (await fetch(url)).json();
        return Math.round(performance.now() - start);
      };
      const first = took('/api/version');
      const other = await took('/api/sources');
      // the map stops where the place landed, a second later
      await new Promise((go) => setTimeout(go, 1000));
      document.querySelector('.map-wrap > .map').dataset.focusAt = '284,75';
      const held = await first;
      // a later one, by its full URL this time
      return { held, other, again: await took(location.origin + '/api/version') };
    })()"""

    async def scenario():
        chrome = responsive.Chrome()
        try:
            page = await chrome.open()
            async with responsive.websockets.connect(page, max_size=None) as ws:
                counter = [0]

                async def call(method: str, params: dict | None = None) -> dict:
                    counter[0] += 1
                    await ws.send(
                        json.dumps({"id": counter[0], "method": method, "params": params or {}})
                    )
                    while True:
                        msg = json.loads(await ws.recv())
                        if msg.get("id") == counter[0]:
                            return msg

                await call("Page.enable")
                await call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": responsive.version_hold(0.5)},
                )
                await call("Page.navigate", {"url": f"{origin}/"})
                for _ in range(50):
                    probe = await call(
                        "Runtime.evaluate",
                        {
                            "expression": "location.origin + ' ' + document.readyState",
                            "returnByValue": True,
                        },
                    )
                    if probe["result"]["result"]["value"] == f"{origin} complete":
                        break
                    await asyncio.sleep(0.1)
                res = await call(
                    "Runtime.evaluate",
                    {"expression": timings, "awaitPromise": True, "returnByValue": True},
                )
                return res["result"]["result"]["value"]
        finally:
            chrome.close()

    try:
        got = asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
    # 1 s until the map stopped, then 0.5 s
    assert 1450 <= got["held"] < 3000, got
    assert got["other"] < 1000 and got["again"] < 1000, got


def test_the_map_credit_must_not_lie_on_the_map_controls(responsive):
    """On the 100 px landscape map (568x320, 667x375, 844x390) MapLibre's
    credit sat on the zoom-out button: closed, 576 px^2 of it and 2 of 9
    taps on the button went to the credit (568x320); open (it opens by
    itself on a map 640 px wide, 667x375), its strip covered 1056 px^2 and
    3 taps (UI leftovers, measured 2026-10-28)."""
    m = {
        "gotData": True,
        "overflowX": 0,
        "offenders": [],
        "regions": {"feed": {"h": 400}, "map": {"h": 100, "w": 568}, "strip": {"w": 300}},
        "itemsAboveFold": 1,
        "items": 5,
        "cardOverflow": [],
        "mapOffset": None,
        "reach": {"filters": {"ok": True}, "panel": {"ok": True}},
    }
    clear = {"zoomIn": 0, "zoomOut": 0, "report": 0}
    every_tap = {"ok": 9, "n": 9}

    def controls(*states: dict) -> dict:
        return {"credit": "shown", "states": list(states)}

    m["reach"]["mapControls"] = controls(
        {"open": False, **clear, "zoomOut": 576, "zoomOutTaps": {"ok": 7, "n": 9}},
        {"open": True, **clear, "zoomOut": 1056, "zoomOutTaps": {"ok": 6, "n": 9}},
    )
    assert responsive.verdict("landscape 568", 568, m) == [
        "the map's credit, closed, covers the zoom-out button (576 px^2)",
        "the map's credit, closed: 2 of 9 taps on the zoom-out button miss it",
        "the map's credit, open, covers the zoom-out button (1056 px^2)",
        "the map's credit, open: 3 of 9 taps on the zoom-out button miss it",
    ]
    # beside the zoom column: nothing covered, every tap lands, either state
    m["reach"]["mapControls"] = controls(
        {"open": True, **clear, "zoomOutTaps": every_tap},
        {"open": False, **clear, "zoomOutTaps": every_tap},
    )
    assert responsive.verdict("landscape 667", 667, m) == []
    # open over the place report's button is a failure too
    m["reach"]["mapControls"]["states"][0]["report"] = 120
    assert responsive.verdict("landscape 667", 667, m) == [
        "the map's credit, open, covers the place report's button (120 px^2)"
    ]
    # the credit is the basemap's licence: it must stay, and toggle on a tap
    m["reach"]["mapControls"] = {"credit": None}
    assert responsive.verdict("landscape 568", 568, m) == [
        "the map's credit (OpenFreeMap / OpenStreetMap attribution) is gone"
    ]
    m["reach"]["mapControls"] = controls(
        {"open": False, **clear, "zoomOutTaps": every_tap},
        {"open": False, **clear, "zoomOutTaps": every_tap},
    )
    assert responsive.verdict("landscape 568", 568, m) == [
        "the map's credit does not open or close on a tap"
    ]
    # its text comes with the first tiles: an empty credit is not judged
    m["reach"]["mapControls"] = {"credit": "empty"}
    assert responsive.verdict("laptop 1536", 1536, m) == []
    # no WebGL map: nothing to judge
    del m["reach"]["mapControls"]
    assert responsive.verdict("landscape 568", 568, m) == []
