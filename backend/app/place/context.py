"""The state one report carries: the rounded point, the place, the clock,
and the client's rate token (spent at most once, and only by a NEW fetch)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.place.fetch import Refused
from app.place.geo import Place


@dataclass
class Context:
    lat: float
    lon: float
    radius_km: float
    lang: str
    now: datetime
    place: Place
    # 0 when this client may start a new upstream fetch (and spends its
    # token), else the seconds until it may. None: not rate limited (tests).
    take_token: Callable[[], float] | None = None
    claimed: bool | None = None
    retry_after: float = 0.0
    # block id -> the code to report if the budget runs out while it waits
    # ("warming" for a global file's first fetch); default "budget"
    pending: dict[str, str] = field(default_factory=dict)

    def claim(self) -> None:
        """Called by the cache when it is about to start a NEW upstream fetch.
        A cached block, a remembered failure, or joining a fetch in flight
        never gets here, so they cost nothing."""
        if self.take_token is None:
            return
        if self.claimed is None:
            wait = self.take_token()
            self.claimed = wait <= 0
            self.retry_after = wait
        if not self.claimed:
            raise Refused(self.retry_after)

    def waiting(self, block_id: str, code: str) -> Callable[[], None]:
        def mark() -> None:
            self.pending[block_id] = code

        return mark

    @property
    def refused(self) -> bool:
        return self.claimed is False
