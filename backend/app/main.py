"""SOSForge -- API + real-time hub."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import geocode, place
from app.changelog import NEW, UNKNOWN, ChangeLog, Touched
from app.climate import monitor as climate
from app.core import privacy
from app.core.config import settings
from app.dedupe import Deduper
from app.geocode import search as geocode_search
from app.guidance import Lang, catalogue
from app.hub import Client, ConnectionQuota, hub
from app.models.event import Event, Kind, utcnow
from app.nearby import deep_links, windy_webcams
from app.pipeline import Pipeline
from app.scope import (
    SCOPE_PARAMS,
    Scope,
    ScopeError,
    compose,
    parse_point_scope,
    parse_scope,
    split,
)
from app.sources.aftershock import AftershockSource
from app.sources.alerts_world import MeteoalarmSource, WmoCapSource
from app.sources.base import Source
from app.sources.eew import CencSource, JmaEewSource
from app.sources.emsc_ws import EmscWebsocketSource
from app.sources.fdsn import NoaSource, NrcanSource
from app.sources.felt import FeltReportCache
from app.sources.gdacs import GdacsSource
from app.sources.geonet_volcano import GeonetVolcanoSource
from app.sources.hazards import AshSource, EonetSource, NhcSource
from app.sources.igp import IgpSource
from app.sources.jma_tsunami import JmaTsunamiSource
from app.sources.nws import NwsSource
from app.sources.phivolcs import PhivolcsSource
from app.sources.regional import (
    AfadSource,
    BmkgSource,
    GeofonSource,
    GeonetSource,
    IngvSource,
    JmaSource,
)
from app.sources.registry import extra_sources
from app.sources.space import SpaceWeatherSource
from app.sources.ssn import SsnSource
from app.sources.tsunami import TsunamiSource
from app.sources.usgs import UsgsSource, backfill
from app.sources.volcano import VolcanoSource
from app.store.ring import EventStore
from app.swarm import as_event as swarm_event
from app.swarm import detect as detect_swarms

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("sosforge")
# people's places (zones, lat/lon, searched names) never reach the log
privacy.install()

store = EventStore(
    maxlen=settings.ring_size, data_dir=settings.data_dir, persist=settings.persist_jsonl
)
ws_quota = ConnectionQuota(per_ip=settings.max_ws_per_ip)
deduper = Deduper(
    window_seconds=settings.dedupe_window_seconds,
    radius_km=settings.dedupe_radius_km,
    mag_delta=settings.dedupe_mag_delta,
)
pipeline = Pipeline(store, deduper)
sources: list[Source] = []
STARTED_AT = utcnow()


def build_sources() -> list[Source]:
    built: list[Source] = []
    if settings.enable_emsc_ws:
        built.append(EmscWebsocketSource(settings.emsc_ws_url))
    if settings.enable_usgs:
        built.append(UsgsSource(settings.usgs_feed_url, settings.usgs_poll_seconds))
    if settings.enable_tsunami:
        built.append(TsunamiSource(settings.tsunami_poll_seconds))
    if settings.enable_gdacs:
        built.append(
            GdacsSource(
                poll_seconds=settings.gdacs_poll_seconds,
                max_age_days=settings.gdacs_max_age_days,
            )
        )
    if settings.enable_nws:
        # The zone cache lives next to the journal: 511 distinct zones in one
        # live snapshot, and boundaries that never move.
        built.append(
            NwsSource(settings.nws_poll_seconds, zone_cache=settings.data_dir / "nws-zones.json")
        )
    if settings.enable_volcano:
        built.append(VolcanoSource(settings.volcano_poll_seconds))
    if settings.enable_jma:
        built.append(JmaSource(settings.jma_poll_seconds))
    if settings.enable_bmkg:
        built.append(BmkgSource(settings.bmkg_poll_seconds))
    if settings.enable_geonet:
        built.append(GeonetSource(settings.geonet_poll_seconds))
    if settings.enable_ingv:
        built.append(IngvSource(settings.ingv_poll_seconds))
    if settings.enable_afad:
        built.append(AfadSource(settings.afad_poll_seconds))
    if settings.enable_nhc:
        built.append(NhcSource(settings.nhc_poll_seconds))
    if settings.enable_ash:
        built.append(AshSource(settings.ash_poll_seconds))
    if settings.enable_geofon:
        built.append(GeofonSource(settings.geofon_poll_seconds))
    if settings.enable_eonet:
        built.append(EonetSource(settings.eonet_poll_seconds))
    if settings.enable_meteoalarm:
        built.append(
            MeteoalarmSource(
                settings.meteoalarm_poll_seconds, min_level=settings.meteoalarm_min_level
            )
        )
    if settings.enable_jma_eew:
        built.append(JmaEewSource(settings.jma_eew_poll_seconds))
    if settings.enable_cenc:
        built.append(CencSource(settings.cenc_poll_seconds))
    if settings.enable_aftershock:
        built.append(AftershockSource(settings.aftershock_poll_seconds))
    if settings.enable_space:
        built.append(SpaceWeatherSource(settings.space_poll_seconds))
    if settings.enable_wmo:
        built.append(
            WmoCapSource(
                settings.wmo_poll_seconds,
                settings.wmo_max_severity_rank,
                # Persisted next to the journal. Unlike an NWS zone, there is a
                # new CAP document per alert issued, so the cache never
                # converges -- but losing it at every restart means starting
                # from zero positions every time, and the alerts that were
                # already placed go back to being unplaceable.
                cap_cache=settings.data_dir / "wmo-cap.json",
            )
        )
    # Japan's own tsunami advisories. Until now the only tsunami coverage was
    # two US centres (NTWC/PTWC): a JMA 津波注意報 never appeared at all, on a
    # product that ships a Japanese interface for the most tsunami-exposed
    # population it targets.
    if settings.enable_jma_tsunami:
        built.append(JmaTsunamiSource(settings.jma_tsunami_poll_seconds))
    # Mexico had NO national coverage. Guerrero and Oaxaca quakes felt across
    # Mexico City fall below the global catalogues' threshold: measured over
    # three days on the whole Mexican box, USGS returned zero events and EMSC
    # 52 at M>=3, while SSN goes down to M1.4. Twenty million people in a basin
    # with extreme site amplification were getting worse coverage than New
    # Zealand's five million.
    if settings.enable_ssn:
        built.append(SsnSource(settings.ssn_poll_seconds))
    # The volcano filter silently meant "US volcanoes". A filter showing zero
    # reads as calm, not as no-coverage. Whakaari killed tourists in 2019 at
    # alert level 2, and that level is published for free.
    # Canada and Greece, through their FDSN event services. Measured against
    # USGS-week plus EMSC: 87% of NRCan's events and 96% of NOA's exist
    # NOWHERE else in this feed. A previous survey wrote both off as dead --
    # NRCan rejects `format=json` with a 422, NOA refuses any query without a
    # `starttime` with a bare Apache 400. Both answer `format=text` fine.
    if settings.enable_nrcan:
        built.append(NrcanSource(settings.nrcan_poll_seconds))
    if settings.enable_noa:
        built.append(NoaSource(settings.noa_poll_seconds))
    # The Philippines: 115 million people on the most seismically active
    # archipelago on Earth, and until now not one local tremor reached this
    # feed. Measured: 479 events over seven days, 84% of them in neither USGS
    # nor EMSC. Two previous audits wrote PHIVOLCS off as "HTML only"; every
    # table row links to its own bulletin, and that filename is a better id
    # and a better timestamp than the visible columns -- UTC where the table
    # shows Philippine time, seconds where it shows minutes.
    if settings.enable_phivolcs:
        built.append(PhivolcsSource(settings.phivolcs_poll_seconds))
    # Peru: a subduction margin where depth decides everything. 88% of IGP's
    # events exist nowhere else here.
    if settings.enable_igp:
        built.append(IgpSource(settings.igp_poll_seconds))
    if settings.enable_geonet_volcano:
        built.append(
            GeonetVolcanoSource(
                settings.geonet_volcano_poll_seconds,
                min_level=settings.geonet_volcano_min_level,
            )
        )
    # Lane C's four sources (IMO quakes and volcanoes, JMA volcanic warnings,
    # NCS India) carry their own SOS_ settings in the registry.
    built.extend(extra_sources())
    return built


def source_states() -> list[dict]:
    """Every source's health, the shape the tick and the snapshot carry."""
    return [{**s.health.snapshot(), "ingested": store.counters.get(s.name, 0)} for s in sources]


