"""`python -m sosforge_agent` -- set it up once, then let it run.

Deliberately not a GUI. A menu-bar app would mean a UI framework, a render
loop and tens of megabytes resident, to display one icon. The agent's job is
to be invisible until it has something to say.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import urllib.request
from pathlib import Path

from .agent import Agent, main_async
from .config import DEFAULT_PATH, DEFAULT_URL, Config
from .notify import notify


def locate() -> tuple[float, float, str, str | None] | None:
    """Rough position from the IP address, as a STARTING POINT only.

    City-level and sometimes wrong by a country -- which is why it is offered
    as a default to confirm, never used silently. Anyone who wants precision
    types their coordinates; this exists so the first run is not a chore.
    """
    try:
        with urllib.request.urlopen("https://ipapi.co/json/", timeout=8) as response:
            data = json.load(response)
        lat, lon = float(data["latitude"]), float(data["longitude"])
        label = f"{data.get('city') or '?'}, {data.get('country_name') or '?'}"
        return lat, lon, label, str(data.get("country_code") or "") or None
    except Exception:  # noqa: BLE001 -- a best-effort convenience, never a dependency
        return None


def cmd_setup(args: argparse.Namespace) -> int:
    path = Path(args.config)
    lat, lon, label = args.lat, args.lon, args.label
    country = args.country

    if lat is None or lon is None:
        guess = locate()
        if guess is None:
            print("Could not guess a position. Pass --lat and --lon.", file=sys.stderr)
            return 2
        lat, lon, guessed, guessed_country = guess
        label = label or guessed
        country = country or guessed_country
        print(f"Approximate position from your IP: {lat:.3f}, {lon:.3f} ({guessed})")
        print("City-level and occasionally wrong by a country -- pass --lat/--lon to be exact.")

    config = Config(
        lat=lat,
        lon=lon,
        label=label or "home",
        url=args.url,
        country_code=country,
        min_severity=args.min_severity,
        max_distance_km=args.radius,
    )
    config.save(path)
    print(f"Saved to {path}")
    print(f"  position     {config.lat:.4f}, {config.lon:.4f}  ({config.label})")
    print(f"  feed         {config.url}")
    print(f"  speaks about {config.min_severity} and above, within {config.max_distance_km:.0f} km")
    print(f"  country      {config.country_code or '(none -- zone alerts will be ignored)'}")
    print(f"  ceiling      {config.max_per_hour} notifications per hour")
    print("\nTry it:   python -m sosforge_agent test")
    print("Run it:   python -m sosforge_agent run")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Proves the notification actually reaches the screen.

    Worth its own command: on macOS the first notification is what triggers
    the permission prompt, and an agent whose permission was never granted is
    an agent that fails silently for months.
    """
    ok = notify(
        "🌍 SOSForge agent is running",
        "This is a test. Real alerts look like this one.",
        urgent=False,
    )
    print("notification delivered" if ok else "the platform refused the notification")
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if not path.exists():
        print(f"No configuration at {path}. Run: python -m sosforge_agent setup", file=sys.stderr)
        return 2
    config = Config.load(path)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    # --verbose is for OUR decisions -- why this event rang and that one did
    # not. The websockets library at DEBUG logs every frame, which buries
    # exactly that and turns a quiet log file into a growing one.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("sosforge.agent").info(
        "watching %.4f, %.4f (%s) -- %s and above within %.0f km",
        config.lat,
        config.lon,
        config.label,
        config.min_severity,
        config.max_distance_km,
    )
    try:
        asyncio.run(main_async(config))
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Replays the last hour of the real feed and says what it WOULD have said.

    The honest way to choose a threshold: not by guessing, but by seeing how
    often this machine would actually have buzzed.
    """
    path = Path(args.config)
    if not path.exists():
        print(f"No configuration at {path}. Run: python -m sosforge_agent setup", file=sys.stderr)
        return 2
    config = Config.load(path)
    api = config.url.replace("wss://", "https://").replace("ws://", "http://").removesuffix("/ws")
    url = f"{api}/api/events?limit=500&hours={args.hours}"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)
    events = payload["events"] if isinstance(payload, dict) else payload

    spoken: list[str] = []
    # The hourly ceiling is a real-time protection; applying it to a replay of
    # a whole day would silently truncate the very count this command exists
    # to show.
    from dataclasses import replace

    agent = Agent(replace(config, max_per_hour=10**6), notifier=lambda *a, **k: True)
    for event in events:
        if agent.handle({"type": "event", "event": event, "primary": True, "breaking": False}):
            spoken.append(f"  {event.get('severity', '?'):9s} {str(event.get('place'))[:56]}")

    print(f"Over the last {args.hours} h, {len(events)} events reached the feed.")
    print(f"This machine would have notified you {len(spoken)} time(s):")
    print("\n".join(spoken) if spoken else "  (nothing -- quiet where you are)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sosforge-agent", description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="write the configuration file")
    setup.add_argument("--lat", type=float)
    setup.add_argument("--lon", type=float)
    setup.add_argument("--label", default=None)
    setup.add_argument(
        "--country",
        default=None,
        help="ISO2, e.g. FR. Used for alerts published without coordinates",
    )
    setup.add_argument("--url", default=DEFAULT_URL)
    setup.add_argument(
        "--min-severity",
        default="moderate",
        choices=["info", "minor", "moderate", "severe", "extreme"],
    )
    setup.add_argument(
        "--radius", type=float, default=300.0, help="km, for hazards that are not earthquakes"
    )
    setup.set_defaults(func=cmd_setup)

    sub.add_parser("test", help="send one test notification").set_defaults(func=cmd_test)

    run = sub.add_parser("run", help="listen to the feed until stopped")
    run.add_argument("--verbose", action="store_true")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="replay the recent feed and show what it would have said")
    check.add_argument("--hours", type=float, default=24.0)
    check.set_defaults(func=cmd_check)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
