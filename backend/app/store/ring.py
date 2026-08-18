"""Store en memoire: ring buffer + index par id, avec append JSONL optionnel.

Pas de base de donnees en v1: un tracker live a besoin de la derniere heure, pas
d'un entrepot. La persistance JSONL suffit a rejouer/auditer, et Postgres pourra
etre branche derriere la meme interface si l'historique devient un besoin.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models.event import Event, utcnow

log = logging.getLogger(__name__)


class EventStore:
    def __init__(self, maxlen: int = 5000, data_dir: Path | None = None, persist: bool = True):
        self._ring: deque[Event] = deque(maxlen=maxlen)
        self._by_id: dict[str, Event] = {}
        self._lock = threading.RLock()
        self._persist = persist
        self._replaying = False
        self._data_dir = data_dir
        self.counters: dict[str, int] = {}

        if self._persist and self._data_dir:
            self._data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ ecriture

    def upsert(self, event: Event) -> tuple[Event, str]:
        """Insere ou met a jour. Retourne (event, action) ou action = new|update|noop."""
        with self._lock:
            existing = self._by_id.get(event.id)
            if existing is None:
                self._by_id[event.id] = event
                self._ring.append(event)
                if len(self._ring) == self._ring.maxlen:
                    self._gc()
                self.counters[event.source] = self.counters.get(event.source, 0) + 1
                self._write_jsonl(event, "new")
                return event, "new"

            if existing.fingerprint() == event.fingerprint():
                # rien de neuf, mais la source vient de le rementionner: c'est
                # exactement ce que `last_seen` doit enregistrer
                # pendant un replay, `last_seen` doit rester celui du journal:
                # le rafraichir redonnait six heures de sursis a toute alerte
                # morte a chaque redemarrage, et masquait la purge entierement
                if not self._replaying:
                    existing.last_seen = utcnow()
                return existing, "noop"

            # revision: on garde l'objet en place (donc dans le ring) et on le met a jour
            event.received_at = existing.received_at
            event.cluster_id = existing.cluster_id or event.cluster_id
            event.revision = existing.revision + 1
            event.updated_at = utcnow()
            event.last_seen = existing.last_seen if self._replaying else utcnow()
            self._by_id[event.id] = event
            for i, e in enumerate(self._ring):
                if e.id == event.id:
                    self._ring[i] = event
                    break
            self._write_jsonl(event, "update")
            return event, "update"

    def _gc(self) -> None:
        """Le ring a evince des elements: purge l'index, et surtout PROMEUT un
        survivant dans les clusters dont le representant vient de disparaitre.

        Sans cette promotion, un seisme entier sortait du flux: `primary_only`
        masque tout evenement dont le `cluster_id` n'est pas le sien, donc si
        l'EMSC (arrive en premier, evince en premier) partait pendant que la
        solution USGS restait, plus personne n'affichait ce seisme.
        """
        alive = {e.id for e in self._ring}
        for dead in [k for k in self._by_id if k not in alive]:
            self._by_id.pop(dead, None)

        promoted: dict[str, str] = {}
        for event in self._ring:
            cluster = event.cluster_id
            if not cluster or cluster in alive:
                continue
            # le plus ancien survivant du cluster devient le nouveau primaire
            new_primary = promoted.setdefault(cluster, event.id)
            event.cluster_id = new_primary

    def _write_jsonl(self, event: Event, action: str) -> None:
        # `_replaying` evite de dupliquer le journal a chaque redemarrage: sans
        # ce garde-fou, relire 400 evenements les reecrivait aussitot.
        if self._replaying or not (self._persist and self._data_dir):
            return
        day = utcnow().strftime("%Y-%m-%d")
        path = self._data_dir / f"events-{day}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({"action": action, **event.model_dump(mode="json")}, default=str)
                    + "\n"
                )
        except OSError as exc:  # le disque ne doit jamais tuer le flux live
            log.warning("persistance jsonl impossible: %s", exc)

    # ------------------------------------------------------------------ lecture

    def get(self, event_id: str) -> Event | None:
        with self._lock:
            return self._by_id.get(event_id)

    def recent(
        self,
        limit: int = 300,
        kind: str | None = None,
        min_magnitude: float | None = None,
        since: datetime | None = None,
        primary_only: bool = False,
    ) -> list[Event]:
        with self._lock:
            items: Iterable[Event] = self._ring
            out: list[Event] = []
            for e in items:
                if kind and e.kind.value != kind:
                    continue
                if min_magnitude is not None and (e.magnitude or 0) < min_magnitude:
                    continue
                if since and e.time < since:
                    continue
                # un cluster n'est represente que par son primaire (cluster_id == id)
                if primary_only and e.cluster_id and e.cluster_id != e.id:
                    continue
                out.append(e)
        # tri par date d'evenement decroissante: le flux montre ce qui vient de se
        # produire, pas ce qui vient d'etre poll (un bulletin vieux de 3 jours
        # remonte a chaque cycle de polling et squatterait la tete de liste)
        out.sort(key=lambda e: e.time, reverse=True)
        return out[:limit]

    def prune_stale(self, max_silence_hours: float) -> list[Event]:
        """Retire les evenements qu'aucune source n'a rementionnes depuis
        longtemps.

        L'horizon d'ingestion exempte les alertes graves ET en cours, parce
        qu'un cyclone rouge ne devient pas caduc en trois jours. Mais rien ne
        les faisait jamais partir: un cyclone dissipe, un bulletin volcanique
        remplace, restaient affiches pour toujours. Une source qui cesse de
        mentionner une alerte a implicitement dit qu'elle est terminee.
        """
        cutoff = utcnow() - timedelta(hours=max_silence_hours)
        with self._lock:
            # SEULES les alertes en cours sont concernees. Un seisme n'est pas
            # "muet": sa source cesse normalement d'en parler des qu'il sort de
            # sa fenetre de publication. Purger les non-ongoing revenait a ne
            # garder que sept heures d'historique alors que l'UI propose 24 h.
            stale = [e for e in self._ring if e.ongoing and e.last_seen < cutoff]
            if not stale:
                return []
            dead = {e.id for e in stale}
            kept = [e for e in self._ring if e.id not in dead]
            self._ring.clear()
            self._ring.extend(kept)
            for event_id in dead:
                self._by_id.pop(event_id, None)
            self._gc()
        return stale

    def stats(self) -> dict:
        now = utcnow()
        with self._lock:
            events = list(self._ring)
        last_hour = [e for e in events if e.time > now - timedelta(hours=1)]
        quakes = [e for e in last_hour if e.kind.value == "earthquake"]
        mags = [e.magnitude for e in quakes if e.magnitude is not None]
        return {
            "total_buffered": len(events),
            "last_hour": len(last_hour),
            "earthquakes_last_hour": len(quakes),
            "max_magnitude_last_hour": max(mags) if mags else None,
            "tsunami_active": sum(1 for e in last_hour if e.tsunami),
            "by_source": dict(self.counters),
            "server_time": now.isoformat(),
        }

    def purge_journals(self, keep_days: int) -> list[Path]:
        """Supprime les journaux plus vieux que `keep_days`.

        Le journal grossit d'environ 5 Mo par jour et n'etait jamais purge: sur
        un service qui tourne en continu, le volume finit par saturer le disque
        de l'hote -- partage avec les autres produits de la suite.
        """
        if not self._data_dir or not self._data_dir.exists():
            return []
        cutoff = (utcnow() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        removed = []
        for journal in sorted(self._data_dir.glob("events-*.jsonl")):
            day = journal.stem.removeprefix("events-")
            # comparaison de chaines: le format ISO du nom de fichier est trie
            if len(day) == 10 and day < cutoff:
                try:
                    journal.unlink()
                    removed.append(journal)
                except OSError as exc:
                    log.warning("purge du journal %s impossible: %s", journal.name, exc)
        return removed

    def load_backlog(self, path: Path) -> int:
        """Recharge un JSONL au demarrage (redemarrage sans trou dans la carte)."""
        if not path.exists():
            return 0
        loaded = 0
        self._replaying = True
        cutoff = utcnow() - timedelta(hours=24)
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                data = json.loads(line)
                data.pop("action", None)
                event = Event.model_validate(data)
            except Exception:
                continue
            if event.time.replace(tzinfo=event.time.tzinfo or UTC) < cutoff:
                continue
            self.upsert(event)
            loaded += 1
        self._replaying = False
        return loaded
