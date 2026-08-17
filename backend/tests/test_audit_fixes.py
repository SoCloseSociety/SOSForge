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
from app.pipeline import Pipeline
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


@pytest.mark.asyncio
async def test_a_future_dated_event_is_rejected(monkeypatch):
    """Defaut 7. Un horodatage dans le futur (fuseau mal pose cote source)
    traversait tout: age negatif donc horizon franchi, `breaking` toujours vrai,
    et tri par date decroissante -- il se clouait en tete du flux pour toujours.
    """
    from app import pipeline as pipeline_module
    from app.core import config

    sent: list[dict] = []

    async def capture(message: dict) -> None:
        sent.append(message)

    monkeypatch.setattr(pipeline_module.hub, "broadcast", capture)
    monkeypatch.setattr(config.settings, "future_tolerance_seconds", 120.0)

    store = EventStore(maxlen=50, data_dir=None, persist=False)
    pipeline = Pipeline(store, Deduper())

    await pipeline.emit(quake("bad:1", minutes_ago=-30))  # date 30 min en avance
    assert store.recent(limit=10) == []
    assert pipeline.dropped == 1
    assert sent == []

    # une avance d'une minute reste toleree: les horloges ne sont jamais exactes
    await pipeline.emit(quake("ok:1", minutes_ago=-1))
    assert [e.id for e in store.recent(limit=10)] == ["ok:1"]
    # ... mais elle n'est pas annoncee comme "vient de se produire"
    assert sent[-1]["breaking"] is False


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


# --- Second audit adversarial (defauts confirmes sur donnees reelles) ---------


def test_a_warning_issued_in_advance_is_not_rejected_as_a_clock_error():
    """Defaut 8. Une vigilance meteo est PUBLIEE AVANT son debut: c'est tout son
    interet, le preavis. Son `onset` est donc legitimement dans le futur, et le
    filtre anti-futur la rejetait a chaque cycle."""
    from app.models.event import Kind as K

    vigilance = quake("meteoalarm:1", minutes_ago=-96)  # debut dans 1 h 36
    vigilance.kind = K.STORM
    vigilance.ongoing = True
    assert vigilance.age_seconds < 0

    store = EventStore(maxlen=10, data_dir=None, persist=False)
    pipeline = Pipeline(store, Deduper())
    asyncio.run(pipeline.emit(vigilance))
    assert [e.id for e in store.recent(limit=5)] == ["meteoalarm:1"]

    # un seisme, lui, ne peut pas etre date en avance
    futur = quake("usgs:futur", minutes_ago=-96)
    asyncio.run(pipeline.emit(futur))
    assert store.get("usgs:futur") is None


def test_the_sweep_only_removes_ongoing_alerts():
    """Defaut 9. Sans filtre `ongoing`, la purge effaçait les seismes ordinaires
    apres six heures de silence: le store ne gardait plus que sept heures
    d'historique alors que l'interface propose 24 h et "tout"."""
    from datetime import timedelta as _td

    store = EventStore(maxlen=50, data_dir=None, persist=False)

    ancien_seisme = quake("usgs:vieux", 60 * 8)
    ancien_seisme.last_seen = datetime.now(UTC) - _td(hours=8)
    store.upsert(ancien_seisme)

    alerte_muette = alert("gdacs:muette")
    alerte_muette.ongoing = True
    alerte_muette.last_seen = datetime.now(UTC) - _td(hours=8)
    store.upsert(alerte_muette)

    removed = store.prune_stale(max_silence_hours=6)
    assert [e.id for e in removed] == ["gdacs:muette"]
    assert store.get("usgs:vieux") is not None


def test_replaying_the_journal_does_not_reset_the_silence_clock(tmp_path):
    """Defaut 10. Le replay rafraichissait `last_seen`, ce qui redonnait six
    heures de sursis a toute alerte morte a CHAQUE redemarrage -- et masquait la
    purge entierement en production."""
    from datetime import timedelta as _td

    store = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    vieille = alert("gdacs:x")
    vieille.ongoing = True
    vieille.last_seen = datetime.now(UTC) - _td(hours=20)
    store.upsert(vieille)

    journal = next(tmp_path.glob("events-*.jsonl"))
    rechargee = EventStore(maxlen=50, data_dir=tmp_path, persist=True)
    rechargee.load_backlog(journal)
    rechargee.load_backlog(journal)  # deuxieme passe: le chemin noop

    restaure = rechargee.get("gdacs:x")
    assert restaure is not None
    silence_h = (datetime.now(UTC) - restaure.last_seen).total_seconds() / 3600
    assert silence_h > 19, "le silence doit survivre au replay"
    assert rechargee.prune_stale(max_silence_hours=6)