def tick_message() -> dict:
    """The freshness proof: server clock, store stats, every source's health."""
    return {
        "type": "tick",
        "server_time": utcnow().isoformat(),
        "stats": store.stats(),
        "sources": source_states(),
        "clients": hub.client_count,
    }


def lite_tick(full: dict) -> dict:
    """The tick of a `/ws?tick=lite` client: what the freshness rule needs
    and the counters on screen, every second. Without the health of every
    source (`sources` messages carry it when it changes) and without
    `stats.by_source` (the same numbers are each source's `ingested`).
    The full tick is several KB a second to every socket, and OkHttp's
    websocket negotiates no compression, so the app's background watch paid
    it raw: about 4.7 MB an hour off Wi-Fi (lane A, emulator)."""
    stats = {k: v for k, v in full["stats"].items() if k != "by_source"}
    return {
        "type": "tick",
        "server_time": full["server_time"],
        "stats": stats,
        "clients": full["clients"],
    }


# A lite client's source list is refreshed at least this often, so last_ok,
# last_event_at and the counters it shows are never older than that.
SOURCES_REFRESH_SECONDS = 300.0


def source_state_key(states: list[dict]) -> tuple:
    """What, in the sources' health, is news for a lite client: up or down,
    degraded, the error, which feeds fail. Not `last_ok` or the counters,
    which move on every poll and would make the message as frequent as the
    tick it replaces."""
    return tuple(
        (
            s["name"],
            s["connected"],
            s.get("degraded"),
            s.get("last_error"),
            tuple(f.get("feed") for f in s.get("failed_feeds") or ()),
        )
        for s in states
    )


class SourcesWatch:
    """Decides when the lite clients get a `sources` message."""

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.sent_at = float("-inf")

    def due(self, states: list[dict], now: float) -> bool:
        key = source_state_key(states)
        if key != self.key or now - self.sent_at >= SOURCES_REFRESH_SECONDS:
            self.key, self.sent_at = key, now
            return True
        return False


