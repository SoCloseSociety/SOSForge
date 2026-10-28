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

What it checks at every viewport (round 2, 2026-10-27: "le responsive doit
etre full op pour tout type de format et d'ecran"):
  * no horizontal page scroll, nothing off screen outside a horizontal strip;
  * nothing overflows its CARD (the owner's rule), measured box by box;
  * every touch target at least 44 px on a touch layout (inline links in a
    sentence excepted, as WCAG 2.5.8 does);
  * the feed shows an event above the fold, the map is non-zero;
  * every panel is reachable: the Filters dialog opens inside the viewport,
    an event opens its panel inside the viewport, the footer's sources strip
    has room;
  * the map's credit (MapLibre's attribution) is there, and neither closed
    nor opened by a tap does it lie on a zoom button or the place report's
    button (on a 100 px landscape map it lay on zoom-out);
  * the stylesheet and the components agree on what a phone is.

With --place "LAT,LON,NAME" the page starts on that place (the saved scope
the app reads at start), and the place report must open inside the
viewport like the Filters dialog. Its button, laid over the map, must not
cover the place the map flew to: where that place landed on screen is read
from the map (MapView's `data-focus-at`, written once the camera stops),
not assumed to be the map's centre. The button waits for /api/version, so
a slow answer brings it in AFTER the map has flown there: --version-late S
holds the page's first GET /api/version until the map has flown to the
place and stopped, then S seconds more. Behind a late answer the button
covered the place at 568x320 and 200 % text, while the timely run was
clean. The hold follows the map, not a clock: a plain 4 s hold raced the
map's load, and covered the place in fr and ja but not in en, whose map
happened to load after the answer. With --near "LAT,LON" geolocation is
granted and the position set over DevTools, so the page shows Near me as a
reader who said yes sees it. Without either, a headless page has no
position and measures World: neither of the two views most readers get.

Usage:  python3 tools/responsive.py [--url URL] [--json] [--shots DIR] [--only LABEL ...]
                                    [--parallel N] [--lang LANG]
                                    [--place LAT,LON,NAME] [--near LAT,LON]
                                    [--version-late S]
Exit code 1 if any viewport fails, so `make responsive` can gate on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("pip install websockets (it is in the backend venv)")

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# A desktop Chrome on Linux: not Android, so the page shows no "Get the app"
# banner and every run measures the same page.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0 Safari/537.36"
)

# Real devices, not round numbers. The narrow end is where things break: 360 is
# the width of an Android phone that is still very much in use, and it is the
# width at which a long region name pushed the event age off screen.
# Real devices, not round numbers (owner's list, 2026-10-27). The narrow end
# is where things break: 320 is an iPhone SE (1st gen) and the width at which
# a long region name pushed the event age off screen. Each entry: label,
# width, height, root text scale (2.0 = the reader's 200 % text).
VIEWPORTS = [
    # the owner's 280 px card rule, as a whole screen: the Galaxy Fold folded
    ("Galaxy Fold 1, folded", 280, 653, 1.0),
    ("iPhone SE 1", 320, 568, 1.0),
    ("iPhone SE 1, 200 % text", 320, 568, 2.0),
    ("Android small", 360, 640, 1.0),
    ("iPhone 8", 375, 667, 1.0),
    ("iPhone 14", 390, 844, 1.0),
    ("iPhone 14, 200 % text", 390, 844, 2.0),
    ("Pixel 7", 412, 915, 1.0),
    ("iPhone 15 Pro Max", 430, 932, 1.0),
    ("landscape 568", 568, 320, 1.0),
    # the smallest map of all (99 px) under a one-line button 50 px tall:
    # where a place report's button that came late covered the place
    ("landscape 568, 200 % text", 568, 320, 2.0),
    ("landscape 667", 667, 375, 1.0),
    ("landscape 844", 844, 390, 1.0),
    ("landscape 932", 932, 430, 1.0),
    ("Galaxy Z Fold, folded", 344, 882, 1.0),
    ("Pixel Fold, open portrait", 673, 841, 1.0),
    ("Pixel Fold, open landscape", 841, 673, 1.0),
    ("iPad mini portrait", 768, 1024, 1.0),
    ("iPad Air portrait", 820, 1180, 1.0),
    ("iPad landscape", 1024, 768, 1.0),
    ("iPad Air landscape", 1180, 820, 1.0),
    ("iPad Pro 12.9 landscape", 1366, 1024, 1.0),
    ("laptop 720p", 1280, 720, 1.0),
    ("laptop 1366", 1366, 768, 1.0),
    ("laptop 1440", 1440, 900, 1.0),
    ("laptop 1536", 1536, 864, 1.0),
    ("full HD", 1920, 1080, 1.0),
    ("QHD", 2560, 1440, 1.0),
    ("ultrawide", 3440, 1440, 1.0),
    ("4K", 3840, 2160, 1.0),
]


MEASURE = r"""
((touch) => {
  const doc = document.documentElement;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const name = (el) => el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className
    ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  // A child of a horizontally scrolling strip is meant to be off screen: the
  // counters on a phone and the footer's sources slider scroll on purpose.
  const inScroller = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const ov = getComputedStyle(p).overflowX;
      if (ov === 'auto' || ov === 'scroll') return true;
    }
    return false;
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.01;
  };
  const out = {
    viewport: vw, overflowX: doc.scrollWidth - vw,
    offenders: [], tinyTargets: [], cardOverflow: [],
  };
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    if (el.closest('.sr-only')) continue;
    if ((r.right > vw + 1 || r.left < -1) && !inScroller(el)) {
      out.offenders.push({ el: name(el), left: Math.round(r.left), right: Math.round(r.right) });
    }
    // 44 px on a touch layout. A link inside a sentence is exempt, as WCAG
    // 2.5.8 exempts it: its target is the line of text it sits in.
    const control = el.matches('button, a, input, select, [role="button"]');
    if (touch && control && r.height > 0 && visible(el)) {
      const inline = getComputedStyle(el).display === 'inline';
      // MapLibre's own attribution text links are inline text too
      if (!inline && (r.height < 44 || r.width < 44)) {
        out.tinyTargets.push({ el: name(el), w: Math.round(r.width), h: Math.round(r.height) });
      }
    }
  }
  // Nothing overflows its card (owner rule): every descendant inside the
  // card's box, and no card scrolling sideways, except the two strips that
  // scroll on purpose.
  const CARDS = [
    '.card', '.kpis', '.kpi', '.live-panel', '.legend', '.filter-dialog', '.source-detail',
    '.watch-compact-toggle', '.scope-bar', '.footer', '.header', '.banner', '.arrival',
    '.item', '.filters', '.get-app-banner', '.maplibregl-popup-content',
  ].join(', ');
  for (const card of document.querySelectorAll(CARDS)) {
    const cr = card.getBoundingClientRect();
    if (!cr.width || !visible(card)) continue;
    if (card.scrollWidth > card.clientWidth + 1 && getComputedStyle(card).overflowX !== 'visible') {
      out.cardOverflow.push({ card: name(card), by: card.scrollWidth - card.clientWidth });
    }
    for (const el of card.querySelectorAll('*')) {
      // the sources slider scrolls on purpose; the counters do NOT any more
      // (their third card was cut at the screen edge), so they are judged
      if (el.closest('.sr-only, .sources-strip')) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      if (r.right > cr.right + 1 || r.left < cr.left - 1) {
        const by = Math.round(Math.max(r.right - cr.right, cr.left - r.left));
        out.cardOverflow.push({ card: name(card), el: name(el), by });
      }
    }
  }
  const box = (s) => { const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect();
    return { top: Math.round(r.top), h: Math.round(r.height), w: Math.round(r.width) }; };
  out.regions = { header: box('.header'), feed: box('.feed'), map: box('.map-wrap'),
                  footer: box('.footer'), strip: box('.sources-strip'), tools: box('.tools-row') };
  out.items = document.querySelectorAll('.item').length;
  // When the feed is missing, say WHY: an empty feed renders `.feed-empty`
  // with a message that names the reason. Without this the tool could only
  // report "0 px" and let the reader assume a CSS fault.
  const empty = document.querySelector('.feed-empty');
  out.emptyMessage = empty ? empty.textContent.trim().slice(0, 80) : null;
  out.connected = !!document.querySelector('.live.on');
  // Do the stylesheet and the components agree on what a phone is? The CSS
  // puts the map above the feed; the components fold the watch into a line
  // and the filters into a bottom sheet. If only one of the two fires, the
  // layout is a chimera -- which is exactly what happened in landscape once.
  const mapCol = document.querySelector('.map-column');
  out.cssPhone = mapCol ? getComputedStyle(mapCol).order === '1' : null;
  out.jsPhone = document.querySelector('.app')?.dataset.layout === 'phone';
  // What a reader actually gets without scrolling.
  out.itemsAboveFold = [...document.querySelectorAll('.item')].filter((e) => {
    const r = e.getBoundingClientRect();
    return r.top < vh && r.bottom > 0 && r.height > 4;
  }).length;
  const map = document.querySelector('.map-column');
  out.mapOffset = map ? Math.round(map.getBoundingClientRect().top + window.scrollY) : null;
  const canvas = document.querySelector('.maplibregl-canvas');
  out.canvas = canvas ? [canvas.clientWidth, canvas.clientHeight] : null;
  out.rootFont = getComputedStyle(doc).fontSize;
  // Where the point the map flew to landed (MapView `data-focus-at`, map
  // pixels, written once the camera stops), and whether the place report's
  // button covers it: over a 139 px map at 200 % text it did.
  out.focus = null;
  const mapEl = document.querySelector('.map-wrap > .map');
  const at = mapEl && mapEl.dataset.focusAt;
  if (at) {
    const [x, y] = at.split(',').map(Number);
    const mr = mapEl.getBoundingClientRect();
    const px = mr.left + x, py = mr.top + y;
    const btn = document.querySelector('.map-report-open');
    const b = btn ? btn.getBoundingClientRect() : null;
    out.focus = {
      x: Math.round(px), y: Math.round(py),
      inMap: px >= mr.left && px <= mr.right && py >= mr.top && py <= mr.bottom,
      button: b ? { left: Math.round(b.left), top: Math.round(b.top),
                    right: Math.round(b.right), bottom: Math.round(b.bottom) } : null,
      iconOnly: !!btn && btn.classList.contains('icon-only'),
      covered: !!b && px >= b.left && px <= b.right && py >= b.top && py <= b.bottom,
    };
  }
  return out;
})
"""

# Every panel must be reachable: the Filters dialog opens INSIDE the
# viewport, and an event opens its panel inside the viewport. Measured after
# a real click, then closed again.
REACH = r"""
(async () => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const inside = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    const fits = r.left >= -1 && r.right <= vw + 1 && r.top >= -1 && r.bottom <= vh + 1;
    return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right),
             bottom: Math.round(r.bottom), ok: r.width > 0 && r.height > 0 && fits }; };
  const pause = () => new Promise((r) => setTimeout(r, 400));
  const out = {};
  // The map's own controls, first (before any panel opens over the map).
  // MapLibre's credit shares the right-hand corner with the zoom column:
  // on a 100 px landscape map it lay ON the zoom-out button, and open (it
  // opens by itself on a map 640 px wide or more), its strip covered the
  // button whole. Judged as shown, then toggled as a tap toggles it (and
  // put back): overlap in px^2 with the zoom buttons and the place
  // report's button, and where 9 taps on zoom-out land. A credit still
  // empty (its text comes with the first tiles) is not judged.
  const credit = document.querySelector('.map .maplibregl-ctrl-attrib');
  const creditButton = document.querySelector('.map .maplibregl-ctrl-attrib-button');
  if (document.querySelector('.maplibregl-canvas')) {
    const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
      return r.width || r.height ? r : null; };
    const area = (a, b) => (!a || !b) ? 0 : Math.round(
      Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
      * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)));
    const zoomIn = document.querySelector('.map .maplibregl-ctrl-zoom-in');
    const zoomOut = document.querySelector('.map .maplibregl-ctrl-zoom-out');
    const report = document.querySelector('.map-report-open');
    const covers = () => ({ zoomIn: area(box(credit), box(zoomIn)),
      zoomOut: area(box(credit), box(zoomOut)), report: area(box(credit), box(report)) });
    const taps = (el) => { const r = box(el); if (!r) return null; let ok = 0, n = 0;
      for (const fx of [0, 0.5, 1]) for (const fy of [0, 0.5, 1]) {
        const x = r.left + 3 + fx * (r.width - 6), y = r.top + 3 + fy * (r.height - 6);
        if (x < 0 || y < 0 || x > vw || y > vh) continue;
        n += 1; const hit = document.elementFromPoint(x, y);
        if (hit && el.contains(hit)) ok += 1;
      }
      return { ok, n }; };
    const state = () => ({ open: credit.classList.contains('maplibregl-compact-show'),
                           ...covers(), zoomOutTaps: taps(zoomOut) });
    const controls = { credit: !credit || !creditButton ? null : box(credit) ? 'shown' : 'empty' };
    if (controls.credit === 'shown') {
      controls.states = [state()];
      creditButton.click();
      controls.states.push(state());
      creditButton.click();
    }
    out.mapControls = controls;
  }
  const trigger = document.querySelector('.filter-trigger');
  if (trigger) {
    trigger.scrollIntoView({ block: 'center' });
    trigger.click(); await pause();
    out.filters = inside(document.querySelector('.filter-dialog'));
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
    document.querySelector('.filter-dialog')?.dispatchEvent(escape);
    await pause();
  }
  const placeOpen = document.querySelector('.map-report-open');
  if (placeOpen) {
    placeOpen.scrollIntoView({ block: 'center' });
    placeOpen.click(); await pause(); await pause();
    out.place = inside(document.querySelector('.climate-dialog'));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await pause();
    window.scrollTo(0, 0);
  }
  const row = document.querySelector('.item');
  if (row) {
    row.scrollIntoView({ block: 'center' });
    row.click(); await pause(); await pause();
    out.panel = inside(document.querySelector('.live-panel'));
    document.querySelector('.live-panel [aria-label]:last-child')?.click();
    window.scrollTo(0, 0);
  }
  return out;
})()
"""


class Chrome:
    """One headless Chrome, on ITS OWN DevTools port and its own profile.

    The port used to be fixed (9500 + index). Two overlapping runs then
    collided: the second Chrome could not bind its port and carried on
    without DevTools, and the second run, polling that port, attached to the
    FIRST run's tab -- resizing and navigating someone else's page, so both
    runs reported the wrong viewport. Port 0 lets Chrome pick a free port and
    write it to `<profile>/DevToolsActivePort`; reading it from our own
    profile is what proves the tab we drive is in the Chrome we started.

    And the profile is removed when Chrome is closed: each viewport used to
    leave a 10-13 MB `sosforge-responsive-*` dir behind, 27 per run.
    """

    def __init__(self) -> None:
        self.profile = Path(tempfile.mkdtemp(prefix="sosforge-responsive-"))
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None

    async def open(self) -> str:
        """Starts Chrome and returns its page's DevTools websocket URL."""
        self.proc = subprocess.Popen(
            [
                # SwiftShader: WebGL without a GPU. Without it the map shows its
                # "unavailable" fallback and "the map is non-zero" measures a
                # placeholder, not a map.
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--remote-debugging-port=0",
                f"--user-data-dir={self.profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        active = self.profile / "DevToolsActivePort"
        for _ in range(150):
            if self.proc.poll() is not None:
                raise RuntimeError("Chrome exited before opening its debugging port")
            lines = active.read_text().split() if active.exists() else []
            if lines and lines[0].isdigit():
                self.port = int(lines[0])
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Chrome did not open a debugging port")
        for _ in range(60):
            try:
                tabs = await asyncio.to_thread(self._tabs)
                pages = [t for t in tabs if t["type"] == "page"]
                if pages:
                    return str(pages[0]["webSocketDebuggerUrl"])
            except Exception:
                pass
            await asyncio.sleep(0.2)
        raise RuntimeError("Chrome opened no page")

    def _tabs(self) -> list[dict]:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=5) as resp:
            return json.load(resp)

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        shutil.rmtree(self.profile, ignore_errors=True)


async def measure(
    url: str,
    width: int,
    height: int,
    scale: float,
    shots: str | None = None,
    label: str = "",
    lang: str = "fr",
    place: dict | None = None,
    near: tuple[float, float] | None = None,
    version_late: float | None = None,
) -> dict:
    chrome = Chrome()
    try:
        ws_url = await chrome.open()

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
            # ... and on the tablets and foldables, which are touch devices
            # whatever their width: an iPad in landscape lays out like a
            # laptop and is still driven by a thumb.
            touch = width <= 779 or height <= 560 or any(k in label for k in ("iPad", "Fold"))
            await call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": 2,
                    "mobile": touch,
                    "screenWidth": width,
                    "screenHeight": height,
                },
            )
            await call("Emulation.setTouchEmulationEnabled", {"enabled": touch})
            # The reader's language, explicitly: the machine's locale used to
            # decide, so two runs of this tool measured two different pages.
            # French by default, its strings are among the longest of the five.
            await call("Network.enable")
            await call(
                "Network.setUserAgentOverride",
                {
                    "userAgent": USER_AGENT,
                    "acceptLanguage": lang,
                },
            )
            await call("Page.enable")
            if place is not None:
                await call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": place_seed(place)},
                )
            if version_late is not None:
                await call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": version_hold(version_late)},
                )
            if near is not None:
                # what the app's useNearMe asks: permission "granted", then a fix
                await call("Browser.grantPermissions", {"permissions": ["geolocation"]})
                await call(
                    "Emulation.setGeolocationOverride",
                    {"latitude": near[0], "longitude": near[1], "accuracy": 50},
                )
            if scale != 1.0:
                # the reader's own text size: rem scales with the root, the
                # way a browser's "very large" or a 200 % zoom of text does
                await call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": f"document.addEventListener('DOMContentLoaded', () => "
                        f"document.documentElement.style.fontSize = '{int(scale * 100)}%')"
                    },
                )
            await call("Page.navigate", {"url": url})

            # Wait for DATA, not for a clock. A fixed sleep measured a page
            # that had not received its snapshot yet, and this tool then
            # reported "the feed is 0 px tall" -- which is what an empty feed
            # and a starved feed look like from the outside, since the
            # component renders `.feed-empty` INSTEAD of `.feed` when it has
            # nothing to show. Against the live site over HTTPS that produced
            # three false failures out of eight. A gate that cries wolf is a
            # gate people learn to ignore.
            # And wait for it to STAY. On its first seconds the client shows
            # "reconnecting" once or twice before the snapshot settles, and a
            # measurement taken in one of those holes reports an empty feed --
            # which is a data condition, not a layout one, and blaming the CSS
            # for it is exactly the wrong conclusion. Two consecutive readings
            # with events, or it is not settled.
            async def item_count() -> int:
                probe = await call(
                    "Runtime.evaluate",
                    {
                        "expression": "document.querySelectorAll('.item').length",
                        "returnByValue": True,
                    },
                )
                return probe["result"]["result"]["value"]

            ready, streak = False, 0
            for _ in range(80):
                await asyncio.sleep(0.5)
                if await item_count() > 0:
                    streak += 1
                    if streak >= 2:
                        ready = True
                        break
                else:
                    streak = 0
            await asyncio.sleep(1.0)
            place_wait = None
            if place is not None:
                # The camera flies to the place once the map is ready, and the
                # button appears once /api/version answered: measured before
                # either, the check would judge a world view or no button.
                # MapLibre's `load` waits for the basemap's first tiles: how
                # long this took is reported, and what was still missing.
                # And both must STAY: a button that arrives after the flight
                # sends the camera off again, and the point it had written
                # is stale until that flight stops. Two equal readings.
                waited = 0.0
                state: dict = {}
                previous = None
                while waited < 90:
                    probe = await call(
                        "Runtime.evaluate", {"expression": PLACE_SETTLED, "returnByValue": True}
                    )
                    state = probe["result"]["result"]["value"]
                    if state["button"] and state["focusAt"] and state == previous:
                        break
                    previous = state
                    await asyncio.sleep(0.5)
                    waited += 0.5
                place_wait = {"s": waited, **state}
                await asyncio.sleep(0.5)
            # Measure, and if the feed came back empty after we HAD seen
            # events, the page hit one of those reconnection holes between the
            # check and the measurement: wait it out and measure again rather
            # than reporting a layout failure that is not one.
            expression = f"{MEASURE}({'true' if touch else 'false'})"
            for _attempt in range(4):
                res = await call(
                    "Runtime.evaluate", {"expression": expression, "returnByValue": True}
                )
                measured = res["result"]["result"]["value"]
                if not ready or measured["items"] > 0:
                    break
                await asyncio.sleep(2.5)
            measured["gotData"] = ready and measured["items"] > 0
            measured["touch"] = touch
            measured["placeWait"] = place_wait
            if shots:
                import base64

                img = await call("Page.captureScreenshot", {"format": "png"})
                safe = "".join(c if c.isalnum() else "-" for c in label).strip("-")
                with open(f"{shots}/{width}x{height}-{safe}.png", "wb") as f:
                    f.write(base64.b64decode(img["result"]["data"]))
            res = await call(
                "Runtime.evaluate",
                {"expression": REACH, "returnByValue": True, "awaitPromise": True},
            )
            measured["reach"] = res["result"]["result"].get("value") or {}
            return measured
    finally:
        chrome.close()


