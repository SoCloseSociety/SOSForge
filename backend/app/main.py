"""SOSForge -- API + real-time hub."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.dedupe import Deduper
from app.geocode import search as geocode_search
from app.hub import Client, ConnectionQuota, hub
from app.models.event import Kind, utcnow
from app.nearby import deep_links, windy_webcams
from app.pipeline import Pipeline
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
    return built


async def heartbeat() -> None:
    """One tick per second: proves to the client that the feed is alive and
    resyncs the clocks (the UI shows an "N s ago" age computed on server time)."""
    while True:
        await asyncio.sleep(settings.heartbeat_seconds)
        # A single uncaught exception here killed the task for good: no more
        # ticks, so every client declared itself disconnected after 15 s and
        # looped on reconnection -- while /healthz answered "ok". That is the
        # freshness promise failing silently.
        try:
            if not hub.client_count:
                continue
            await hub.broadcast(
                {
                    "type": "tick",
                    "server_time": utcnow().isoformat(),
                    "stats": store.stats(),
                    "sources": [
                        {**s.health.snapshot(), "ingested": store.counters.get(s.name, 0)}
                        for s in sources
                    ],
                    "clients": hub.client_count,
                }
            )
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

    # 2. start the ingesters + the heartbeat
    # a source must be able to WITHDRAW an event, not only publish one: a
    # cancelled early warning is the case that matters
    for source in sources:
        source.retract = pipeline.retract
    tasks = [asyncio.create_task(s.supervise(pipeline.emit), name=f"src:{s.name}") for s in sources]
    tasks.append(asyncio.create_task(heartbeat(), name="heartbeat"))
    tasks.append(asyncio.create_task(sweep_stale(), name="sweep"))
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


@app.get("/api/events")
async def api_events(
    limit: int = Query(300, ge=1, le=2000),
    # A typo in `kind` used to return an empty list, which on this product reads
    # as "nothing is happening" -- the worst possible answer to a bad question.
    kind: Kind | None = None,
    min_magnitude: float | None = Query(None, ge=0, le=12),
    hours: float | None = Query(None, ge=0.01, le=720),
    primary_only: bool = True,
) -> dict:
    since = utcnow() - timedelta(hours=hours) if hours else None
    events = store.recent(
        limit=limit,
        kind=kind.value if kind else None,
        min_magnitude=min_magnitude,
        since=since,
        primary_only=primary_only,
    )
    return {
        "count": len(events),
        "server_time": utcnow().isoformat(),
        "events": [e.public() for e in events],
    }


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
async def api_geocode(q: str = Query(min_length=2, max_length=120)) -> dict:
    """Area search by name. Proxied to hold the rate Nominatim imposes
    (1 req/s), which browser calls would violate."""
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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    # Refuse BEFORE accepting: an accepted connection has already cost a
    # snapshot, and refusing after would be the expensive half of the work we
    # are declining to do.
    address = ws.client.host if ws.client else None
    if not ws_quota.acquire(address):
        log.warning("websocket refused: %s is already at the per-address cap", address)
        await ws.close(code=1013)  # 1013 = try again later
        return

    await ws.accept()
    client = Client(uuid.uuid4().hex[:8])
    await hub.register(client)

    try:
        snapshot = store.recent(limit=settings.snapshot_size, primary_only=True)
        await ws.send_text(
            json.dumps(
                {
                    "type": "snapshot",
                    "server_time": utcnow().isoformat(),
                    "events": [e.public() for e in snapshot],
                    "stats": store.stats(),
                    "sources": [
                        {**s.health.snapshot(), "ingested": store.counters.get(s.name, 0)}
                        for s in sources
                    ],
                },
                default=str,
            )
        )

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
