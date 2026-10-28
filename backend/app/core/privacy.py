"""Where people are does not go into the logs.

The v2 contract puts a person's places in the URL: `/ws?zones=lat,lon,radius`,
`/api/events?lat=&lon=&radius_km=`, `unplaced=FR,JP`, and a place searched
by name in `/api/geocode?q=`. uvicorn logs the full path with its query
string, on every request (`uvicorn.access`) and on every websocket accepted
or refused (`uvicorn.error`), so the server's log was a list of who watches
which home, kept as long as the log is.

Every query value is blanked before a record is formatted, except for the
parameters known to carry no place. An allowlist, not a blocklist: a
misspelled `zone=` is refused by the API but carries a home all the same.
The parameter NAMES, the path and the status stay, which is everything an
operator needs to read a log ("a zoned socket was refused 1008").
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from urllib.parse import unquote_plus, urlsplit

from app.changelog import CURSOR
from app.guidance import Lang
from app.models.event import Kind, Severity

# A parameter NAME is kept only if it is a plain lowercase word: a name is
# as free as a value, and `/ws?48.8566,2.3522,25=` or an encoded
# `zones%3D48.85%2C2.35%2C25=1` put a home in the name (recheck of PR #5).
_NAME = re.compile(r"^[a-z_]{1,32}$")
_KINDS = frozenset(k.value for k in Kind)
_SEVERITIES = frozenset(s.value for s in Severity)
_LANGS = frozenset(lang.value for lang in Lang)
_BOOLS = frozenset({"true", "false", "1", "0", "yes", "no", "on", "off"})
_INT = re.compile(r"^\d{1,4}$")
_ONE_DECIMAL = re.compile(r"^-?\d{1,3}(\.\d)?$")


def _bounded(pattern: re.Pattern[str], low: float, high: float) -> Callable[[str], bool]:
    def check(value: str) -> bool:
        return bool(pattern.match(value)) and low <= float(value) <= high

    return check


def _kinds(value: str) -> bool:
    return bool(value) and all(part in _KINDS for part in value.split(","))


# The parameters whose values say nothing about where anyone is, each kept
# only inside its real vocabulary or range. Free text of the right
# alphabet is not enough: `lang=Montmartre` and `min_severity=Rue_de_Rivoli`
# are words, and a 3-decimal `hours=48.857&limit=2.352` is a position. The
# numbers keep their API's own bounds, which no coordinate pair fits inside
# beyond one decimal (about 11 km) and |value| <= 12.9 for one of the two.
SAFE: dict[str, Callable[[str], bool]] = {
    "chunks": lambda v: v == "1",
    "tick": lambda v: v == "lite",
    "lang": lambda v: v in _LANGS,
    "min_severity": lambda v: v in _SEVERITIES,
    "kind": lambda v: v in _KINDS,
    "kinds": _kinds,
    "primary_only": lambda v: v.lower() in _BOOLS,
    "limit": _bounded(_INT, 1, 2000),
    "hours": _bounded(_ONE_DECIMAL, 0, 720),
    # negative on purpose: IMO publishes magnitudes below zero
    "min_magnitude": _bounded(_ONE_DECIMAL, -9.9, 12.9),
    "since": lambda v: bool(CURSOR.match(v)),
}
BLANK = "-"
# The loggers uvicorn writes request paths to.
LOGGERS = ("uvicorn.access", "uvicorn.error")
# The one outbound request that carries what a person typed: the place
# search. httpx logs every request URL at INFO on its own logger.
PRIVATE_HOSTS = ("https://nominatim.openstreetmap.org/",)
# Hosts whose URLs are logged as `scheme://host` and nothing else: the place
# report's upstreams (app/place/fetch.py registers them). Blanking the query
# is not enough there, the point also travels in PATHS (NWS
# `/points/61.2180,-149.9000`, `/gridpoints/AER/143,236`).
HOST_ONLY: set[str] = set()


def host_only(hosts: Iterable[str]) -> None:
    """Registers hosts whose logged URLs keep only scheme and host."""
    HOST_ONLY.update(h.lower() for h in hosts)


def redact_query(path: str) -> str:
    """The path with every query value blanked to `-`, except a SAFE
    parameter's value inside its vocabulary. A part whose name is not a
    plain lowercase word is blanked whole, name included; a part with no
    '=' is kept only if it is a bare SAFE name (or empty)."""
    base, sep, query = path.partition("?")
    if not sep:
        return path
    parts: list[str] = []
    for part in query.split("&"):
        if not part:
            parts.append(part)
            continue
        name, eq, value = part.partition("=")
        key = unquote_plus(name)
        if not _NAME.match(key):
            parts.append(BLANK)  # the name itself may be the place
        elif not eq:
            parts.append(part if key in SAFE else BLANK)
        elif key in SAFE and SAFE[key](unquote_plus(value)):
            parts.append(part)
        else:
            parts.append(f"{name}={BLANK}")
    return f"{base}?{'&'.join(parts)}"


class RedactPlaces(logging.Filter):
    """Rewrites a record's arguments before formatting. uvicorn passes the
    path as an argument (`'%s - "%s %s HTTP/%s" %d'`, `'%s - "WebSocket %s"
    ...'`), never pre-formatted, so the message itself carries no place."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(_redact_arg(arg) for arg in record.args)
        return True


def _redact_arg(arg: object) -> object:
    if isinstance(arg, str) and arg.startswith("/"):
        return redact_query(arg)
    # httpx passes the request URL as an `httpx.URL`, not a string
    text = str(arg) if not isinstance(arg, (int, float)) else ""
    if HOST_ONLY and text.startswith(("http://", "https://")):
        parts = urlsplit(text)
        if (parts.hostname or "").lower() in HOST_ONLY:
            return f"{parts.scheme}://{parts.hostname}"
    for host in PRIVATE_HOSTS:
        if text.startswith(host):
            return host[:-1] + redact_query(text[len(host) - 1 :])
    return arg


def install() -> None:
    """Idempotent: one filter per logger, however many times it is called."""
    for name in (*LOGGERS, "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactPlaces) for f in logger.filters):
            logger.addFilter(RedactPlaces())
