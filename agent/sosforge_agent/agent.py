"""The agent itself: one websocket, one process, no polling.

Resource behaviour is the point here, so it is worth saying what makes it
cheap. There is no browser, no renderer, no timer loop, no local database.
The process sleeps inside `recv()` and the kernel wakes it when a frame
arrives -- about one small JSON object per second from the server's heartbeat,
which we drop unless it says something changed. What that costs is MEASURED in
the README, not asserted here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Callable

import websockets

from .config import Config
from .notify import notify as desktop_notify
from .proximity import Decision, should_notify

log = logging.getLogger("sosforge.agent")

KIND_ICON = {
    "earthquake": "🌍",
    "tsunami": "🌊",
    "volcano": "🌋",
    "cyclone": "🌀",
    "flood": "💧",
    "wildfire": "🔥",
    "storm": "⛈️",
    "heat": "🌡️",
    "space_weather": "☀️",
}

# The feed emits a tick every second. Fifteen seconds of silence is not a slow
# network, it is a dead connection -- and an agent that believes it is
# connected is an agent that is silently protecting nobody.
SILENCE_TIMEOUT_S = 15.0


class RateLimiter:
    """A hard ceiling on notifications per hour.

    Not politeness: survival. A seismic swarm or a storm outbreak produces
    dozens of qualifying events in minutes, and a machine that buzzes forty
    times gets its notifications switched off by the person using it -- the
    one outcome this agent must never cause.
    """

    def __init__(self, max_per_hour: int):
        self.max_per_hour = max_per_hour
        self._stamps: deque[float] = deque()

    def allow(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        while self._stamps and now - self._stamps[0] > 3600:
            self._stamps.popleft()
        if len(self._stamps) >= self.max_per_hour:
            return False
        self._stamps.append(now)
        return True


def format_event(event: dict, decision: Decision) -> tuple[str, str]:
    kind = str(event.get("kind") or "other")
    icon = KIND_ICON.get(kind, "⚠️")
    magnitude = event.get("magnitude")
    severity = str(event.get("severity") or "info").upper()

    if kind == "earthquake" and magnitude is not None:
        head = f"{icon} M{magnitude} earthquake"
    elif kind == "earthquake":
        head = f"{icon} Earthquake -- {severity}"
    else:
        head = f"{icon} {kind.replace('_', ' ').title()} -- {severity}"

    where = str(event.get("place") or "unknown location")
    if decision.distance_km is not None:
        where = f"{where} -- {decision.distance_km:.0f} km from you"

    detail = [where]
    if event.get("preliminary"):
        # Say it. The first automatic solution of a large quake is routinely
        # off by up to a magnitude unit, and a screenshot of the wrong number
        # travels further than the correction ever does.
        detail.append("preliminary, may be revised")
    depth = event.get("depth_km")
    if isinstance(depth, (int, float)):
        detail.append(f"depth {depth:.0f} km")
    return head, " · ".join(detail)


class Agent:
    def __init__(self, config: Config, notifier: Callable[..., bool] = desktop_notify):
        self.config = config
        self.notifier = notifier
        self.limiter = RateLimiter(config.max_per_hour)
        # Ids already announced. Revisions arrive constantly -- an early
        # warning is re-issued every second or two, USGS revises magnitudes for
        # hours -- and not one of them may ring again.
        self._announced: set[str] = set()
        self.connected = False
        self.notified = 0
        self.considered = 0

    def handle(self, message: dict) -> bool:
        """One server message. Returns whether it produced a notification."""
        kind = message.get("type")

        if kind == "snapshot":
            # The backlog is history, not news. Remembering it is exactly what
            # stops the agent from firing three hundred notifications the
            # second it starts.
            for event in message.get("events") or []:
                self._announced.add(str(event.get("id")))
            log.info("connected -- %d events already known", len(self._announced))
            return False

        if kind == "purge":
            for event_id in message.get("ids") or []:
                self._announced.discard(str(event_id))
            return False

        if kind not in ("event", "update"):
            return False

        event = message.get("event") or {}
        event_id = str(event.get("id"))
        if event_id in self._announced:
            return False

        self.considered += 1
        decision = should_notify(
            event,
            self.config.lat,
            self.config.lon,
            min_severity=self.config.min_severity,
            max_distance_km=self.config.max_distance_km,
            home_country=self.config.country_code,
        )
        self._announced.add(event_id)

        if not decision.notify:
            log.debug("silent: %s (%s)", event.get("place"), decision.reason)
            return False

        if not self.limiter.allow():
            log.warning("rate limit reached, holding back: %s", event.get("place"))
            return False

        title, body = format_event(event, decision)
        urgent = str(event.get("severity")) in ("severe", "extreme") and self.config.sound
        self.notifier(title, body, urgent=urgent)
        self.notified += 1
        log.info("NOTIFIED %s -- %s (%s)", title, body, decision.reason)
        return True

    async def run_once(self) -> None:
        """One connection, until it dies."""
        async with websockets.connect(self.config.url, ping_interval=20) as socket:
            self.connected = True
            log.info("listening to %s", self.config.url)
            try:
                while True:
                    raw = await asyncio.wait_for(socket.recv(), timeout=SILENCE_TIMEOUT_S)
                    try:
                        self.handle(json.loads(raw))
                    except (ValueError, TypeError) as exc:
                        # One malformed frame must not take the connection with
                        # it; the next one is a second away.
                        log.warning("unreadable message: %s", exc)
            finally:
                self.connected = False

    async def run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                await self.run_once()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- ANY failure means retry
                # Deliberately blind: DNS, TLS, a proxy, a closed laptop lid.
                # There is no failure mode where giving up is better than
                # reconnecting, and an agent that exits is one that protects
                # nobody until someone notices.
                log.warning("disconnected (%s), retrying in %.0f s", exc, delay)
            await asyncio.sleep(delay)
            # Capped backoff: a laptop waking with no network must not spin,
            # and one whose network just came back must not wait ten minutes
            # before it starts protecting again.
            delay = min(delay * 2, 60.0)


async def main_async(config: Config, notifier: Callable[..., bool] = desktop_notify) -> None:
    agent = Agent(config, notifier)
    with contextlib.suppress(asyncio.CancelledError):
        await agent.run_forever()