# What place mode waits for: the button (and its box) and where the map
# says the place landed.
PLACE_SETTLED = r"""
(() => {
  const btn = document.querySelector('.map-report-open');
  const b = btn ? btn.getBoundingClientRect() : null;
  return {
    button: b ? [b.left, b.top, b.right, b.bottom].map(Math.round) : null,
    focusAt: document.querySelector('.map-wrap > .map')?.dataset.focusAt || null,
  };
})()
"""


def version_hold(seconds: float, cap: float = 30.0) -> str:
    """Runs before the page's own scripts: the page's FIRST `GET /api/version`
    is sent only once the map says where the place landed (MapView's
    `data-focus-at`, written when its flight stops; at most `cap` seconds),
    then `seconds` more. Every later request, and every other URL, goes out
    at once. The place report's button waits for that answer
    (useApiFeature), so it arrives after the flight to the place."""
    return (
        "(() => {"
        " const send = window.fetch.bind(window); let held = false;"
        " const flown = () => new Promise((go) => {"
        " const start = Date.now();"
        " const look = () => document.querySelector('.map-wrap > .map')?.dataset.focusAt"
        f" || Date.now() - start > {round(cap * 1000)} ? go() : setTimeout(look, 100);"
        " look();"
        " });"
        " window.fetch = (input, init) => {"
        " const raw = typeof input === 'string' || input instanceof URL ? input : input.url;"
        " const url = new URL(raw, location.href);"
        " if (held || url.pathname !== '/api/version') return send(input, init);"
        " held = true;"
        " return flown()"
        f" .then(() => new Promise((go) => setTimeout(go, {round(seconds * 1000)})))"
        " .then(() => send(input, init));"
        " };"
        "})()"
    )