def next_tick(deadline: float, now: float, period: float) -> float:
    """When the next tick is due, on a fixed grid.

    The heartbeat used to sleep a full period AFTER building and fanning out
    each tick, so every tick came late by what the previous one cost:
    measured 1.0019 s with one socket and 1.0256 s with a thousand, 92 ticks
    short an hour at that load. On a grid the period is the period whatever
    the fan-out costs. A loop stalled past a whole period resumes from now:
    a burst of catch-up ticks would say nothing a single one does not.
    """
    deadline += period
    return deadline if deadline > now else now


async def heartbeat() -> None:
    """One tick per second: proves to the client that the feed is alive and
    resyncs the clocks (the UI shows an "N s ago" age computed on server time)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    watch = SourcesWatch()
    while True:
        deadline = next_tick(deadline, loop.time(), settings.heartbeat_seconds)
        await asyncio.sleep(deadline - loop.time())
        # A single uncaught exception here killed the task for good: no more
        # ticks, so every client declared itself disconnected after 15 s and
        # looped on reconnection -- while /healthz answered "ok". That is the
        # freshness promise failing silently.
        try:
            if not hub.client_count:
                continue
            tick = tick_message()
            if watch.due(tick["sources"], loop.time()):
                # before the tick of the same second, to lite clients only
                await hub.broadcast(
                    None,
                    lite={
                        "type": "sources",
                        "server_time": tick["server_time"],
                        "sources": tick["sources"],
                    },
                )
            await hub.broadcast(tick, lite=lite_tick(tick))
        except Exception as exc:
            log.warning("heartbeat: %s", exc)


async def sweep_stale() -> None:
    """Sweeps out the alerts no source mentions anymore."""
    while True:
        await asyncio.sleep(settings.sweep_seconds)
        # same reason as the heartbeat: this task must survive its errors
        try:
            # through the pipeline: it removes AND tells the open tabs
            removed = await pipeline.sweep(settings.stale_after_hours)
            if removed:
                log.info(
                    "purge: %d alerts with no news for %.0f h (%s)",
                    len(removed),
                    settings.stale_after_hours,
                    ", ".join(sorted({e.source for e in removed})),
                )
            # Swarm detection runs on the store, not on a source: it is a
            # pattern across events, so it can only be seen once they are all
            # in one place.
            if settings.enable_swarm_detection:
                for swarm in detect_swarms(
                    store.recent(limit=settings.ring_size, primary_only=True),
                    radius_km=settings.swarm_radius_km,
                    window_hours=settings.swarm_window_hours,
                    min_count=settings.swarm_min_count,
                ):
                    await pipeline.emit(swarm_event(swarm))

            journals = store.purge_journals(settings.journal_keep_days)
            if journals:
                log.info("journals deleted: %s", ", ".join(j.name for j in journals))
        except Exception as exc:
            log.warning("sweep: %s", exc)


async def felt_reports() -> None:
    """How many people reported feeling it, arriving as a revision.

    Not a source: it adds nothing to the feed, it corrects something already
    in it. The count is what answers "did anyone else feel this", which the
    magnitude cannot -- and it only exists minutes AFTER the quake, growing
    from a dozen to a thousand. Measured: present on 40% of M>=5 events and
    4% overall, so this is a detail, not a headline.
    """
    cache = FeltReportCache()

    async def on_change(unid: str, count: int) -> None:
        event = store.get(f"emsc:{unid}")
        if event is None or event.felt_reports == count:
            return
        # Back through the pipeline so it becomes an ordinary revision: the
        # fingerprint covers `felt_reports`, so open tabs learn about it.
        updated = event.model_copy(deep=True)
        updated.felt_reports = count
        await pipeline.emit(updated)

    try:
        await cache.run(on_change)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- a detail must never kill the feed
        log.warning("felt reports unavailable: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global sources
    privacy.install()  # again, in case the server reconfigured logging after import
    sources = build_sources()

    # 1. fill the map before opening the floodgates: first the local journal
    # (it contains the EMSC pushes and the bulletins, which the USGS backfill
    # ignores), then the network catch-up.
    pipeline.quiet = True
    try:
        # The previous day TOO: a restart at 00:15 only replayed a quarter
        # hour of journal, and everything that is not a quake (cyclones,
        # tsunami bulletins, volcanoes, fires) disappeared -- while the UI
        # offers 24 h and "all".
        today = utcnow()
        restored = sum(
            [
                await pipeline.replay(settings.data_dir / f"events-{day:%Y-%m-%d}.jsonl")
                for day in (today - timedelta(days=1), today)
            ]
        )
        if restored:
            log.info("local journal: %d events restored", restored)
    except Exception as exc:
        log.warning("local journal unreadable (%s)", exc)
    try:
        count = await backfill(settings.backfill_url, pipeline.emit)
        log.info("backfill: %d events loaded", count)
    except Exception as exc:
        log.warning("backfill failed (%s), cold start", exc)
    finally:
        pipeline.quiet = False
    # The journal and the backfill went in without a message. The change log
    # must still know what they look like, or the first revision of each one
    # could not tell whether a polling client held it.
    hub.changelog.seed(store.recent(limit=settings.ring_size))

    # 2. start the ingesters + the heartbeat
    # a source must be able to WITHDRAW an event, not only publish one: a
    # cancelled early warning is the case that matters
    for source in sources:
        source.retract = pipeline.retract
    tasks = [asyncio.create_task(s.supervise(pipeline.emit), name=f"src:{s.name}") for s in sources]
    tasks.append(asyncio.create_task(heartbeat(), name="heartbeat"))
    tasks.append(asyncio.create_task(sweep_stale(), name="sweep"))
    # climate indicators: each polled at its dataset's own cadence (app/climate.py)
    tasks.append(asyncio.create_task(climate.run(), name="climate"))
    if settings.enable_felt_reports:
        tasks.append(asyncio.create_task(felt_reports(), name="felt"))
    log.info("SOSForge online -- %d sources: %s", len(sources), ", ".join(s.name for s in sources))

    yield

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("SOSForge stopped")


app = FastAPI(
    title="SOSForge",
    version="1.0.0",
    description="Real-time tracking of earthquakes, tsunamis and natural disasters",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    # No wildcard fallback: an empty CORS_ORIGINS means "no cross-origin
    # access", not "everyone". The wildcard was a footgun waiting for the day
    # someone adds authentication.
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- REST


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "uptime_seconds": round((utcnow() - STARTED_AT).total_seconds(), 1),
        "clients": hub.client_count,
        "ingested": pipeline.ingested,
        "buffered": store.stats()["total_buffered"],
    }


@app.get("/readyz")
async def readyz(response: Response) -> dict:
    """Readiness, as opposed to liveness.

    `/healthz` answers "ok" as long as the process is alive, which says nothing
    about whether it is doing its job. An orchestrator that restarts on liveness
    alone will happily keep a container that has not ingested anything in an
    hour. This one answers on the sources.
    """
    up = [s for s in sources if s.health.connected]
    # A tracker with a third of its sources down is degraded, not ready. The
    # threshold is deliberate: one flaky feed must not take the service out.
    ready = bool(sources) and len(up) >= max(1, int(len(sources) * 0.6))
    if not ready:
        response.status_code = 503
    return {
        "ready": ready,
        "sources_up": len(up),
        "sources_total": len(sources),
        "down": sorted(s.name for s in sources if not s.health.connected),
        "buffered": store.stats()["total_buffered"],
    }


# ------------------------------------------------------------ API v2 contract
#
# Everything below is ADDITIVE. A request without any of the new parameters
# gets the body it always got. The native app relies on three things here:
# a zone scope (the same object the websocket uses, so polling and listening
# agree), a cursor (a 15-minute poll on mobile data must not re-read 300
# events to learn that two changed), and an ETag (so that poll costs a 304
# when nothing changed).

API_VERSION = 2
FEATURES = [
    "ws_zones",  # /ws?zones=&min_severity=&kinds=&unplaced=
    "ws_out_of_scope",  # a held event leaving the scope arrives as purge reason=out_of_scope
    "ws_error",  # bad /ws params: one `error` message, then close 1008
    "snapshot_pinned",  # with chunks=1 (and v2 REST full reads): latest N + every pinned event
    "events_zone",  # /api/events?lat=&lon=&radius_km= (and the ws scope params)
    "events_cursor",  # /api/events?since=<cursor> -> events, removed, cursor, reset
    "events_etag",  # ETag / If-None-Match -> 304 on every v2 read
    "guidance",  # /api/guidance?lang=en|fr|es|ja|id
    "escalated",  # `update` messages may carry escalated: true, forwarded untouched
    "snapshot_chunked",  # /ws?chunks=1: snapshot + `more`, then snapshot_more chunks
    "tick_lite",  # /ws?tick=lite: tick without sources; `sources` messages when they change
    "climate",  # /api/climate: live indicators, each with its dataset's own as_of
]
# `place` (/api/place: the place report) is added by `api_version` when
# SOS_ENABLE_PLACE is on, so the flag never claims a route that answers 404.


def _refuse(exc: ScopeError) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": exc.code, "reason": exc.reason})


def _passes(
    event: Event,
    kind: str | None,
    min_magnitude: float | None,
    after: datetime | None,
    primary_only: bool,
) -> bool:
    """The `/api/events` base filters, asked of ONE event. Mirrors
    `EventStore.recent`, which applies them to the whole ring: a cursor
    delta looks events up by id and must apply the same rules."""
    if kind and event.kind.value != kind:
        return False
    if min_magnitude is not None and (event.magnitude or 0) < min_magnitude:
        return False
    if after and event.time < after:
        return False
    return not (primary_only and event.cluster_id and event.cluster_id != event.id)


def _was_in(
    touched: Touched,
    scope: Scope | None,
    kind: str | None,
    min_magnitude: float | None,
    primary_only: bool,
) -> bool:
    """Could the client have held this event at its cursor?

    Errs toward yes: a removal the client did not need costs it nothing, a
    removal it needed and did not get is a cancelled alert left on its
    screen. The `hours` window is not applied -- an event ageing out of it is
    not a change."""
    if touched.before == NEW:
        return False
    prev = touched.prev
    if touched.before == UNKNOWN or prev is None:
        return True
    if kind and prev.kind != kind:
        return False
    if min_magnitude is not None and (prev.magnitude or 0) < min_magnitude:
        return False
    if primary_only and not prev.primary:
        return False
    return scope is None or scope.matches_fields(
        prev.lat, prev.lon, prev.severity, prev.kind, prev.country_code, prev.bbox
    )


def _etag(events: list[Event], removed: list[str], reset: bool) -> str:
    """Identity of the RESULT, not of the response: `server_time` changes on
    every call, the cursor on every change anywhere on the planet, and
    `last_seen` on every poll of an unchanged alert, so none belongs here.
    What does: each event's id and revision (bumped by the store on every
    real change), the removals, and whether this is a full read."""
    digest = hashlib.sha1(b"full" if reset else b"delta")
    for event in events:
        digest.update(f"\n{event.id}|{event.revision}|{event.updated_at}".encode())
    digest.update(("\n-" + ",".join(sorted(removed))).encode())
    return f'"{digest.hexdigest()[:20]}"'


def _matches_if_none_match(header: str | None, etag: str) -> bool:
    if not header:
        return False
    for candidate in header.split(","):
        tag = candidate.strip()
        # a proxy that compresses the body is allowed to weaken the tag
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag == "*" or tag == etag:
            return True
    return False


@app.get("/api/events", response_model=None)
async def api_events(
    request: Request,
    limit: int = Query(300, ge=1, le=2000),
    # A typo in `kind` used to return an empty list, which on this product reads
    # as "nothing is happening" -- the worst possible answer to a bad question.
    kind: Kind | None = None,
    min_magnitude: float | None = Query(None, ge=0, le=12),
    hours: float | None = Query(None, ge=0.01, le=720),
    primary_only: bool = True,
    # --- v2: one zone as three numbers (all three or none); ranges are
    # checked by the scope parser so REST and websocket refuse identically
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float | None = None,
    # ... or the exact websocket scope parameters
    zones: str | None = Query(None, max_length=400),
    min_severity: str | None = Query(None, max_length=16),
    kinds: str | None = Query(None, max_length=200),
    unplaced: str | None = Query(None, max_length=40),
    # --- v2: incremental read. Empty = "everything, and give me a cursor".
    since: str | None = Query(None, max_length=40),
) -> dict | Response:
    try:
        point = parse_point_scope(lat, lon, radius_km)
        if point:
            zones = f"{point};{zones}" if zones else point
        scope = parse_scope(
            {"zones": zones, "min_severity": min_severity, "kinds": kinds, "unplaced": unplaced}
        )
    except ScopeError as exc:
        raise _refuse(exc) from None

    after = utcnow() - timedelta(hours=hours) if hours else None
    kind_value = kind.value if kind else None

    if scope is None and since is None:
        # the legacy request, answered exactly as before -- no ETag either: a
        # browser revalidating it would hand the page a cached `server_time`,
        # and the clock skew every age on screen is computed from
        legacy = store.recent(
            limit=limit,
            kind=kind_value,
            min_magnitude=min_magnitude,
            since=after,
            primary_only=primary_only,
        )
        # A JSONResponse built here, not a dict: FastAPI would walk every
        # field of the 300 events through `jsonable_encoder` first -- 6.9 ms
        # of a 14 ms request, measured on the live journal -- to give back
        # the very same values, since `public()` is already JSON-native. The
        # bytes are identical (tests/test_lane_p_core.py).
        return JSONResponse(
            {
                "count": len(legacy),
                "server_time": utcnow().isoformat(),
                "events": [e.public() for e in legacy],
            }
        )

    if since and not ChangeLog.well_formed(since):
        raise _refuse(ScopeError("bad_since", "since is a cursor returned by this endpoint"))

    # The cursor is minted BEFORE the store is read. A change landing between
    # the two is then both in this body and in the next delta -- a harmless
    # repeat. The other order would lose it.
    next_cursor = hub.changelog.cursor()
    reset_reason: str | None = "no_cursor"
    events: list[Event] = []
    removed: list[str] = []
    placed = None

    if since:
        delta = hub.changelog.since(since)
        reset_reason = delta.reset
        if reset_reason is None:
            for touched in delta.touched:
                current = None if touched.removed else store.get(touched.event_id)
                if (
                    current is not None
                    and _passes(current, kind_value, min_magnitude, after, primary_only)
                    and (scope is None or scope.matches(current))
                ):
                    events.append(current)
                elif _was_in(touched, scope, kind_value, min_magnitude, primary_only):
                    # gone, filtered out, or out of the scope: for the client
                    # these are all the same thing -- drop what you hold
                    removed.append(touched.event_id)
            if len(events) > limit:
                # Cut, a delta would silently lose changes. A full read is
                # what the client would have to do anyway.
                reset_reason, events, removed = "too_many_changes", [], []
            elif not events and not removed:
                # Nothing for THIS client. Handing its own cursor back keeps
                # the next poll's URL identical, so any HTTP cache revalidates
                # it and gets a 304 instead of an empty 200.
                next_cursor = since
            events.sort(key=lambda e: e.time, reverse=True)

    if reset_reason is not None:
        candidates = store.recent(
            limit=settings.ring_size,
            kind=kind_value,
            min_magnitude=min_magnitude,
            since=after,
            primary_only=primary_only,
        )
        # same composition as the websocket snapshot, so the poll fallback
        # and the socket cannot disagree about what is current
        events, placed = compose(candidates, limit, scope)

    etag = _etag(events, removed, reset_reason is not None)
    # `no-cache` = store it, but always ask before reusing it: a cache must
    # never serve an emergency feed without checking it is still current
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _matches_if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    body: dict = {
        "count": len(events),
        "server_time": utcnow().isoformat(),
        "events": [e.public() for e in events],
        "cursor": next_cursor,
        "reset": reset_reason is not None,
        "reset_reason": reset_reason,
        "removed": removed,
        "scope": scope.public() if scope is not None else None,
        # what a zoned full read could not place (see app/scope.py); null on
        # a delta, where nothing was counted
        "unplaced": placed.public() if placed is not None and scope and scope.zoned else None,
    }
    return JSONResponse(body, headers=headers)


@app.get("/api/climate")
async def api_climate() -> dict:
    """Live climate indicators, each with the date its DATASET gives
    (`as_of`), its own poll state and last error. See app/climate.py."""
    return climate.snapshot()


@app.get("/api/version")
async def api_version() -> dict:
    features = [*FEATURES, "place"] if settings.enable_place else FEATURES
    return {"api": API_VERSION, "features": features, "app": app.version}


# Its own budget, not the geocoder's: a place report reaches about fifteen
# upstreams, a search one. Keyed by the client address the Dockerfile's
# --proxy-headers / FORWARDED_ALLOW_IPS make real.
place_budget = geocode.AddressBudget(
    burst=settings.place_rate_burst, per_second=settings.place_rate_per_second
)
place.configure(
    data_dir=settings.data_dir,
    max_upstream=settings.place_max_upstream,
    cache_entries=settings.place_cache_entries,
    history_cache_mb=settings.place_history_cache_mb,
)


def place_alerts() -> tuple[list[Event], list[dict]]:
    """What the place report's `alerts_now` block reads: the store's primary
    events and the sources' health. Passed in, so app/place never imports
    this module."""
    return store.recent(limit=settings.ring_size, primary_only=True), source_states()


@app.get("/api/place", response_model=None)
async def api_place(request: Request) -> Response:
    """The place report (app/place/): for ONE point the reader searched or
    clicked, 22 blocks, each from one dataset with its own `as_of`. Read
    strictly from `request.query_params` (FastAPI ignores extras). A report
    that starts a new upstream fetch spends the caller's token; a cached
    one, or one that only joins a fetch in flight, costs nothing."""
    if not settings.enable_place:
        raise HTTPException(
            status_code=404,
            detail={"code": "place_disabled", "reason": "the place report is switched off here"},
        )
    try:
        query = place.parse_query(request.query_params)
    except place.QueryError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reason": exc.reason}
        ) from None
    address = request.client.host if request.client else None
    result = await place.report(
        query,
        place_alerts,
        budget_s=settings.place_budget_seconds,
        take_token=lambda: place_budget.take(address),
    )
    if result.refused or result.body is None or result.etag is None:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "reason": "too many new place reports from this address, try again shortly",
            },
            headers={"Retry-After": str(max(1, math.ceil(result.retry_after)))},
        )
    # `no-cache` + ETag: a 304 means nothing in the report changed. The web
    # fetches with `cache: 'no-store'`, or a revalidated cached body would
    # hand it the OLD server_time every age on screen is computed from.
    headers = {"ETag": result.etag, "Cache-Control": "no-cache"}
    if _matches_if_none_match(request.headers.get("if-none-match"), result.etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(result.body, headers=headers)


@app.get("/api/guidance")
async def api_guidance(lang: Lang = Lang.EN) -> dict:
    """What to do before, during and after each kind of hazard, in the
    requested language, every line carrying the official page it
    paraphrases. Loaded once from `app/data/guidance.json`."""
    try:
        return catalogue().for_language(lang)
    except (OSError, ValueError) as exc:
        log.error("guidance unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="guidance catalogue unavailable") from None


# MIND the order: the `:path` converter swallows slashes, so the generic
# route `/api/events/{event_id:path}` would also match ".../nearby" if it were
# declared first. The most specific one goes first.
@app.get("/api/events/{event_id:path}/nearby")
async def api_nearby(event_id: str) -> dict:
    """Live views around an event: deep links always available, plus the
    public webcams if a Windy key is configured."""
    event = store.get(event_id)
    if event is None or event.lat is None or event.lon is None:
        return {"found": False, "links": [], "cameras": []}

    return {
        "found": True,
        "lat": event.lat,
        "lon": event.lon,
        "links": deep_links(event.lat, event.lon, event.place, event.time.isoformat()),
        "cameras": await windy_webcams(event.lat, event.lon, settings.nearby_radius_km),
        "cameras_configured": bool(settings.windy_api_key),
    }


@app.get("/api/events/{event_id:path}")
async def api_event(event_id: str) -> dict:
    event = store.get(event_id)
    if event is None:
        # A 200 with "found: false" makes every machine client treat a missing
        # event as a successful read.
        raise HTTPException(status_code=404, detail=f"unknown event: {event_id}")
    # NOT `event.raw`. That is the verbatim object the source sent: the one
    # place where feed-controlled data would reach a client without passing
    # through normalisation, and unbounded besides (a GDACS event carries
    # every episode, a JMA list every entry).
    return {"found": True, "event": event.public()}


@app.get("/api/geocode")
async def api_geocode(request: Request, q: str = Query(min_length=2, max_length=120)) -> dict:
    """Area search by name. Proxied to hold the rate Nominatim imposes
    (1 req/s), which browser calls would violate. A search that would reach
    Nominatim also spends the caller's own budget (`geocode.AddressBudget`),
    so one address cannot make every other visitor wait behind its burst; a
    cached answer costs nothing and is never refused."""
    if not geocode.cached(q):
        wait = geocode.budget.take(request.client.host if request.client else None)
        if wait:
            raise HTTPException(
                status_code=429,
                detail="too many searches from this address, try again shortly",
                headers={"Retry-After": str(max(1, math.ceil(wait)))},
            )
    return {"query": q, "results": await geocode_search(q)}


@app.get("/api/stats")
async def api_stats() -> dict:
    return store.stats()


@app.get("/api/sources")
async def api_sources() -> dict:
    # `events_seen` counts what the source READ each cycle (the JMA list
    # returns 763 every poll), `ingested` what actually entered the store.
    # Showing only the former gave misleading observability.
    ingested = store.counters
    return {
        "server_time": utcnow().isoformat(),
        "sources": [
            {**s.health.snapshot(), "mode": s.kind, "ingested": ingested.get(s.name, 0)}
            for s in sources
        ],
    }


# ----------------------------------------------------------------------- websocket


WS_QUERY_MAX = 2000
WS_PARAMS = (*SCOPE_PARAMS, "chunks", "tick")
# `chunks=1`: the snapshot a chunk-aware client opts into. Without it, the
# snapshot is the rule every client had before v2 (latest `snapshot_size`
# primaries, no pinning, no `more`): web builds from before chunking THROW on
# an unknown frame, and the installed macOS agents are old. With it, the
# composed snapshot (latest + every pinned event) goes out as `snapshot` +
# `snapshot_more` chunks, none of them long on a slow link.
# Chosen by measurement on live data (tasks/todo.md, lane B, chunking): with
# permessage-deflate the 32 KB JSON budget gave messages of at most 6.0 KiB
# on the wire, half the 12 KiB ceiling, so a day that compresses twice as
# badly still fits.
SNAPSHOT_CHUNK_EVENTS = 40
SNAPSHOT_CHUNK_BYTES = 32_000
# How long a shared snapshot may be reused while the store has not changed.
# The store's version is what keeps it exact; this only bounds how stale
# `last_seen` (refreshed in place on every re-mention, without a version
# bump) can be in a snapshot.
SNAPSHOT_CACHE_SECONDS = 1.0

Chunks = list[tuple[str, int]]  # (events serialized as a JSON array, how many)
# chunked -> (the store it was built from, its version then, when, chunks)
_shared_snapshots: dict[bool, tuple[EventStore, int, float, Chunks]] = {}


def serialize_chunks(publics: list[dict], chunked: bool) -> Chunks:
    parts = split(publics, SNAPSHOT_CHUNK_EVENTS, SNAPSHOT_CHUNK_BYTES) if chunked else [publics]
    return [(json.dumps(part, default=str), len(part)) for part in parts]


def shared_snapshot(chunked: bool) -> Chunks:
    """The snapshot of an unscoped socket (the web, the agent), serialized
    once per store version and shared by every socket that opens meanwhile.

    It was composed and serialized for every socket: 6 ms with a full ring,
    17 ms chunked, all of it on the event loop that owes every open tab a
    tick each second. A thousand people opening the site after a large
    quake -- the one moment this product exists for -- paid it a thousand
    times over for identical bytes. Exact, not approximate: the store bumps
    its version on every insert, revision, removal and promotion, so a
    cached snapshot is only served while it is still what composing would
    give.
    """
    now = time.monotonic()
    hit = _shared_snapshots.get(chunked)
    if (
        hit is not None
        and hit[0] is store
        and hit[1] == store.version
        and now - hit[2] < SNAPSHOT_CACHE_SECONDS
    ):
        return hit[3]
    events, _ = compose(
        store.recent(limit=settings.ring_size, primary_only=True),
        settings.snapshot_size,
        None,
        pin=chunked,
    )
    chunks = serialize_chunks([e.public() for e in events], chunked)
    _shared_snapshots[chunked] = (store, store.version, now, chunks)
    return chunks


def splice(head: dict, key: str, value_json: str, tail: dict) -> str:
    """`json.dumps({**head, key: value, **tail})` with `value` already
    serialized. Byte for byte the same text: `json.dumps` of a dict is its
    members' serializations joined with the default separators."""
    out = f"{json.dumps(head, default=str)[:-1]}, {json.dumps(key)}: {value_json}"
    return f"{out}, {json.dumps(tail, default=str)[1:]}" if tail else f"{out}}}"


