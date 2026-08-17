"""SOSForge -- API + hub temps reel."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.dedupe import Deduper
from app.hub import Client, hub
from app.models.event import utcnow
from app.nearby import deep_links, windy_webcams
from app.pipeline import Pipeline
from app.sources.base import Source
from app.sources.emsc_ws import EmscWebsocketSource
from app.sources.gdacs import GdacsSource
from app.sources.hazards import AshSource, NhcSource
from app.sources.nws import NwsSource
from app.sources.regional import (
    AfadSource,
    BmkgSource,
    GeonetSource,
    IngvSource,
    JmaSource,
)
from app.sources.tsunami import TsunamiSource
from app.sources.usgs import UsgsSource, backfill
from app.sources.volcano import VolcanoSource
from app.store.ring import EventStore

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("sosforge")

store = EventStore(
    maxlen=settings.ring_size, data_dir=settings.data_dir, persist=settings.persist_jsonl
)
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
        built.append(NwsSource(settings.nws_poll_seconds))
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
    return built


async def heartbeat() -> None:
    """Un tick par seconde: prouve au client que le flux est vivant et resynchronise
    les horloges (l'UI affiche un age "il y a N s" calcule sur l'heure serveur)."""
    while True:
        await asyncio.sleep(settings.heartbeat_seconds)
        if hub.client_count:
            await hub.broadcast(
                {
                    "type": "tick",
                    "server_time": utcnow().isoformat(),
                    "stats": store.stats(),
                    "sources": [s.health.snapshot() for s in sources],
                    "clients": hub.client_count,
                }
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global sources
    sources = build_sources()

    # 1. remplir la carte avant d'ouvrir les vannes: d'abord le journal local
    # (il contient les push EMSC et les bulletins, que le backfill USGS ignore),
    # puis le rattrapage reseau.
    pipeline.quiet = True
    try:
        restored = store.load_backlog(settings.data_dir / f"events-{utcnow():%Y-%m-%d}.jsonl")
        if restored:
            log.info("journal local: %d evenements restaures", restored)
    except Exception as exc:
        log.warning("journal local illisible (%s)", exc)
    try:
        count = await backfill(settings.backfill_url, pipeline.emit)
        log.info("backfill: %d evenements charges", count)
    except Exception as exc:
        log.warning("backfill impossible (%s), demarrage a froid", exc)
    finally:
        pipeline.quiet = False

    # 2. lancer les ingesteurs + le heartbeat
    tasks = [asyncio.create_task(s.supervise(pipeline.emit), name=f"src:{s.name}") for s in sources]
    tasks.append(asyncio.create_task(heartbeat(), name="heartbeat"))
    log.info(
        "SOSForge en ligne -- %d sources: %s", len(sources), ", ".join(s.name for s in sources)
    )

    yield

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("SOSForge arrete")


app = FastAPI(
    title="SOSForge",
    version="1.0.0",
    description="Suivi temps reel des seismes, tsunamis et catastrophes naturelles",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list or ["*"],
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


@app.get("/api/events")
async def api_events(
    limit: int = Query(300, ge=1, le=2000),
    kind: str | None = None,
    min_magnitude: float | None = None,
    hours: float | None = Query(None, ge=0.01, le=720),
    primary_only: bool = True,
) -> dict:
    since = utcnow() - timedelta(hours=hours) if hours else None
    events = store.recent(
        limit=limit,
        kind=kind,
        min_magnitude=min_magnitude,
        since=since,
        primary_only=primary_only,
    )
    return {
        "count": len(events),
        "server_time": utcnow().isoformat(),
        "events": [e.public() for e in events],
    }


# ATTENTION a l'ordre: le converter `:path` avale les slashs, donc la route
# generique `/api/events/{event_id:path}` matcherait aussi ".../nearby" si elle
# etait declaree avant. La plus specifique passe en premier.
@app.get("/api/events/{event_id:path}/nearby")
async def api_nearby(event_id: str) -> dict:
    """Vues en direct autour d'un evenement: liens profonds toujours disponibles,
    plus les webcams publiques si une cle Windy est configuree."""
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
        return {"found": False, "id": event_id}
    return {"found": True, "event": event.public(), "raw": event.raw}


@app.get("/api/stats")
async def api_stats() -> dict:
    return store.stats()


@app.get("/api/sources")
async def api_sources() -> dict:
    return {
        "server_time": utcnow().isoformat(),
        "sources": [{**s.health.snapshot(), "mode": s.kind} for s in sources],
    }


# ----------------------------------------------------------------------- websocket


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
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
                    "sources": [s.health.snapshot() for s in sources],
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
                        # le hub nous a ejecte (client trop lent): on ferme
                        # franchement au lieu de laisser une connexion muette
                        await ws.close(code=1013)
                        return
                    payload = getter.result()
                    getter = asyncio.ensure_future(client.queue.get())
                    await ws.send_text(payload)
            finally:
                getter.cancel()
                evicted.cancel()

        async def drain() -> None:
            # on ne fait rien des messages entrants, mais les lire est ce qui
            # detecte la deconnexion du navigateur
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
        log.debug("websocket %s termine: %s", client.id, exc)
    finally:
        await hub.unregister(client)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower()
    )


if __name__ == "__main__":
    main()