def parse_place(text: str) -> dict:
    """`LAT,LON,NAME` (the name may hold commas) -> the app's saved place."""
    lat, lon, name = text.split(",", 2)
    return {"lat": float(lat), "lon": float(lon), "name": name.strip(), "country": None}


def parse_near(text: str) -> tuple[float, float]:
    lat, lon = (float(x) for x in text.split(","))
    return lat, lon


def place_seed(place: dict) -> str:
    """Runs before the page's own scripts: the saved scope the store reads
    at start (`sosforge.scope`), so the page opens on that place."""
    saved = json.dumps({"scope": "place", "asked": True, "place": place})
    return f"localStorage.setItem('sosforge.scope', {json.dumps(saved)})"


def map_controls_failures(controls: dict | None) -> list[str]:
    """The map's credit (MapLibre's compact attribution) must stay one tap
    away, and never lie on a zoom button or the place report's button,
    closed or open. `controls` is REACH's `mapControls`: None without a
    WebGL map (nothing to judge); `credit` None when the control is gone,
    "empty" before its text arrived with the tiles (not judged), "shown"
    with `states`, as shown then as toggled by one tap."""
    if controls is None:
        return []
    if controls.get("credit") is None:
        return ["the map's credit (OpenFreeMap / OpenStreetMap attribution) is gone"]
    failures = []
    names = {
        "zoomOut": "zoom-out button",
        "zoomIn": "zoom-in button",
        "report": "place report's button",
    }
    states = controls.get("states") or []
    for state in states:
        how = "open" if state["open"] else "closed"
        for key, what in names.items():
            if state.get(key, 0) > 0:
                failures.append(f"the map's credit, {how}, covers the {what} ({state[key]} px^2)")
        taps = state.get("zoomOutTaps")
        if taps and taps["ok"] < taps["n"]:
            failures.append(
                f"the map's credit, {how}: {taps['n'] - taps['ok']} of {taps['n']} taps "
                "on the zoom-out button miss it"
            )
    if len(states) == 2 and states[0]["open"] == states[1]["open"]:
        failures.append("the map's credit does not open or close on a tap")
    return failures


