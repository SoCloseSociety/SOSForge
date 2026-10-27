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
from urllib.parse import unquote_plus

# The parameters whose values say nothing about where anyone is.
SAFE = frozenset(
    {
        "chunks",
        "hours",
        "kind",
        "kinds",
        "lang",
        "limit",
        "min_magnitude",
        "min_severity",
        "primary_only",
        "since",
    }
)
BLANK = "-"
# The loggers uvicorn writes request paths to.
LOGGERS = ("uvicorn.access", "uvicorn.error")


def redact_query(path: str) -> str:
    """The path with the value of every parameter not in SAFE replaced by
    `-`. Everything else is kept byte for byte, malformed parts included."""
    base, sep, query = path.partition("?")
    if not sep:
        return path
    parts = []
    for part in query.split("&"):
        name, eq, _ = part.partition("=")
        if eq and unquote_plus(name) not in SAFE:
            parts.append(f"{name}={BLANK}")
        else:
            parts.append(part)
    return f"{base}?{'&'.join(parts)}"


class RedactPlaces(logging.Filter):
    """Rewrites a record's arguments before formatting. uvicorn passes the
    path as an argument (`'%s - "%s %s HTTP/%s" %d'`, `'%s - "WebSocket %s"
    ...'`), never pre-formatted, so the message itself carries no place."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(
                redact_query(arg) if isinstance(arg, str) and arg.startswith("/") else arg
                for arg in record.args
            )
        return True


def install() -> None:
    """Idempotent: one filter per logger, however many times it is called."""
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactPlaces) for f in logger.filters):
            logger.addFilter(RedactPlaces())