def ws_params(ws: WebSocket) -> tuple[Scope | None, bool, bool]:
    """The socket's scope, whether it takes a chunked snapshot, and whether
    it takes the lite tick. Strict on
    purpose: an unknown parameter is a typo (`zone=` for `zones=`), and a
    client that misspelled its zone and silently got the whole planet would
    take every alert it receives for a local one."""
    params = ws.query_params
    if len(ws.url.query) > WS_QUERY_MAX:
        raise ScopeError("too_long", f"query string longer than {WS_QUERY_MAX} characters")
    unknown = sorted(set(params.keys()) - set(WS_PARAMS))
    if unknown:
        raise ScopeError(
            "unknown_param",
            f"unknown parameter(s): {', '.join(unknown)} (accepted: {', '.join(WS_PARAMS)})",
        )
    for key in params:
        if len(params.getlist(key)) > 1:
            raise ScopeError(f"bad_{key}", f"{key} given more than once")
    chunks = params.get("chunks")
    if chunks is not None and chunks != "1":
        raise ScopeError("bad_chunks", f"chunks only takes 1 -- got {chunks!r}")
    tick = params.get("tick")
    if tick is not None and tick != "lite":
        raise ScopeError("bad_tick", f"tick only takes lite -- got {tick!r}")
    scope = parse_scope({k: params.get(k) for k in SCOPE_PARAMS})
    return scope, chunks == "1", tick == "lite"


