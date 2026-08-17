"""Le chemin unique que suit tout evenement, quelle que soit sa source.

    source -> normalize -> filtre -> dedupe -> store -> hub -> navigateurs

Un seul point d'entree: si un jour une source dedoublonne mal ou floode, c'est ici
qu'on regarde.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.countries import resolve as resolve_country
from app.dedupe import Deduper
from app.hub import hub
from app.models.event import Event, Kind, Severity
from app.store.ring import EventStore

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store: EventStore, deduper: Deduper):
        self.store = store
        self.deduper = deduper
        self.ingested = 0
        self.dropped = 0
        self.quiet = False  # pendant le backfill: on remplit sans reveiller personne

    async def emit(self, event: Event) -> None:
        if event.magnitude is not None and event.magnitude < settings.min_magnitude:
            self.dropped += 1
            return

        # Un horodatage DANS LE FUTUR est une erreur de source (fuseau mal pose,
        # horloge derivante). Non filtre, il traversait tout: l'horizon le laissait
        # passer (age negatif), il etait annonce "en direct" en permanence, et le
        # tri par date le clouait en tete du flux pour toujours. On tolere une
        # petite avance (les horloges ne sont jamais exactement synchrones) et on
        # rejette au-dela.
        if event.age_seconds < -settings.future_tolerance_seconds:
            self.dropped += 1
            log.warning(
                "%s: horodatage dans le futur de %.0f s, evenement rejete (%s)",
                event.source,
                -event.age_seconds,
                event.id,
            )
            return

        # Horizon d'ingestion. Plusieurs sources servent un catalogue et non un
        # flux: la liste JMA remonte a plus de neuf mois, GDACS garde ses alertes
        # des semaines. Sans horizon, ces archives remplissent le ring buffer et
        # evincent les evenements du moment -- l'inverse exact du produit.
        # Exception: une alerte GRAVE ET EN COURS survit a l'horizon -- un cyclone
        # rouge ne devient pas caduc parce qu'il dure depuis trois jours. Un
        # seisme, lui, est instantane: passe l'horizon c'est de l'histoire, meme
        # a magnitude 8. Sans cette nuance, un vieux seisme japonais a shindo 6
        # restait affiche neuf mois plus tard.
        # "En cours" vient d'abord de la source quand elle le dit (EONET publie
        # status=open, le NHC ne liste que les tempetes actives). La gravite
        # elevee reste un repli pour les sources qui ne le disent pas.
        ongoing = event.ongoing or (
            event.kind is not Kind.EARTHQUAKE
            and event.severity in (Severity.SEVERE, Severity.EXTREME)
        )
        if event.age_seconds > settings.max_event_age_days * 86400 and not ongoing:
            self.dropped += 1
            return

        # Le pays est resolu ICI et pas dans chaque source: six sources sur dix
        # le donnent deja, l'USGS ne donne qu'un texte de lieu, et une regle
        # unique vaut mieux que dix variantes.
        # une source qui connait deja le code pays fait autorite: l'agregat de
        # l'OMM le porte dans l'identifiant, on ne va pas le redeviner d'un texte
        event.country_code = event.country_code or resolve_country(event.country, event.place)

        self.deduper.assign(event)
        stored, action = self.store.upsert(event)
        if action == "noop":
            return

        self.ingested += 1
        if self.quiet:
            return

        # "nouveau pour le store" n'est pas "vient de se produire". GDACS garde ses
        # alertes des jours: au premier cycle, une centaine d'evenements anciens
        # entrent d'un coup. Ils doivent apparaitre sur la carte, mais surtout pas
        # clignoter ni declencher le son comme s'ils venaient de tomber.
        breaking = action == "new" and 0 <= stored.age_seconds <= settings.breaking_seconds

        await hub.broadcast(
            {
                "type": "event" if action == "new" else "update",
                "event": stored.public(),
                "primary": self.deduper.is_primary(stored),
                "breaking": breaking,
            }
        )
        if action == "new" and (stored.severity.value in ("severe", "extreme") or stored.tsunami):
            log.info(
                "ALERTE %s %s M%s %s",
                stored.severity.value.upper(),
                stored.kind.value,
                stored.magnitude,
                stored.place,
            )
