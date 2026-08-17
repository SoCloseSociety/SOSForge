"""Non-regressions issues de l'audit adversarial du 2026-08-17.

Chaque test correspond a un defaut qui avait ete REPRODUIT sur le systeme vivant.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.dedupe import Deduper
from app.hub import QUEUE_MAX, Client, Hub
from app.models.event import Event, Kind, Severity
from app.sources.tsunami import TsunamiSource
from app.store.ring import EventStore


def quake(event_id: str, minutes_ago: float, lat: float = 10.0, lon: float = 20.0) -> Event:
    return Event(
        id=event_id,
        source=event_id.split(":")[0],
        source_id=event_id,
        kind=Kind.EARTHQUAKE,
        time=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        lat=lat,
        lon=lon,
        magnitude=4.0,
        place="quelque part",
    )


def alert(event_id: str) -> Event:
    return Event(
        id=event_id,
        source="nws",
        source_id=event_id,
        kind=Kind.FLOOD,
        time=datetime.now(UTC),
        lat=1.0,
        lon=1.0,
        place="zone",
        severity=Severity.SEVERE,
    )


def test_alert_repolls_no_longer_flush_the_dedup_window():
    """Defaut 1. Les alertes re-emises a chaque cycle (NWS, GDACS, tsunami:
    ~146/minute mesurees) remplissaient l'historique du deduper et evinçaient
    l'entree EMSC avant que l'USGS ne publie sa solution, 5 a 15 min plus tard.
    """
    deduper = Deduper(history=50)
    emsc = quake("emsc:1", minutes_ago=1)
    deduper.assign(emsc)

    # le bruit de fond: dix fois la capacite de l'historique
    for i in range(500):
        deduper.assign(alert(f"nws:{i}"))

    usgs = quake("usgs:1", minutes_ago=0.5)
    deduper.assign(usgs)

    assert usgs.cluster_id == emsc.cluster_id, "le dedup EMSC/USGS doit survivre au bruit"


@pytest.mark.asyncio
async def test_a_source_whose_every_feed_failed_is_not_green():
    """Defaut 2. `health.ok()` etait appele meme quand les deux centres tsunami
    etaient injoignables: l'interface affichait une source d'alerte tsunami
    saine alors qu'elle etait morte."""
    # deux feeds sur un port ferme
    source = TsunamiSource(
        poll_seconds=0.01,
        feeds={"A": "http://127.0.0.1:9/a.xml", "B": "http://127.0.0.1:9/b.xml"},
    )

    async def emit(_: Event) -> None:  # pragma: no cover - jamais appele
        raise AssertionError("aucun evenement ne peut sortir d'un feed mort")

    task = asyncio.create_task(source.run(emit))
    await asyncio.sleep(0.4)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    snapshot = source.health.snapshot()
    assert snapshot["connected"] is False
    assert snapshot["last_error"] is not None
    assert snapshot["errors"] > 0


@pytest.mark.asyncio
async def test_an_evicted_client_is_signalled_not_left_hanging():
    """Defaut 3. Le client trop lent etait retire du hub mais sa websocket
    restait ouverte et muette: sa tache d'envoi dormait pour toujours sur
    queue.get()."""
    hub = Hub()
    client = Client("lent")
    await hub.register(client)

    for i in range(QUEUE_MAX + 5):
        await hub.broadcast({"type": "tick", "n": i})

    assert hub.client_count == 0
    assert client.evicted.is_set(), "l'ejection doit etre signalee a la tache d'envoi"


def test_evicting_a_cluster_primary_promotes_a_survivor():
    """Defaut 4. `primary_only` masque tout evenement dont le cluster_id n'est
    pas le sien. Quand le ring evinçait le representant (l'EMSC, arrive en
    premier, part en premier), le seisme disparaissait entierement du flux."""
    store = EventStore(maxlen=3, data_dir=None, persist=False)

    emsc = quake("emsc:1", 5)
    emsc.cluster_id = "emsc:1"
    usgs = quake("usgs:1", 4)
    usgs.cluster_id = "emsc:1"  # meme cluster, secondaire
    store.upsert(emsc)
    store.upsert(usgs)

    # on sature le ring: emsc:1 est le plus ancien, il saute
    store.upsert(quake("x:1", 3))
    store.upsert(quake("x:2", 2))

    assert store.get("emsc:1") is None
    ids = [e.id for e in store.recent(limit=10, primary_only=True)]
    assert "usgs:1" in ids, "le survivant doit etre promu, pas efface du flux"


def test_replaying_the_journal_does_not_rewrite_it(tmp_path):
    """Defaut 5. Chaque redemarrage reecrivait tout le journal du jour: 747
    lignes dont 368 doublons apres deux redemarrages."""
    store = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    store.upsert(quake("usgs:a", 1))
    journal = next(tmp_path.glob("events-*.jsonl"))
    before = journal.read_text().count("\n")

    reloaded = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    assert reloaded.load_backlog(journal) == 1
    assert journal.read_text().count("\n") == before, "relire ne doit pas reecrire"