def verdict(label: str, width: int, m: dict, scale: float = 1.0, place: bool = False) -> list[str]:
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
        why = m.get("emptyMessage")
        if why:
            # Not a layout verdict: the feed rendered its empty state, which is
            # a data or filter condition, not a sizing one.
            return [f"the feed is empty, not squeezed -- it says: {why!r}"]
        failures.append(f"the feed is {feed['h'] if feed else 0} px tall: no events are readable")
    # At 200 % text the page reflows and SCROLLS, which is what WCAG 1.4.10
    # asks of it: the checks that matter there are no sideways scroll, no
    # overflow out of a card, every panel reachable. A 320x568 screen at
    # 32 px text cannot also hold an event above the fold.
    if m["itemsAboveFold"] < 1 and m["items"] > 0 and scale == 1.0:
        failures.append("not one event is visible without scrolling")
    if m.get("cardOverflow"):
        seen = {}
        for o in m["cardOverflow"]:
            seen.setdefault((o["card"], o.get("el")), o)
        detail = ", ".join(
            f"{c}>{e or 'itself'} by {o['by']} px" for (c, e), o in list(seen.items())[:3]
        )
        failures.append(f"{len(seen)} overflow(s) out of a card: {detail}")
    mapbox = m["regions"].get("map")
    if not mapbox or mapbox["h"] < 40 or mapbox["w"] < 40:
        failures.append(f"the map is {mapbox} : not usable")
    if place and m.get("canvas"):
        # Judged on a real map only: without WebGL there is no camera, and
        # "the map is not usable" has already been said.
        focus = m.get("focus")
        if not focus:
            waited = m.get("placeWait") or {}
            after = f" after {waited['s']} s, {waited}" if waited else ""
            failures.append(f"the map never said where the place landed (no data-focus-at){after}")
        elif not focus["inMap"]:
            failures.append(f"the place landed off the map, at {focus['x']},{focus['y']}")
        elif focus["covered"]:
            failures.append(
                f"the place report's button {focus['button']} covers the place "
                f"the map flew to, at {focus['x']},{focus['y']}"
            )
    reach = m.get("reach") or {}
    panels = [("filters", "the Filters dialog"), ("panel", "an event's panel")]
    if place:
        panels.append(("place", "the place report"))
    for key, what in panels:
        got = reach.get(key)
        if not got:
            failures.append(f"{what} did not open")
        elif not got["ok"]:
            failures.append(f"{what} opens outside the viewport: {got}")
    failures.extend(map_controls_failures(reach.get("mapControls")))
    strip = m["regions"].get("strip")
    if not strip or strip["w"] < 60:
        failures.append(f"the sources strip has no room: {strip}")
    if width < 780 and m["mapOffset"] is not None and m["mapOffset"] > 3 * 844:
        failures.append(f"the map starts {m['mapOffset']} px down the page: unreachable")
    if m.get("cssPhone") is not None and m["cssPhone"] != m["jsPhone"]:
        failures.append(
            f"stylesheet and components disagree on the breakpoint "
            f"(css says phone={m['cssPhone']}, components say {m['jsPhone']})"
        )
    if m.get("touch") and m["tinyTargets"]:
        seen = {}
        for t in m["tinyTargets"]:
            seen.setdefault(t["el"], t)
        detail = ", ".join(f"{t['el']} {t['w']}x{t['h']}" for t in list(seen.values())[:4])
        failures.append(f"{len(seen)} touch target(s) under 44 px: {detail}")
    return failures


