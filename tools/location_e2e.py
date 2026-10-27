#!/usr/bin/env python3
"""Location, end to end, in a real Chrome (Playwright, the installed Chrome).

The owner asked (2026-10-27) to check that "use my location" works. jsdom
cannot: it has no permission model and no geolocation. This drives the real
page through the four answers a browser can give, and checks what the reader
SEES -- the Near me / World switch, the reason line, the watch card -- not
what the code believes.

  granted   position given: Near me, the far severe count, watch from it
  denied    permission denied: the world, the reason, "Location refused"
  timeout   permission given, no fix ever: the world after 8 s, the watch
            gives up after 20 s instead of "Locating..." forever
  moved     granted, then the position changes: the coarse position follows
The position itself is never written to storage (owner: nothing may be
fake; a stale position restored on reload would be a guess).

Usage: python3 tools/location_e2e.py [--url http://localhost:5380/] [--shots DIR]
Exit 1 if any scenario fails. Needs `pip install playwright` (no browser
download: it drives the installed Google Chrome).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from playwright.sync_api import Page, sync_playwright

TOKYO = {"latitude": 35.6812, "longitude": 139.7671}
OSAKA = {"latitude": 34.6937, "longitude": 135.5022}

# Never answers: the browser has permission, the fix never comes.
SILENT_GEOLOCATION = """
(() => {
  const silent = { getCurrentPosition() {}, watchPosition() { return 1 }, clearWatch() {} };
  Object.defineProperty(navigator, 'geolocation', { configurable: true, get: () => silent });
})()
"""


def wait_for_feed(page: Page) -> None:
    page.wait_for_selector(".item", timeout=60_000)


def pressed(page: Page) -> str:
    return page.eval_on_selector(
        ".scope-switch button[aria-pressed='true']", "b => b.textContent.trim()"
    )


def open_watch(page: Page) -> None:
    """The watch is one line that opens its card."""
    toggle = page.locator(".watch-compact-toggle")
    if toggle.count() and toggle.get_attribute("aria-expanded") != "true":
        toggle.click()


def here(page: Page) -> str | None:
    """The coarse position the view is centred on (`.scope-bar[data-here]`)."""
    return page.get_attribute(".scope-bar", "data-here")


def stored(page: Page) -> dict:
    return json.loads(page.evaluate("localStorage.getItem('sosforge.scope') || '{}'"))


def scenario(browser, url: str, name: str, shots: str | None, setup) -> list[str]:
    context = browser.new_context(viewport={"width": 1280, "height": 860}, locale="en-US")
    page = context.new_page()
    page.add_init_script("localStorage.setItem('sosforge.lang', 'en')")
    failures: list[str] = []
    try:
        failures = setup(context, page)
    except Exception as exc:  # a scenario that throws is a failure, said plainly
        failures = [f"{type(exc).__name__}: {exc}"]
    if shots:
        page.screenshot(path=f"{shots}/location-{name}.png")
    context.close()
    return failures


def granted(context, page: Page, url: str) -> list[str]:
    context.grant_permissions(["geolocation"])
    context.set_geolocation(TOKYO)
    page.goto(url)
    wait_for_feed(page)
    page.wait_for_function(
        "document.querySelector('.scope-switch [aria-pressed=true]')"
        "?.textContent.includes('Near me')",
        timeout=15_000,
    )
    bad = []
    if not page.is_visible(".scope-elsewhere"):
        bad.append("Near me shows no count of severe events elsewhere")
    if here(page) != "35.68,139.77":
        bad.append(f"the position is not the coarse fix: {here(page)!r}")
    if "lat" in stored(page) or "lon" in stored(page):
        bad.append(f"the position was written to storage: {stored(page)}")
    # the watch funnel, from the position Near me already has: no second
    # prompt, then step 2 (what to watch), step 3 (notifications), done
    open_watch(page)
    page.get_by_role("button", name="Use my location").click()
    page.get_by_role("button", name="Continue").click(timeout=10_000)
    if page.get_by_role("button", name="Not now").is_visible():
        page.get_by_role("button", name="Not now").click()
    page.wait_for_selector("text=Watching", timeout=10_000)
    watch = json.loads(page.evaluate("localStorage.getItem('sosforge.watch')"))
    if (watch["lat"], watch["lon"]) != (35.68, 139.77):
        bad.append(f"the watch did not reuse the coarse position: {watch}")
    return bad


def denied(context, page: Page, url: str) -> list[str]:
    page.goto(url)
    cdp = context.new_cdp_session(page)
    cdp.send("Browser.setPermission", {"permission": {"name": "geolocation"}, "setting": "denied"})
    page.reload()
    wait_for_feed(page)
    page.wait_for_selector(".scope-reason", timeout=15_000)
    bad = []
    if "World" not in pressed(page):
        bad.append(f"denied, yet the switch says {pressed(page)!r}")
    reason = page.inner_text(".scope-reason")
    if "blocked" not in reason:
        bad.append(f"the reason line says {reason!r}")
    open_watch(page)
    page.get_by_role("button", name="Use my location").click()
    page.wait_for_selector(".watch .state-att", timeout=10_000)
    said = page.inner_text(".watch .state-att")
    if "refused" not in said:
        bad.append(f"the watch says {said!r} for a refusal")
    return bad


def timeout(context, page: Page, url: str) -> list[str]:
    context.grant_permissions(["geolocation"])
    page.add_init_script(SILENT_GEOLOCATION)
    page.goto(url)
    wait_for_feed(page)
    started = time.time()
    page.wait_for_function(
        "document.querySelector('.scope-reason')?.textContent.includes('No position yet')",
        timeout=15_000,
    )
    waited = time.time() - started
    bad = []
    if "World" not in pressed(page):
        bad.append("the world view did not take over after the timeout")
    open_watch(page)
    page.get_by_role("button", name="Use my location").click()
    if not page.get_by_role("button", name="Locating...").is_disabled():
        bad.append("no 'Locating...' state while waiting")
    page.wait_for_selector("text=No position came", timeout=30_000)
    if not page.get_by_role("button", name="Use my location").is_enabled():
        bad.append("'Use my location' stayed disabled after giving up")
    print(f"    (world view after {waited:.1f} s of silence)")
    return bad


def moved(context, page: Page, url: str) -> list[str]:
    context.grant_permissions(["geolocation"])
    context.set_geolocation(TOKYO)
    page.goto(url)
    wait_for_feed(page)
    page.wait_for_function(
        "document.querySelector('.scope-bar')?.dataset.here === '35.68,139.77'", timeout=15_000
    )
    context.set_geolocation(OSAKA)
    page.wait_for_function(
        "document.querySelector('.scope-bar')?.dataset.here === '34.69,135.5'", timeout=30_000
    )
    return [] if "Near me" in pressed(page) else ["the switch left Near me after a move"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5380/")
    ap.add_argument("--shots", default=None)
    args = ap.parse_args()
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for name, run in (
            ("granted", granted),
            ("denied", denied),
            ("timeout", timeout),
            ("moved", moved),
        ):
            bad = scenario(
                browser, args.url, name, args.shots, lambda c, pg, run=run: run(c, pg, args.url)
            )
            print(f"  {'FAIL' if bad else 'ok  '}  {name}")
            for line in bad:
                print(f"          -> {line}")
            failed += bool(bad)
        browser.close()
    print(f"\n{failed} of 4 scenarios fail." if failed else "\nAll 4 scenarios pass.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
