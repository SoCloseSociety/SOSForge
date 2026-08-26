"""Where the agent lives and what it cares about.

One JSON file, written once. No service, no account, no telemetry: the
position never leaves the machine -- it is compared locally against a feed
that is public anyway.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path.home() / ".config" / "sosforge" / "agent.json"
DEFAULT_URL = "wss://sosforge.soclose.co/ws"


@dataclass
class Config:
    lat: float
    lon: float
    label: str = "home"
    # ISO2. The only locator an alert without coordinates carries -- and
    # without it such an alert cannot be judged at all (see proximity.py).
    country_code: str | None = None
    url: str = DEFAULT_URL
    min_severity: str = "moderate"
    max_distance_km: float = 300.0
    # Never more than this many notifications in an hour, whatever happens.
    # A swarm or a storm outbreak can produce dozens of qualifying events in
    # minutes, and a machine that buzzes forty times gets muted for good.
    max_per_hour: int = 12
    sound: bool = True
    # Alerts published with NO coordinates at all -- most of the WMO aggregate,
    # some national bulletins. Country is the only locator they carry, which is
    # a fine proxy for France and a useless one for the United States: measured
    # on a real day of the feed, someone in Los Angeles would have been woken
    # 63 times by marine warnings in Michigan, Florida and the Carolinas, while
    # someone in Paris got 2 genuinely local ones. Severity does not separate
    # them either -- all 63 were "extreme" too.
    #
    # So it is a choice, defaulted to silence, and `check` prints what turning
    # it on would cost YOU before you decide. The real remedy is upstream:
    # giving those alerts a position, the way NWS zones now are.
    zone_alerts: bool = False

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> Config:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path = DEFAULT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
