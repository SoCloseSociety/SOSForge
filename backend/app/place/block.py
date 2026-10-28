"""The envelope every block of the place report travels in.

One block = one dataset = one source and one `as_of`, the DATASET's own time
(an observation time, a model run, an issue time), never the time we read
it. `fetched_at` is our read, shown apart. A block that cannot be fetched is
`unavailable` with the real reason; a block that does not apply here is
`not_applicable`. Neither ever carries a value: `data` is null unless the
status is `ok`, and an unknown value inside `data` is null, never 0 and never
the lowest class.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.place import messages

STATUSES = ("ok", "unavailable", "not_applicable")
NATURES = ("observation", "forecast", "model", "monitoring", "static", "catalogue", "live")
AS_OF_KINDS = (
    "observed",
    "issued",
    "model_run",
    "valid_for",
    "period_start",
    "through",
    "published",
    "live",
)
UNAVAILABLE = (
    "timeout",
    "budget",
    "busy",
    "upstream_http",
    "upstream_unreachable",
    "upstream_parse",
    "stale_upstream",
    "no_coverage",
    "no_station",
    "coast_not_resolved",
    "masked",
    "warming",
    # our own processing of a good download did not finish (IBTrACS: the
    # build process killed or out of memory): never blamed on the source
    "build_failed",
)
NOT_APPLICABLE = ("inland", "at_sea", "no_country")


def iso(moment: datetime | None) -> str | None:
    """ISO 8601 UTC with `Z`, whole seconds."""
    if moment is None:
        return None
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    attribution: str
    licence: str
    # A second attribution the data of THIS answer requires (ISC-GEM rows in
    # the USGS catalogue). Empty for every other block.
    also: tuple[Source, ...] = ()

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
            "attribution": self.attribution,
            "licence": self.licence,
        }
        if self.also:
            out["also"] = [s.public() for s in self.also]
        return out


@dataclass(frozen=True)
class Spec:
    """What a block is, whatever this report finds."""

    id: str
    nature: str
    as_of_kind: str
    max_age_s: float | None
    source: Source


@dataclass
class Problem:
    """Why a block has no value (or why a refresh failed). `reason` is for
    operators, in English, and never holds a coordinate or a URL; `params`
    fill the localized sentence."""

    code: str
    reason: str
    params: dict[str, Any] = field(default_factory=dict)

    def public(self, lang: str) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": self.reason,
            "message": messages.message(self.code, lang, self.params),
        }


@dataclass
class Block:
    spec: Spec
    status: str
    as_of: datetime | None = None
    fetched_at: datetime | None = None
    data: dict[str, Any] | None = None
    error: Problem | None = None
    missing: list[dict[str, str]] = field(default_factory=list)
    source: Source | None = None
    # The block's own staleness rule, on top of `as_of + max_age_s`
    # (fire danger: a run three days old still renders "today").
    stale_rule: bool = False
    nature: str | None = None
    as_of_kind: str | None = None

    def stale(self, now: datetime) -> bool:
        if self.status != "ok":
            return False
        if self.stale_rule:
            return True
        if self.as_of is None or self.spec.max_age_s is None:
            return False
        return now - self.as_of > timedelta(seconds=self.spec.max_age_s)

    def public(self, now: datetime, lang: str) -> dict[str, Any]:
        ok = self.status == "ok"
        return {
            "status": self.status,
            "nature": self.nature or self.spec.nature,
            "as_of": iso(self.as_of) if ok else None,
            "as_of_kind": self.as_of_kind or self.spec.as_of_kind,
            "stale": self.stale(now),
            "max_age_s": self.spec.max_age_s,
            "fetched_at": iso(self.fetched_at),
            "source": (self.source or self.spec.source).public(),
            "error": self.error.public(lang) if self.error else None,
            "missing": list(self.missing),
            "data": self.data if ok else None,
        }


def ok(
    spec: Spec,
    as_of: datetime,
    data: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
    missing: list[dict[str, str]] | None = None,
    error: Problem | None = None,
    source: Source | None = None,
    stale_rule: bool = False,
    nature: str | None = None,
    as_of_kind: str | None = None,
) -> Block:
    return Block(
        spec,
        "ok",
        as_of=as_of,
        fetched_at=fetched_at,
        data=data,
        error=error,
        missing=missing or [],
        source=source,
        stale_rule=stale_rule,
        nature=nature,
        as_of_kind=as_of_kind,
    )


def unavailable(
    spec: Spec,
    code: str,
    reason: str,
    *,
    fetched_at: datetime | None = None,
    source: Source | None = None,
    **params: Any,
) -> Block:
    assert code in UNAVAILABLE, code
    return Block(
        spec,
        "unavailable",
        fetched_at=fetched_at,
        error=Problem(code, reason, params),
        source=source,
    )


def not_applicable(spec: Spec, code: str, reason: str, **params: Any) -> Block:
    assert code in NOT_APPLICABLE, code
    return Block(spec, "not_applicable", error=Problem(code, reason, params))


# ------------------------------------------------------------- unknown values
#
# Every sentinel a source uses for "no value" becomes None at parse time. A
# real 0 stays 0: GDO rdria 0 is "No risk", INFORM 0.0 is a score, CAMS O3 0.0
# at night is a model value, SMOC 0.0 m is a level at the global mean sea level.

SENTINEL_TEXT = frozenset({"", "MM", "NaN", "nan", "N/A", "No Data", "NoData", "-"})
SENTINEL_NUMBERS = frozenset({-9999.0, -999.0, -327.68})


def number(value: Any, *, sentinels: frozenset[float] = SENTINEL_NUMBERS) -> float | None:
    """A finite float, or None for every "no value" spelling."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text in SENTINEL_TEXT:
            return None
        try:
            out = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        out = float(value)
    else:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(out) or out in sentinels:
        return None
    return out


def rounded(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def years_of(moment: datetime | None) -> str:
    """The year a "Generated using Copernicus ... information {year}" notice
    takes: the data's own, computed at run time, never a literal."""
    return str((moment or datetime.now(UTC)).astimezone(UTC).year)
