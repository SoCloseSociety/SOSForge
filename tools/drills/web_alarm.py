#!/usr/bin/env python3
"""The web's watch alarm, proven in a real browser against a drill server.

Starts `tools/drill.py serve` on 127.0.0.1:8334, the worktree's Vite dev
server on 127.0.0.1:5334 (proxying /api and /ws to the drill), and Chrome
through Playwright with the notifications permission granted. The page
watches Tokyo (the drill's home), the sound is switched on with a real click,
and `Notification`, `ServiceWorkerRegistration.showNotification` and the
Web Audio oscillators are spied on. The drills play; after each one the
script asserts what rang, what did not, and what the alarm list shows.

    python3 tools/drills/web_alarm.py        # a python3 that has Playwright

Needs `npm ci` in this worktree's frontend/ first. Stops both servers by
port when it exits.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DRILL_PORT = 8334
VITE_PORT = 5334
TOKYO = json.loads((ROOT / "tools" / "drills" / "homes.json").read_text())["tokyo"]
SCENARIOS = [
    "01-quake-in-zone",
    "02-eew-cancelled",
    "05-relocated-out",
    "07-far-extreme",
    "08-burst",
]

SPY = """
(() => {
  window.__notes = [];
  window.__closed = [];
  window.__beeps = [];
  const record = (via, title, opts) => {
    window.__notes.push(
      {via, title, tag: opts && opts.tag, body: opts && opts.body, t: Date.now()});
  };
  class SpyNotification {
    constructor(title, opts) { record('page', title, opts); this.tag = opts && opts.tag; }
    close() { window.__closed.push(this.tag); }
    static get permission() { return 'granted'; }
    static requestPermission() { return Promise.resolve('granted'); }
  }
  window.Notification = SpyNotification;
  if (window.ServiceWorkerRegistration) {
    const proto = ServiceWorkerRegistration.prototype;
    proto.showNotification = function (title, opts) {
      record('sw', title, opts);
      return Promise.resolve();
    };
    proto.getNotifications = function (filter) {
      const tag = filter && filter.tag;
      return Promise.resolve(window.__notes
        .filter(n => n.via === 'sw' && (!tag || n.tag === tag))
        .map(n => ({tag: n.tag, title: n.title, close() { window.__closed.push(n.tag); }})));
    };
  }
  const Ctx = window.AudioContext;
  if (Ctx) {
    const create = Ctx.prototype.createOscillator;
    Ctx.prototype.createOscillator = function () {
      const osc = create.call(this);
      const set = osc.frequency.setValueAtTime.bind(osc.frequency);
      osc.frequency.setValueAtTime = (value, at) => {
        window.__beeps.push({hz: value, t: Date.now()});
        return set(value, at);
      };
      return osc;
    };
  }
})();
"""


def free(port: int) -> bool:
    out = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
    )
    return not out.stdout.strip()


def stop_port(port: int) -> None:
    out = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
    )
    for pid in out.stdout.split():
        os.kill(int(pid), signal.SIGTERM)


def wait_http(url: str, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise SystemExit(f"web_alarm: {url} never answered")


def timeline_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    for port in (DRILL_PORT, VITE_PORT):
        if not free(port):
            raise SystemExit(f"web_alarm: port {port} is taken; stop it by port first")
    timeline = Path(tempfile.mkdtemp(prefix="sosforge-web-drill-")) / "timeline.jsonl"
    drill = subprocess.Popen(
        [
            str(ROOT / "backend" / ".venv" / "bin" / "python"),
            str(ROOT / "tools" / "drill.py"),
            "serve",
            "--port",
            str(DRILL_PORT),
            "--wait-clients",
            "1",
            "--delay",
            "6",
            "--gap",
            "4",
            "--exit",
            "--timeline",
            str(timeline),
            "--scenario",
            *SCENARIOS,
        ],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    vite = subprocess.Popen(
        ["npx", "vite", "--port", str(VITE_PORT), "--host", "127.0.0.1"],
        cwd=ROOT / "frontend",
        env={**os.environ, "SOS_API_URL": f"http://127.0.0.1:{DRILL_PORT}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rows: list[tuple[str, bool, str]] = []
    try:
        wait_http(f"http://127.0.0.1:{DRILL_PORT}/healthz", 30)
        wait_http(f"http://127.0.0.1:{VITE_PORT}/", 60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, permissions=["notifications"]
            )
            context.add_init_script(SPY)
            watch = {"lat": TOKYO["lat"], "lon": TOKYO["lon"], "name": "Tokyo (drill)"}
            context.add_init_script(
                f"localStorage.setItem('sosforge.watch', {json.dumps(json.dumps(watch))});"
            )
            page = context.new_page()
            errors: list[str] = []

            def on_console(message):  # type: ignore[no-untyped-def]
                # the page reconnecting once the drill server has exited is the
                # teardown, not a defect
                if message.type == "error" and drill.poll() is None:
                    errors.append(message.text)

            page.on("console", on_console)
            page.goto(f"http://127.0.0.1:{VITE_PORT}/")
            page.locator("button.toggle[aria-pressed]").first.click()  # sound on, a real gesture
            sound_on = page.locator("button.toggle[aria-pressed='true']").count() > 0

            # follow the drill until it exits, sampling the alarm list
            samples: list[tuple[float, list[str]]] = []
            while drill.poll() is None:
                texts = page.locator(".watch-alarms li").all_inner_texts()
                samples.append((time.time() * 1000, texts))
                time.sleep(0.5)
            page.wait_for_timeout(2000)
            notes = page.evaluate("window.__notes")
            closed = page.evaluate("window.__closed")
            beeps = page.evaluate("window.__beeps")
            final_alarms = page.locator(".watch-alarms li").all_inner_texts()
            browser.close()

        entries = timeline_entries(timeline)
        ids = {e.get("id") for e in entries if e.get("id")}
        run = next((i.split("drill")[1][:6] for i in ids if i and "drill" in i), "")
        q1, e1, q2 = (f"usgs:drill{run}q1", f"jma_eew:drill{run}e1", f"usgs:drill{run}q2")
        tags = [n["tag"] for n in notes]

        rows.append(
            ("sound switched on by a click", sound_on, f"aria-pressed after click: {sound_on}")
        )
        rows.append(
            (
                "severe quake 85 km away rings",
                tags.count(q1) >= 1,
                f"notifications for q1: {tags.count(q1)}",
            )
        )
        rows.append(
            (
                "its escalation to extreme rings again",
                tags.count(q1) >= 2,
                f"notifications for q1: {tags.count(q1)} (severe, then escalated to extreme)",
            )
        )
        rows.append(
            ("the EEW rings", tags.count(e1) >= 1, f"notifications for e1: {tags.count(e1)}")
        )
        rows.append(
            (
                "the EEW escalation rings again",
                tags.count(e1) >= 2,
                f"notifications for e1: {tags.count(e1)}",
            )
        )
        rows.append(
            (
                "never twice for the same revision",
                tags.count(q1) <= 2 and tags.count(e1) <= 2,
                f"q1 {tags.count(q1)}, e1 {tags.count(e1)} for 2 revisions each",
            )
        )
        cancelled_listed = any("福島県沖" in t for t in final_alarms)
        rows.append(
            (
                "the cancelled EEW leaves the alarm list",
                not cancelled_listed,
                f"final alarm list: {final_alarms or '[]'}",
            )
        )
        rows.append(
            (
                "the cancelled EEW's notification is closed",
                e1 in closed,
                f"close() called for: {closed or 'nothing'}",
            )
        )
        rows.append(
            (
                "the relocated quake rang while near",
                tags.count(q2) == 1,
                f"notifications for q2: {tags.count(q2)}",
            )
        )
        relocated_listed = any("Tsuchiura" in t or "Kurilsk" in t for t in final_alarms)
        rows.append(
            (
                "the relocated quake leaves the alarm list",
                not relocated_listed,
                f"final alarm list: {final_alarms or '[]'}",
            )
        )
        far = [t for t in tags if t == f"usgs:drill{run}f1"]
        burst = [t for t in tags if t and t.startswith(f"usgs:drill{run}b")]
        rows.append(
            (
                "far extreme and minor burst stay silent",
                not far and not burst,
                f"far {len(far)}, burst {len(burst)}",
            )
        )

        # Which broadcast each beep follows: the page and the drill share this
        # machine's clock. Two things sound in this app: the watch alarm, and
        # the feed's own alert for any breaking or escalated severe+ event.
        def beeps_after(entry: dict) -> list[dict]:
            t0 = datetime.fromisoformat(entry["server_time"]).timestamp() * 1000
            return [b for b in beeps if t0 <= b["t"] <= t0 + 1500]

        loud = [
            e
            for e in entries
            if e.get("type") in ("event", "update")
            and e.get("severity") in ("severe", "extreme")
            and (e.get("breaking") or e.get("escalated"))
        ]
        for entry in loud:
            got = beeps_after(entry)
            label = f"{entry['type']} {entry['id'].split(':')[0]} {entry['severity']}"
            rows.append(
                (
                    f"sound after {label}",
                    bool(got),
                    f"{len(got)} beep(s) at {sorted({b['hz'] for b in got}) or '-'} Hz",
                )
            )
        quiet = [e for e in entries if e.get("id", "").startswith(f"usgs:drill{run}b")]
        burst_beeps = [b for e in quiet for b in beeps_after(e)]
        rows.append(
            ("the minor burst makes no sound", not burst_beeps, f"{len(burst_beeps)} beep(s)")
        )
        rows.append(("no console error", not errors, "; ".join(errors[:3]) or "none"))
        print(
            "\nnotifications:",
            json.dumps(
                [{k: n[k] for k in ("via", "title", "tag")} for n in notes], ensure_ascii=False
            ),
        )
    finally:
        for proc in (drill, vite):
            if proc.poll() is None:
                proc.terminate()
        time.sleep(1)
        stop_port(DRILL_PORT)
        stop_port(VITE_PORT)
        shutil.rmtree(timeline.parent, ignore_errors=True)

    print()
    failures = 0
    for name, ok, detail in rows:
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:46s} {detail}")
    print(f"\n{len(rows) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