async def refuse_ws(ws: WebSocket, exc: ScopeError) -> None:
    """Accept, say why, close. Closing before the handshake would reach the
    client as a bare HTTP 403 with no reason, and a socket accepted and then
    left silent is exactly what the freshness rule forbids."""
    try:
        await ws.accept()
        await ws.send_text(
            json.dumps(
                {
                    "type": "error",
                    "code": exc.code,
                    "reason": exc.reason,
                    "server_time": utcnow().isoformat(),
                }
            )
        )
        await ws.close(code=1008, reason=exc.code)  # 1008 = policy violation
    except (WebSocketDisconnect, RuntimeError):
        pass


async def refuse_busy(ws: WebSocket) -> None:
    """Accept, then close 1013 ("try again later") with a reason. No `error`
    message: the quota is not the client's mistake, and a pre-v2 client
    would throw on a frame type it does not know."""
    try:
        await ws.accept()
        await ws.close(code=1013, reason="too_many_connections")
    except (WebSocketDisconnect, RuntimeError):
        pass


async def send_snapshot(ws: WebSocket, client: Client, scope: Scope | None, chunked: bool) -> None:
    """The opening snapshot, then its chunks, before the pump starts.

    Its own coroutine on purpose: everything it builds (the events' public
    dicts, the serialized chunks) dies when it returns. Built as locals of
    the endpoint, it lived as long as the connection -- the endpoint's frame
    stays alive until the socket closes -- and every open tab kept its 300
    public dicts, about 0.45 MB measured per socket, for hours.
    """
    # Composed (or taken from the shared cache) with no await in between,
    # and after the client is registered: every broadcast from here on
    # reaches this client, and for a scoped one it is narrowed against a
    # held set that already contains the snapshot, so a revision or a
    # withdrawal of a snapshot event cannot slip past it.
    tail: dict = {}
    if scope is None:
        chunks = shared_snapshot(chunked)
    else:
        snapshot, placed = compose(
            store.recent(limit=settings.ring_size, primary_only=True),
            settings.snapshot_size,
            scope,
            pin=chunked,
        )
        # every chunked id too: a chunk still in flight is already held
        for event in snapshot:
            client.hold(event.id)
        chunks = serialize_chunks([e.public() for e in snapshot], chunked)
    remaining = sum(count for _, count in chunks) - chunks[0][1]
    tail["stats"] = store.stats()
    tail["sources"] = [
        {**s.health.snapshot(), "ingested": store.counters.get(s.name, 0)} for s in sources
    ]
    if chunked:
        tail["more"] = remaining
    if scope is not None:
        tail["scope"] = scope.public()
        tail["unplaced"] = placed.public()
    await ws.send_text(
        splice(
            {"type": "snapshot", "server_time": utcnow().isoformat()},
            "events",
            chunks[0][0],
            tail,
        )
    )
    # The rest of the snapshot, before the pump starts: no queued delta can
    # overtake a chunk, so a revision always lands on top of the version it
    # revises.
    for chunk_json, count in chunks[1:]:
        remaining -= count
        await ws.send_text(
            splice(
                {"type": "snapshot_more"},
                "events",
                chunk_json,
                {"remaining": remaining, "server_time": utcnow().isoformat()},
            )
        )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    try:
        scope, chunked, lite = ws_params(ws)
    except ScopeError as refused:
        # The code only: the reason quotes the rejected text, and a rejected
        # zone is still somebody's home (it goes to the client, not the log).
        log.info("websocket refused: %s", refused.code)
        await refuse_ws(ws, refused)
        return

    # Decided before any snapshot work: the snapshot is the expensive half of
    # what we are declining to do. But the handshake IS accepted first. A
    # close before accept never reaches the wire as a close code: the ASGI
    # server answers it with an HTTP 403, which tells a client nothing about
    # backing off, so web and app retried as hard as on any other failure
    # while the contract promised them 1013 (lane N's drills, 2026-10-27).
    address = ws.client.host if ws.client else None
    if not ws_quota.acquire(address):
        log.warning("websocket refused: %s is already at the per-address cap", address)
        await refuse_busy(ws)
        return

    await ws.accept()
    client = Client(uuid.uuid4().hex[:8], scope, lite=lite)
    await hub.register(client)

    try:
        await send_snapshot(ws, client, scope, chunked)

        async def pump() -> None:
            getter = asyncio.ensure_future(client.queue.get())
            evicted = asyncio.ensure_future(client.evicted.wait())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        [getter, evicted], return_when=asyncio.FIRST_COMPLETED
                    )
                    if evicted in done:
                        # the hub evicted us (client too slow): close cleanly
                        # instead of leaving a silent connection
                        await ws.close(code=1013)
                        return
                    payload = getter.result()
                    getter = asyncio.ensure_future(client.queue.get())
                    await ws.send_text(payload)
            finally:
                getter.cancel()
                evicted.cancel()

        async def drain() -> None:
            # incoming messages are unused, but reading them is what detects
            # the browser's disconnection
            while True:
                await ws.receive_text()

        pumper = asyncio.create_task(pump())
        drainer = asyncio.create_task(drain())
        done, pending = await asyncio.wait([pumper, drainer], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("websocket %s ended: %s", client.id, exc)
    finally:
        await hub.unregister(client)
        ws_quota.release(address)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower()
    )


if __name__ == "__main__":
    main()
