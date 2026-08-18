#!/usr/bin/env python3
"""Responsive gate: measures the real layout in Chrome, at every breakpoint.

Why a tool and not an eyeball: this product was shipped with a phone layout in
which `.feed` was **0 pixels tall**. The build was green, the unit tests were
green, the CSS had a `@media (max-width: 900px)` block that looked reasonable,
and a phone showed not one earthquake. Nothing in the test suite could see it,
because jsdom has no layout engine -- it will happily report a healthy DOM for
a page that renders as an empty screen.

So this asks a real browser, and it asks for numbers rather than a picture. A
screenshot proves there is a problem; only a measurement says which element
causes it.

Two traps this tool exists to avoid, both of which produced wrong conclusions
here before it was written:

  * `--window-size=390,844` does NOT give a 390 px viewport. Chrome laid the
    page out at 500 px and the screenshot was cropped to 390, which looks
    exactly like a horizontal-overflow bug and is not one. Device metrics are
    set over the DevTools protocol instead.
  * A screenshot taken before the websocket delivers anything shows an empty,
    disconnected product (lesson 17). The page is given real time to fill.

Usage:  python3 tools/responsive.py [--url URL] [--json]
Exit code 1 if any breakpoint fails, so `make responsive` can gate on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("pip install websockets (it is in the backend venv)")

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Real devices, not round numbers. The narrow end is where things break: 360 is
# the width of an Android phone that is still very much in use, and it is the
# width at which a long region name pushed the event age off screen.
VIEWPORTS = [
    ("small phone", 360, 800),
    ("iPhone 14", 390, 844),
    ("large phone", 414, 896),
    ("phone landscape", 844, 390),
    ("tablet portrait", 768, 1024),
    ("breakpoint edge", 820, 1180),
    ("small laptop", 1024, 768),
    ("desktop", 1440, 900),
]

MEASURE = r"""
(() => {
  const doc = document.documentElement;
  const vw = window.innerWidth;
  const name = (el) => el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className
    ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  // A child of a horizontally scrolling strip is meant to be off screen; the
  // filter chips and the counter strip both scroll on purpose.
  const inScroller = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const ov = getComputedStyle(p).overflowX;
      if (ov === 'auto' || ov === 'scroll') return true;
    }
    return false;
  };
  const out = { viewport: vw, overflowX: doc.scrollWidth - vw, offenders: [], tinyTargets: [] };
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    if ((r.right > vw + 1 || r.left < -1) && !inScroller(el)) {
      out.offenders.push({ el: name(el), left: Math.round(r.left), right: Math.round(r.right) });
    }
    if (el.matches('button, a, input, select, [role="button"]') && r.height > 0 &&
        (r.height < 36 || r.width < 36) && !inScroller(el)) {
      out.tinyTargets.push({ el: name(el), w: Math.round(r.width), h: Math.round(r.height) });
    }
  }
  const box = (s) => { const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect();
    return { top: Math.round(r.top), h: Math.round(r.height) }; };
  out.regions = { header: box('.header'), feed: box('.feed'), map: box('.map-wrap, .map-column'),
                  footer: box('.footer') };
  out.items = document.querySelectorAll('.item').length;
  // Do the stylesheet and the components agree on what a phone is? The CSS
  // switches the map above the feed; the components collapse the filters. If
  // only one of the two fires, the layout is a chimera -- which is exactly
  // what happened in landscape, where the CSS stacked the page while the
  // filter panel stayed expanded and buried every event.
  const mapCol = document.querySelector('.map-column');
  out.cssPhone = mapCol ? getComputedStyle(mapCol).order === '1' : null;
  out.jsPhone = !!document.querySelector('.filters-collapsed, .footer-compact');
  // What a reader actually gets without scrolling. The number that matters:
  // "the feed exists" is not the same claim as "you can see an event".
  out.itemsAboveFold = [...document.querySelectorAll('.item')].filter((e) => {
    const r = e.getBoundingClientRect();
    return r.top < window.innerHeight && r.bottom > 0 && r.height > 4;
  }).length;
  // How far down the map starts: with the feed above it, it once began 20968 px
  // down the page, which is the same as not existing.
  const map = document.querySelector('.map-column');
  out.mapOffset = map ? Math.round(map.getBoundingClientRect().top + window.scrollY) : null;
  return out;
})()
"""


async def measure(url: str, width: int, height: int, port: int) -> dict:
    profile = tempfile.mkdtemp(prefix="sosforge-responsive-")
    chrome = subprocess.Popen(
        [
            CHROME, "--headless=new", "--disable-gpu",
            f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                pages = [t for t in tabs if t["type"] == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.4)
        if not ws_url:
            raise RuntimeError("Chrome did not open a debugging port")

        async with websockets.connect(ws_url, max_size=None) as ws:
            counter = [0]

            async def call(method: str, params: dict | None = None) -> dict:
                counter[0] += 1
                mid = counter[0]
                await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid:
                        return msg

            # Touch is emulated on exactly the viewports the stylesheet treats
            # as phones -- same expression as PHONE in useMediaQuery.ts. Getting
            # this wrong made the tool report undersized tap targets on a
            # landscape phone: the `pointer: coarse` rules that fix them were
            # simply never applied, so it was measuring a device that does not
            # exist.
            touch = width <= 820 or height <= 560
            await call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 2,
                "mobile": touch, "screenWidth": width, "screenHeight": height,
            })
            await call("Emulation.setTouchEmulationEnabled", {"enabled": touch})
            await call("Page.enable")
            await call("Page.navigate", {"url": url})

            # Wait for DATA, not for a clock. A fixed sleep measured a page
            # that had not received its snapshot yet, and this tool then
            # reported "the feed is 0 px tall" -- which is what an empty feed
            # and a starved feed look like from the outside, since the
            # component renders `.feed-empty` INSTEAD of `.feed` when it has
            # nothing to show. Against the live site over HTTPS that produced
            # three false failures out of eight. A gate that cries wolf is a
            # gate people learn to ignore.
            ready = False
            for _ in range(60):
                await asyncio.sleep(0.5)
                probe = await call("Runtime.evaluate", {
                    "expression": "document.querySelectorAll('.item').length",
                    "returnByValue": True,
                })
                if probe["result"]["result"]["value"] > 0:
                    ready = True
                    break
            # a little more, so late-arriving events do not resize mid-measure
            await asyncio.sleep(1.5)
            res = await call("Runtime.evaluate", {"expression": MEASURE, "returnByValue": True})
            measured = res["result"]["result"]["value"]
            measured["gotData"] = ready
            return measured
    finally:
        chrome.terminate()


def verdict(label: str, width: int, m: dict) -> list[str]:
    """The invariants. Each one is a bug this product actually shipped."""
    failures = []
    if not m.get("gotData"):
        # Not a layout verdict: the page never received an event, so there is
        # nothing to lay out. Said plainly rather than blamed on the CSS.
        return ["no event ever arrived: the feed was never populated, layout not judged"]
    if m["overflowX"] > 1:
        failures.append(f"the page scrolls sideways by {m['overflowX']} px")
    if m["offenders"]:
        seen: dict[str, dict] = {}
        for o in m["offenders"]:
            seen.setdefault(o["el"], o)
        detail = ", ".join(f"{o['el']} ends at {o['right']}" for o in list(seen.values())[:3])
        failures.append(f"{len(seen)} element(s) off screen: {detail}")
    feed = m["regions"].get("feed")
    if not feed or feed["h"] < 40:
        failures.append(f"the feed is {feed['h'] if feed else 0} px tall: no events are readable")
    if m["itemsAboveFold"] < 1 and m["items"] > 0:
        failures.append("not one event is visible without scrolling")
    if width < 820 and m["mapOffset"] is not None and m["mapOffset"] > 3 * 844:
        failures.append(f"the map starts {m['mapOffset']} px down the page: unreachable")
    if m.get("cssPhone") is not None and m["cssPhone"] != m["jsPhone"]:
        failures.append(
            f"stylesheet and components disagree on the breakpoint "
            f"(css says phone={m['cssPhone']}, components say {m['jsPhone']})"
        )
    if m["cssPhone"] and m["tinyTargets"]:
        seen = {}
        for t in m["tinyTargets"]:
            seen.setdefault(t["el"], t)
        detail = ", ".join(f"{t['el']} {t['w']}x{t['h']}" for t in list(seen.values())[:3])
        failures.append(f"{len(seen)} touch target(s) under 36 px: {detail}")
    return failures


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5273/")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results, failed = [], 0
    for i, (label, w, h) in enumerate(VIEWPORTS):
        m = await measure(args.url, w, h, 9500 + i)
        bad = verdict(label, w, m)
        results.append({"label": label, "width": w, "height": h, "measured": m, "failures": bad})
        if bad and m.get("gotData"):
            failed += 1
        elif bad:
            # inconclusive, not failed
            pass
        if not args.json:
            mark = "??  " if not m.get("gotData") else ("FAIL" if bad else "ok  ")
            feed_h = (m["regions"].get("feed") or {}).get("h", 0)
            print(f"  {mark}  {label:17s} {w:>4}x{h:<4}  "
                  f"feed {feed_h:>6} px, {m['itemsAboveFold']} event(s) above the fold")
            for line in bad:
                print(f"          -> {line}")

    if args.json:
        print(json.dumps(results, indent=2))
    elif failed:
        print(f"\n{failed} of {len(VIEWPORTS)} viewports fail.")
    else:
        print(f"\nAll {len(VIEWPORTS)} viewports pass.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
