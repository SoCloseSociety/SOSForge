#!/usr/bin/env python3
"""SOSForge notification drill.

Starts the REAL app in this process (FastAPI + uvicorn, the real pipeline,
store, deduper and hub) on 127.0.0.1, with the real sources switched off and
the data directory forced to a fresh temp dir, then plays scripted scenarios
from `tools/drills/*.json` through the real normalizers (USGS, JMA EEW,
Meteoalarm, NWS parsers) and the real `pipeline.emit` / `retract`. Every
message the hub sends is printed on a timeline with the server's clock, so a
client under test -- the web, the app on an emulator, the macOS agent -- can
be checked against it.

    backend/.venv/bin/python tools/drill.py list
    backend/.venv/bin/python tools/drill.py serve [--port 8334] [--wait-clients 1]
    backend/.venv/bin/python tools/drill.py check [--port 8334]

`serve` plays the scenarios once (after `--wait-clients` sockets are open
and `--delay` seconds) and keeps serving until Ctrl-C, so a client can poll
`/api/events` afterwards. `check` connects its own probe clients (the web's
socket, the app's zoned sockets, the app's cursor poll), plays everything,
asserts the exact messages each one got, and exits 1 on any failure.

A drill never touches the product's data: it refuses to start if the app was
imported before the temp dir was set, or if the store does not point at it.
Every place and title it publishes says [DRILL].
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import math
import os
import random
import re
import shutil
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DRILLS = Path(__file__).resolve().parent / "drills"
DEFAULT_PORT = 8334
JST = ZoneInfo("Asia/Tokyo")

HOMES: dict[str, dict[str, Any]] = {
    k: v
    for k, v in json.loads((DRILLS / "homes.json").read_text()).items()
    if not k.startswith("_")
}
ZONES = ";".join(f"{h['lat']},{h['lon']},{h['radius_km']}" for h in HOMES.values())
COUNTRIES = ",".join(h["country"] for h in HOMES.values())

# The clients a drill speaks for. `check` opens every websocket one and polls
# the REST one; `serve` prints them so an outside client can use the same.
CLIENTS: dict[str, str] = {
    # the macOS agent and any web build from before chunking
    "world": "/ws",
    # the current web
    "web": "/ws?chunks=1",
    # the app in the foreground: every place, their countries, no floor
    "app_fg": f"/ws?zones={ZONES}&unplaced={COUNTRIES}&chunks=1",
    # the app's background socket: same places, a moderate floor
    "app_bg": f"/ws?zones={ZONES}&unplaced={COUNTRIES}&min_severity=moderate&chunks=1",
    # a zoned client that did not ask for unplaced alerts
    "zoned_drop": f"/ws?zones={ZONES}&chunks=1",
}
POLL = f"/api/events?zones={ZONES}&unplaced={COUNTRIES}&min_severity=moderate&limit=500"


# ------------------------------------------------------------------ isolation


def isolate() -> Path:
    """A fresh data dir, set BEFORE the app is imported: the store opens its
    journal at import time."""
    if any(name == "app" or name.startswith("app.") for name in sys.modules):
        raise SystemExit("drill: the app was imported before isolation; refusing to run")
    data = Path(tempfile.mkdtemp(prefix="sosforge-drill-"))
    os.environ["SOS_DATA_DIR"] = str(data)
    sys.path.insert(0, str(BACKEND))
    return data


def load_app(data: Path, with_sources: bool, verbose: bool = False) -> Any:
    import logging

    import app.main as main
    from app.core.config import settings

    if not verbose:
        # the timeline is the output; per-connection INFO lines drown it
        logging.getLogger().setLevel(logging.WARNING)

    store_dir = main.store._data_dir
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        settings.data_dir.resolve() != data.resolve()
        or store_dir is None
        or Path(store_dir).resolve() != data.resolve()
        or temp_root not in data.resolve().parents
    ):
        raise SystemExit(
            f"drill: refusing to run, the data dir is {settings.data_dir} and the store's is "
            f"{store_dir}, not the drill's own temp dir {data}"
        )
    if not with_sources:

        async def no_backfill(url: str, emit: Callable[..., Any]) -> int:
            return 0

        main.build_sources = lambda: []
        main.backfill = no_backfill
        settings.enable_felt_reports = False
    return main


# --------------------------------------------------------------- templating

TOKEN = re.compile(r"^@(iso|ms|jst)(?:([+-]\d+(?:\.\d+)?)([smhd]))?$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def resolve(value: Any, now: datetime, run: str, extra: dict[str, str] | None = None) -> Any:
    """`{run}` (and `{i}` in a burst) in every string; a string that is only
    `@iso-20s`, `@ms+1h` or `@jst-5s` becomes that time relative to `now`, in
    the format the named source actually uses."""
    if isinstance(value, dict):
        return {k: resolve(v, now, run, extra) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, now, run, extra) for v in value]
    if not isinstance(value, str):
        return value
    text = value.replace("{run}", run)
    for key, sub in (extra or {}).items():
        text = text.replace("{" + key + "}", sub)

    # a time token may sit inside a CAP `references` triple
    def one(token: str) -> Any:
        match = TOKEN.match(token)
        if not match:
            return token
        fmt, amount, unit = match.groups()
        when = now + timedelta(seconds=float(amount or 0) * UNITS.get(unit or "s", 1))
        if fmt == "ms":
            return int(when.timestamp() * 1000)
        if fmt == "jst":
            return when.astimezone(JST).strftime("%Y/%m/%d %H:%M:%S")
        return when.isoformat(timespec="seconds")

    if TOKEN.match(text):
        return one(text)
    if "@" in text and "," in text:
        return ",".join(str(one(part)) for part in text.split(","))
    return text


# ----------------------------------------------------------------- scenarios


@dataclass
class Scenario:
    path: Path
    name: str
    title: str
    steps: list[dict[str, Any]]
    expect: dict[str, Any]
    fresh: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Scenario:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path,
            raw["name"],
            raw.get("title", ""),
            raw["steps"],
            raw.get("expect", {}),
            raw.get("fresh", []),
        )

    def expectation(self, client: str, run: str) -> Any:
        value = self.expect.get(client)
        seen = set()
        while isinstance(value, str):  # "world": the same as another client
            if value in seen:
                raise ValueError(f"{self.name}: circular expectation {value}")
            seen.add(value)
            value = self.expect.get(value)
        return resolve(copy.deepcopy(value), datetime.now(UTC), run) if value is not None else None


def scenarios(names: list[str] | None) -> list[Scenario]:
    found = [Scenario.load(p) for p in sorted(DRILLS.glob("*.json")) if p.name != "homes.json"]
    if not names or names == ["all"]:
        return found
    chosen = [s for s in found if s.name in names or s.path.stem in names]
    missing = set(names) - {s.name for s in chosen} - {s.path.stem for s in chosen}
    if missing:
        raise SystemExit(f"drill: unknown scenario(s): {', '.join(sorted(missing))}")
    return chosen


# ----------------------------------------------------------------- timeline


def stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).strftime("%H:%M:%S.%f")[:-3] + "Z"


class Timeline:
    """Every non-tick message the hub fans out, with the server's clock."""

    def __init__(self, jsonl: Path | None):
        self.entries: list[dict[str, Any]] = []
        self.scenario = "-"
        self.jsonl = jsonl.open("a", encoding="utf-8") if jsonl else None

    def say(self, text: str) -> None:
        print(f"{stamp()}  {text}", flush=True)

    def record(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        entry = {
            "server_time": datetime.now(UTC).isoformat(),
            "scenario": self.scenario,
            "type": kind,
        }
        if kind in ("event", "update"):
            event = message.get("event") or {}
            entry.update(
                id=event.get("id"),
                severity=event.get("severity"),
                breaking=message.get("breaking"),
                escalated=message.get("escalated"),
                primary=message.get("primary"),
                alert=event.get("alert"),
            )
            flags = " ".join(k for k in ("breaking", "escalated") if message.get(k))
            if message.get("primary") is False:
                flags += " DUPLICATE"
            self.say(f"  -> {kind:6s} {event.get('id')} {event.get('severity')} {flags}".rstrip())
        elif kind == "purge":
            entry.update(ids=message.get("ids"), reason=message.get("reason"))
            self.say(f"  -> purge  {', '.join(message.get('ids') or [])} ({message.get('reason')})")
        self.entries.append(entry)
        if self.jsonl:
            self.jsonl.write(json.dumps(entry) + "\n")
            self.jsonl.flush()


# -------------------------------------------------------------------- player


class Player:
    """Turns scenario steps into calls on the real pipeline."""

    def __init__(self, main: Any, timeline: Timeline, run: str, speed: float):
        self.main = main
        self.timeline = timeline
        self.run = run
        self.speed = speed
        self.touched: dict[str, set[str]] = {}

    def normalize(self, step: dict[str, Any], payload: Any) -> tuple[list[Any], Any]:
        """(events, source-or-None) from one payload, through the real parser."""
        source = step["source"]
        if source == "usgs":
            from app.sources.usgs import parse_feature

            event = parse_feature(payload)
            return ([event] if event else []), None
        if source == "nws":
            from app.sources.nws import parse_feature as parse_nws

            event = parse_nws(payload)
            return ([event] if event else []), None
        if source == "jma_eew":
            from app.sources.eew import JmaEewSource

            eew = JmaEewSource()
            return eew.parse_payload(payload), eew
        if source == "meteoalarm":
            from app.sources.alerts_world import MeteoalarmSource

            meteoalarm = MeteoalarmSource()
            return meteoalarm.select({"warnings": [payload]}, step["country"]), meteoalarm
        raise SystemExit(f"drill: no adapter for source {source!r}")

    def burst_payloads(self, spec: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
        rng = random.Random(self.run)
        lat0, lon0 = spec["center"]
        lo, hi = spec["magnitude"]
        out = []
        for i in range(spec["count"]):
            # a uniform point in a disc, origin times 100 s apart going back
            r = spec["spread_km"] * math.sqrt(rng.random())
            theta = rng.random() * 2 * math.pi
            lat = lat0 + (r * math.cos(theta)) / 111.0
            lon = lon0 + (r * math.sin(theta)) / (111.0 * math.cos(math.radians(lat0)))
            fid = resolve(spec["id"], now, self.run, {"i": str(i)})
            place = resolve(spec["place"], now, self.run, {"i": str(i)})
            mag = round(lo + (hi - lo) * rng.random(), 1)
            origin = now - timedelta(seconds=60 + 100 * i)
            out.append(
                {
                    "type": "Feature",
                    "id": fid,
                    "properties": {
                        "mag": mag,
                        "place": place,
                        "time": int(origin.timestamp() * 1000),
                        "updated": int(now.timestamp() * 1000),
                        "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/{fid}",
                        "felt": None,
                        "cdi": None,
                        "mmi": None,
                        "alert": None,
                        "status": "automatic",
                        "tsunami": 0,
                        "sig": 150,
                        "magType": "ml",
                        "type": "earthquake",
                        "title": f"M {mag} - {place}",
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(lon, 4), round(lat, 4), 10.0],
                    },
                }
            )
        return out

    async def play(self, scenario: Scenario) -> None:
        self.timeline.scenario = scenario.name
        ids = self.touched.setdefault(scenario.name, set())
        self.timeline.say(f"== {scenario.name}: {scenario.title}")
        resolved: list[Any] = []
        start = time.monotonic()
        for index, step in enumerate(scenario.steps):
            delay = start + step.get("at", 0) / self.speed - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            now = datetime.now(UTC)
            label = step.get("note", "")
            if "repeat_step" in step:
                base = scenario.steps[step["repeat_step"]]
                payload = resolved[step["repeat_step"]]
                step = {**base, "note": label}
            elif "burst" in step:
                payload = None
            else:
                payload = resolve(step.get("payload"), now, self.run)
            resolved.append(payload)

            if "burst" in step:
                spec = step["burst"]
                features = self.burst_payloads(spec, now)
                self.timeline.say(f"step {index + 1}: {label} ({len(features)} x {spec['source']})")
                for feature in features:
                    events, _ = self.normalize({"source": spec["source"]}, feature)
                    for event in events:
                        ids.add(event.id)
                        await self.main.pipeline.emit(event)
                    if spec.get("every_s"):
                        await asyncio.sleep(spec["every_s"] / self.speed)
                continue

            if "retract" in step:
                event_id = resolve(step["retract"], now, self.run)
                ids.add(event_id)
                self.timeline.say(f"step {index + 1}: {label} (retract {event_id})")
                await self.main.pipeline.retract(event_id, step.get("reason", "cancelled"))
                continue

            events, source = self.normalize(step, payload)
            self.timeline.say(
                f"step {index + 1}: {label} ({step['source']} -> "
                f"{', '.join(e.id for e in events) or 'no event'})"
            )
            for event in events:
                ids.add(event.id)
                await self.main.pipeline.emit(event)
            if source is not None:
                pending = list(source.retractions) + list(source.expirations) + list(source.lifts)
                ids.update(pending)
                source.retract = self.main.pipeline.retract
                await source.flush_retractions()


# --------------------------------------------------------------------- probes


class Probe:
    """One real websocket client: reads its snapshot (and the chunks behind
    it), then keeps every message with the time it arrived."""

    def __init__(self, name: str, base: str, path: str):
        self.name = name
        self.url = base + path
        self.snapshot: list[dict[str, Any]] = []
        self.first: dict[str, Any] = {}
        self.snapshot_messages = 0
        self.messages: list[dict[str, Any]] = []
        self.task: asyncio.Task[None] | None = None
        self.ready = asyncio.Event()
        self.closed: tuple[int | None, str | None] | None = None

    async def run(self) -> None:
        import websockets

        try:
            async with websockets.connect(self.url, max_size=2**26) as ws:
                first = json.loads(await ws.recv())
                self.first = first
                self.snapshot = list(first.get("events") or [])
                self.snapshot_messages = 1
                remaining = first.get("more", 0)
                while remaining:
                    message = json.loads(await ws.recv())
                    if message.get("type") == "snapshot_more":
                        self.snapshot.extend(message.get("events") or [])
                        self.snapshot_messages += 1
                        remaining = message.get("remaining", 0)
                    else:
                        self.messages.append(message)
                self.ready.set()
                async for raw in ws:
                    self.messages.append(json.loads(raw))
        except websockets.ConnectionClosed as exc:
            self.closed = (
                exc.rcvd.code if exc.rcvd else None,
                exc.rcvd.reason if exc.rcvd else None,
            )
        finally:
            self.ready.set()

    async def start(self) -> None:
        self.task = asyncio.create_task(self.run())
        await asyncio.wait_for(self.ready.wait(), 10)

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task


def _http_get(url: str, etag: str | None = None) -> tuple[int, dict[str, Any] | None, str | None]:
    request = urllib.request.Request(url, headers={"If-None-Match": etag} if etag else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read()), response.headers.get("ETag")
    except urllib.error.HTTPError as err:
        return err.code, None, err.headers.get("ETag")


class AgentProbe:
    """The REAL macOS agent (agent/sosforge_agent), connected to the drill
    server, with its desktop notifier swapped for a recorder. Only the
    notifier is replaced: the proximity rule, the rate limiter, the history
    snapshot and the escalation logic are the agent's own."""

    def __init__(self, name: str, base: str, home: dict[str, Any], zone_alerts: bool):
        agent_root = ROOT / "agent"
        if str(agent_root) not in sys.path:
            sys.path.insert(0, str(agent_root))
        from sosforge_agent.agent import Agent
        from sosforge_agent.config import Config

        self.name = name
        self.notes: list[tuple[str | None, str, str]] = []
        self._current: dict[str, Any] = {}
        config = Config(
            lat=home["lat"],
            lon=home["lon"],
            label=name,
            country_code=home["country"],
            url=base + "/ws",
            zone_alerts=zone_alerts,
        )
        self.agent = Agent(config, notifier=self._notify)
        handle = self.agent.handle

        def remembering(message: dict[str, Any]) -> bool:
            self._current = message
            return handle(message)

        self.agent.handle = remembering  # type: ignore[method-assign]
        self.task: asyncio.Task[None] | None = None

    def _notify(self, title: str, body: str, *, urgent: bool = False) -> bool:
        event = self._current.get("event") or {}
        self.notes.append((event.get("id"), title, body))
        return True

    async def start(self) -> None:
        self.task = asyncio.create_task(self.agent.run_once())
        for _ in range(100):
            if self.agent.connected and self.agent._history_loaded:
                return
            await asyncio.sleep(0.05)
        raise SystemExit(f"drill: the agent probe {self.name} never got its snapshot")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task


async def http_get(
    url: str, etag: str | None = None
) -> tuple[int, dict[str, Any] | None, str | None]:
    """In a thread: the server runs on THIS event loop, and a blocking call
    here would wait for an answer the loop can never produce."""
    return await asyncio.to_thread(_http_get, url, etag)


def relevant(
    messages: list[dict[str, Any]], ids: set[str]
) -> list[tuple[str, str, dict[str, Any]]]:
    """(type, id, message) for every message about one of `ids`."""
    out = []
    for message in messages:
        kind = message.get("type")
        if kind in ("event", "update"):
            event_id = (message.get("event") or {}).get("id")
            if event_id in ids:
                out.append((kind, event_id, message))
        elif kind == "purge":
            for event_id in message.get("ids") or ():
                if event_id in ids:
                    out.append(("purge", event_id, message))
    return out


def matches(entry: list[Any], got: tuple[str, str, dict[str, Any]]) -> bool:
    kind, event_id, message = got
    want_kind, want_id = entry[0], entry[1]
    attrs = entry[2] if len(entry) > 2 else {}
    if event_id != want_id:
        return False
    if want_kind == "gone":
        # withdrawn either way: purged, or re-sent as lifted
        event = message.get("event") or {}
        return kind == "purge" or (kind == "update" and (event.get("alert") == "lifted"))
    if kind != want_kind:
        return False
    for key, value in attrs.items():
        if kind == "purge" or key in ("breaking", "escalated", "primary"):
            actual = message.get(key)
        else:
            actual = (message.get("event") or {}).get(key)
        if actual != value:
            return False
    return True


def describe(got: tuple[str, str, dict[str, Any]]) -> str:
    kind, event_id, message = got
    if kind == "purge":
        return f"purge {event_id} ({message.get('reason')})"
    event = message.get("event") or {}
    flags = "".join(f" {k}" for k in ("breaking", "escalated") if message.get(k))
    return f"{kind} {event_id} {event.get('severity')}{flags}"


def verdict_ws(expected: Any, got: list[tuple[str, str, dict[str, Any]]]) -> tuple[bool, str]:
    if isinstance(expected, dict) and "count" in expected:
        counts = {k: sum(1 for g in got if g[0] == k) for k in expected["count"]}
        ok = counts == expected["count"]
        return ok, f"counts {counts}, expected {expected['count']}"
    ok = len(got) == len(expected) and all(
        matches(e, g) for e, g in zip(expected, got, strict=False)
    )
    if ok:
        return True, "; ".join(describe(g) for g in got) or "nothing, as expected"
    want = (
        "; ".join(
            " ".join(str(x) for x in e[:2]) + (f" {e[2]}" if len(e) > 2 and e[2] else "")
            for e in expected
        )
        or "nothing"
    )
    return False, f"GOT [{'; '.join(describe(g) for g in got) or 'nothing'}] EXPECTED [{want}]"


# ------------------------------------------------------------------- serving


@dataclass
class Running:
    server: Any
    task: asyncio.Task[None]


async def start_server(main: Any, port: int) -> Running:
    """uvicorn on 127.0.0.1 only, in this loop. Its own signal handling stays:
    Ctrl-C stops the server, which ends `Running.task`."""
    import uvicorn

    config = uvicorn.Config(
        main.app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    # uvicorn catches SIGINT / SIGTERM to shut down gracefully, then re-raises
    # the signal once done; with the default handler that kills the process
    # before the temp data dir is removed. A no-op handler lets `serve()`
    # return, so the caller's cleanup runs. Stopping by port stays the rule.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: None)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        if task.done():
            task.result()
        await asyncio.sleep(0.05)
    else:
        raise SystemExit(f"drill: the server did not start on 127.0.0.1:{port}")
    return Running(server, task)


async def stop_server(running: Running) -> None:
    running.server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(running.task, 10)


def hook_timeline(main: Any, timeline: Timeline) -> None:
    hub = main.hub
    original = hub.broadcast

    async def recording(message: dict[str, Any]) -> None:
        if message.get("type") != "tick":
            timeline.record(message)
        await original(message)

    hub.broadcast = recording


def banner(port: int, data: Path, run: str, with_sources: bool) -> None:
    print(
        f"drill server on http://127.0.0.1:{port}  (run {run}, data {data}, "
        f"sources {'ON' if with_sources else 'off'})"
    )
    for name, path in CLIENTS.items():
        print(f"  {name:10s} ws://127.0.0.1:{port}{path}")
    print(f"  {'poll':10s} http://127.0.0.1:{port}{POLL}&since=")
    for name, home in HOMES.items():
        where = f"{home['lat']},{home['lon']} r={home['radius_km']} km"
        print(f"  home {name}: {where} ({home['country']})")
    sys.stdout.flush()


async def serve(args: argparse.Namespace) -> int:
    data = isolate()
    try:
        main = load_app(data, args.with_sources, args.verbose)
        run = args.run or uuid.uuid4().hex[:6]
        timeline = Timeline(Path(args.timeline) if args.timeline else None)
        hook_timeline(main, timeline)
        server = await start_server(main, args.port)
        banner(args.port, data, run, args.with_sources)
        try:
            if args.wait_clients:
                timeline.say(f"waiting for {args.wait_clients} websocket client(s)")
                while main.hub.client_count < args.wait_clients:
                    await asyncio.sleep(0.2)
            if args.delay:
                await asyncio.sleep(args.delay)
            player = Player(main, timeline, run, args.speed)
            for _ in range(args.repeat):
                for scenario in scenarios(args.scenario):
                    await player.play(scenario)
                    await asyncio.sleep(args.gap / args.speed)
                player.run = uuid.uuid4().hex[:6]
            timeline.say(
                "drills played; still serving (Ctrl-C to stop)"
                if not args.exit
                else "drills played"
            )
            if not args.exit:
                await server.task  # until Ctrl-C stops the server
        finally:
            await stop_server(server)
    finally:
        if not args.keep_data:
            shutil.rmtree(data, ignore_errors=True)
    return 0


async def check(args: argparse.Namespace) -> int:
    data = isolate()
    failures = 0
    rows: list[tuple[str, str, bool, str]] = []
    try:
        main = load_app(data, args.with_sources, args.verbose)
        run = args.run or uuid.uuid4().hex[:6]
        timeline = Timeline(Path(args.timeline) if args.timeline else None)
        hook_timeline(main, timeline)
        server = await start_server(main, args.port)
        base_ws = f"ws://127.0.0.1:{args.port}"
        base_http = f"http://127.0.0.1:{args.port}"
        banner(args.port, data, run, args.with_sources)
        probes = {name: Probe(name, base_ws, path) for name, path in CLIENTS.items()}
        agents = {
            "agent_tokyo": AgentProbe("agent_tokyo", base_ws, HOMES["tokyo"], zone_alerts=False),
            "agent_paris": AgentProbe("agent_paris", base_ws, HOMES["paris"], zone_alerts=True),
        }
        try:
            for probe in probes.values():
                await probe.start()
            for agent in agents.values():
                await agent.start()
            player = Player(main, timeline, run, args.speed)
            for scenario in scenarios(args.scenario):
                marks = {name: len(p.messages) for name, p in probes.items()}
                agent_marks = {name: len(a.notes) for name, a in agents.items()}
                status, body, _ = await http_get(f"{base_http}{POLL}&since=")
                cursor = (body or {}).get("cursor")
                await player.play(scenario)
                await asyncio.sleep(args.settle)
                ids = player.touched.get(scenario.name, set())
                for name, probe in probes.items():
                    expected = scenario.expectation(name, run)
                    if expected is None:
                        continue
                    ok, detail = verdict_ws(expected, relevant(probe.messages[marks[name] :], ids))
                    rows.append((scenario.name, name, ok, detail))
                for name, agent in agents.items():
                    expected = scenario.expectation(name, run)
                    if expected is None:
                        continue
                    notes = agent.notes[agent_marks[name] :]
                    got = [n[0] for n in notes if n[0] in ids or n[0] is None]
                    ok = got == expected
                    detail = "; ".join(f"{n[0]}: {n[1]}" for n in notes) or "silent"
                    if not ok:
                        detail += f" EXPECTED notifications for {expected or 'nothing'}"
                    rows.append((scenario.name, name, ok, detail))
                rows.extend(await check_poll(scenario, run, ids, base_http, cursor))
                for spec in scenario.fresh:
                    rows.append(await check_fresh(scenario, spec, ids, base_ws))
                await asyncio.sleep(args.gap / args.speed)
            rows.extend(await check_refusals(base_ws, main))
        finally:
            for probe in probes.values():
                await probe.stop()
            for agent in agents.values():
                await agent.stop()
            await stop_server(server)
    finally:
        if not args.keep_data:
            shutil.rmtree(data, ignore_errors=True)

    print()
    print(f"{'scenario':20s} {'client':11s} result")
    for scenario_name, client, ok, detail in rows:
        failures += not ok
        print(f"{scenario_name:20s} {client:11s} {'PASS' if ok else 'FAIL'}  {detail}")
    print(f"\n{len(rows) - failures} passed, {failures} failed")
    return 1 if failures else 0


async def check_poll(
    scenario: Scenario, run: str, ids: set[str], base: str, cursor: str | None
) -> list[tuple[str, str, bool, str]]:
    expected = scenario.expectation("poll", run)
    if expected is None or cursor is None:
        return []
    status, body, etag = await http_get(f"{base}{POLL}&since={cursor}")
    if status != 200 or body is None:
        return [(scenario.name, "poll", False, f"HTTP {status}")]
    events = sorted(e["id"] for e in body["events"] if e["id"] in ids)
    removed = sorted(i for i in body["removed"] if i in ids)
    if "events_count" in expected:
        ok = len(events) == expected["events_count"] and removed == sorted(expected["removed"])
    else:
        ok = events == sorted(expected["events"]) and removed == sorted(expected["removed"])
    again, _, _ = await http_get(f"{base}{POLL}&since={cursor}", etag)
    detail = (
        f"upserts {events or '[]'} removed {removed or '[]'} reset={body['reset']} "
        f"then {again} on If-None-Match"
    )
    ok = ok and not body["reset"] and again == 304
    if not ok:
        detail += f" EXPECTED {expected} then 304"
    return [(scenario.name, "poll", ok, detail)]


async def check_fresh(
    scenario: Scenario, spec: dict[str, Any], ids: set[str], base: str
) -> tuple[str, str, bool, str]:
    probe = Probe(spec["client"], base, CLIENTS[spec["client"]])
    await probe.start()
    await probe.stop()
    got = [e for e in probe.snapshot if e.get("id") in ids]
    ok = True
    detail = f"fresh snapshot: {len(got)} drill events in {probe.snapshot_messages} message(s)"
    if "ids_min" in spec:
        ok &= len(got) >= spec["ids_min"]
    if "messages_min" in spec:
        ok &= probe.snapshot_messages >= spec["messages_min"]
    if "messages_max" in spec:
        ok &= probe.snapshot_messages <= spec["messages_max"]
    if "unplaced_dropped_min" in spec:
        dropped = (probe.first.get("unplaced") or {}).get("dropped", 0)
        ok &= dropped >= spec["unplaced_dropped_min"]
        detail += f", unplaced.dropped={dropped}"
    if not ok:
        detail += f" EXPECTED {spec}"
    return (scenario.name, f"{spec['client']}*", ok, detail)


async def check_refusals(base: str, main: Any) -> list[tuple[str, str, bool, str]]:
    """Bad parameters are refused with a reason and close 1008; past the
    per-address quota a socket is closed 1013."""
    import websockets

    rows = []
    for query, code in (
        ("zones=91,0,10", "bad_zones"),
        ("chunks=2", "bad_chunks"),
        ("zone=1,1,1", "unknown_param"),
        ("min_severity=huge", "bad_min_severity"),
    ):
        got_code = got_close = None
        try:
            async with websockets.connect(f"{base}/ws?{query}") as ws:
                got_code = json.loads(await ws.recv()).get("code")
                await ws.recv()
        except websockets.ConnectionClosed as exc:
            got_close = exc.rcvd.code if exc.rcvd else None
        ok = got_code == code and got_close == 1008
        rows.append(("refusal", query, ok, f"error {got_code}, close {got_close}"))

    # The probes already hold some of this address's sockets: open until the
    # server refuses, and say which one it was.
    held = []
    quota = main.settings.max_ws_per_ip
    refused: Any = None
    opened_before = main.hub.client_count
    try:
        for _ in range(quota + 1):
            ws = await websockets.connect(f"{base}/ws", max_size=2**26)
            held.append(ws)
            await asyncio.wait_for(ws.recv(), 5)  # its snapshot
    except websockets.InvalidStatus as exc:
        refused = f"HTTP {exc.response.status_code} on the handshake"
    except websockets.ConnectionClosed as exc:
        refused = exc.rcvd.code if exc.rcvd else None
    finally:
        for ws in held:
            await ws.close()
    nth = opened_before + len(held) + 1
    ok = refused == 1013
    detail = f"socket {nth} from this address: {refused}"
    if not ok:
        detail += " (the endpoint means close 1013 'try again later')"
    rows.append(("refusal", f"quota {quota}", ok, detail))
    return rows


# ---------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="the scenarios, the homes and the client URLs")
    for name in ("serve", "check"):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int, default=DEFAULT_PORT)
        p.add_argument("--scenario", nargs="*", help="names or file stems; default all")
        p.add_argument(
            "--with-sources",
            action="store_true",
            help="keep the real sources and the USGS backfill running",
        )
        p.add_argument("--speed", type=float, default=1.0, help="timeline speed factor")
        p.add_argument("--gap", type=float, default=3.0, help="seconds between scenarios")
        p.add_argument("--run", help="fixed run id (default: random)")
        p.add_argument("--timeline", help="also append the timeline as JSONL to this file")
        p.add_argument("--keep-data", action="store_true", help="keep the temp data dir")
        p.add_argument("--verbose", action="store_true", help="keep the app's INFO logs")
        if name == "serve":
            p.add_argument(
                "--wait-clients",
                type=int,
                default=0,
                help="play only once this many websockets are open",
            )
            p.add_argument("--delay", type=float, default=0.0, help="seconds before playing")
            p.add_argument("--repeat", type=int, default=1, help="play the whole set N times")
            p.add_argument("--exit", action="store_true", help="exit once played")
        else:
            p.add_argument(
                "--settle",
                type=float,
                default=1.5,
                help="seconds to wait for messages after a scenario",
            )
    args = parser.parse_args()

    if args.command == "list":
        for scenario in scenarios(None):
            print(f"{scenario.path.stem:24s} {scenario.title}")
        banner(DEFAULT_PORT, Path("<temp>"), "<random>", False)
        return 0
    runner = serve if args.command == "serve" else check
    try:
        return asyncio.run(runner(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