def build_parser() -> argparse.ArgumentParser:
    """Every flag, here and in the docstring's Usage line (tested)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5380/")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--shots", default=None, help="directory for one screenshot per viewport")
    ap.add_argument("--only", nargs="*", default=None, help="labels (substrings) to run")
    ap.add_argument("--parallel", type=int, default=3, help="Chromes at once")
    ap.add_argument("--lang", default="fr", help="language of the page (en, fr, es, ja, id)")
    ap.add_argument("--place", default=None, help='start on a place: "LAT,LON,NAME"')
    ap.add_argument("--near", default=None, help='Near me at a granted position: "LAT,LON"')
    ap.add_argument(
        "--version-late",
        type=float,
        default=None,
        metavar="S",
        help="hold the page's first GET /api/version until the map has flown to the place,"
        " then S seconds (the place button comes late)",
    )
    return ap


async def main() -> int:
    args = build_parser().parse_args()
    place = parse_place(args.place) if args.place else None
    near = parse_near(args.near) if args.near else None

    chosen = [
        v for v in VIEWPORTS if not args.only or any(o.lower() in v[0].lower() for o in args.only)
    ]
    results, failed = [], 0
    for start in range(0, len(chosen), args.parallel):
        batch = chosen[start : start + args.parallel]
        measured = await asyncio.gather(
            *[
                measure(
                    args.url,
                    w,
                    h,
                    scale,
                    args.shots,
                    label,
                    args.lang,
                    place,
                    near,
                    args.version_late,
                )
                for label, w, h, scale in batch
            ]
        )
        for (label, w, h, scale), m in zip(batch, measured, strict=True):
            bad = verdict(label, w, m, scale, place is not None)
            results.append(
                {
                    "label": label,
                    "width": w,
                    "height": h,
                    "scale": scale,
                    "measured": m,
                    "failures": bad,
                }
            )
            if bad and m.get("gotData"):
                failed += 1
            if not args.json:
                mark = "??  " if not m.get("gotData") else ("FAIL" if bad else "ok  ")
                focus = m.get("focus") if place else None
                where = ""
                if focus:
                    button = focus["button"] or {}
                    where = (
                        f", place at y {focus['y']}, button bottom {button.get('bottom', '--')}"
                        f"{' (icon only)' if focus['iconOnly'] else ''}"
                    )
                print(
                    f"  {mark}  {label:28s} {w:>4}x{h:<4}  "
                    f"{m['itemsAboveFold']} event(s) above the fold, map "
                    f"{(m['regions'].get('map') or {}).get('h', 0)} px"
                    f"{', touch' if m.get('touch') else ''}{where}",
                    flush=True,
                )
                for line in bad:
                    print(f"          -> {line}", flush=True)

    if args.json:
        print(json.dumps(results, indent=2))
    elif failed:
        print(f"\n{failed} of {len(chosen)} viewports fail.")
    else:
        print(f"\nAll {len(chosen)} viewports pass.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
