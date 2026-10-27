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
from collections.abc import Iterable
from urllib.parse import unquote_plus, urlsplit

# The parameters whose values say nothing about where anyone is, each with
# the only shape its value may have to be kept. A value of another shape is
# blanked like any other: `kinds=earthquake;zones=48.85,2.35,25` (a ';'
# typed for '&') is a `kinds` that carries a home.
_NUMBER = re.compile(r"^\d{1,6}(\.\d{1,3})?$")
_WORDS = re.compile(r"^[A-Za-z_,]{0,200}$")
SAFE: dict[str, re.Pattern[str]] = {
    "chunks": re.compile(r"^\d{1,2}$"),
    "tick": re.compile(r"^[a-z]{1,16}$"),
    "hours": _NUMBER,
    "limit": _NUMBER,
    "min_magnitude": _NUMBER,
    "kind": _WORDS,
    "kinds": _WORDS,
    "lang": _WORDS,
    "min_severity": _WORDS,
    "primary_only": _WORDS,
    "since": re.compile(r"^[0-9a-f]{1,16}:\d{1,12}$"),
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
    parameter's value of the expected shape. A part with no '=' is kept
    only if it is a bare SAFE name (or empty): `zones48.85,2.35,25`, a bare
    `48.85,2.35,25` and an encoded `zones%3D48.85,...` are blanked whole."""
    base, sep, query = path.partition("?")
    if not sep:
        return path
    parts = []
    for part in query.split("&"):
        name, eq, value = part.partition("=")
        key = unquote_plus(name)
        if not eq:
            parts.append(part if not part or key in SAFE else BLANK)
        elif key in SAFE and SAFE[key].match(unquote_plus(value)):
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
