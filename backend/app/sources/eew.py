"""Alerte precoce et couverture asiatique, via le relais Wolfx.

**Ce que ces sources changent de nature.** Les seize autres sources publient
APRES coup: un seisme a eu lieu, une agence le localise, on l'affiche. L'alerte
precoce japonaise (EEW) est emise **pendant** la propagation des ondes, quelques
secondes apres la detection par les stations les plus proches, avant que les
ondes destructrices n'atteignent les villes. C'est la seule categorie
d'information de ce produit qui puisse encore servir a se mettre a l'abri.

**Reserve assumee, a lire avant de s'y fier.** Wolfx est un relais **tiers non
officiel**. La JMA et le CENC ne publient pas d'API ouverte; ce service
retransmet leurs flux. On le traite donc comme une source "au mieux": elle
enrichit, elle ne fait autorite sur rien, et sa panne ne doit rien casser. Leurs
websockets sont derriere Cloudflare et refusent tout client non navigateur (403),
d'ou le polling.

Deux pieges de fuseau, la raison principale de la vigilance de ce module: la JMA
horodate en heure du Japon et le CENC en heure de Pekin, **tous deux sans
indiquer le decalage**. Un `fromisoformat` naif les daterait de l'heure du
serveur, soit sept a neuf heures d'ecart sur un produit ou la seconde compte.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.models.event import Event, Kind, Severity, severity_from_magnitude
from app.sources.regional import SHINDO_SEVERITY, JsonPollSource

log = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
CST = ZoneInfo("Asia/Shanghai")

# Une EEW annulee (la detection etait un faux positif, frequent en debut
# d'alerte) ne doit surtout pas rester affichee comme une alerte en cours.
JMA_CANCELLED = "キャンセル"
# 警報 = alerte (secousse forte attendue), 予報 = prevision (information)
JMA_WARNING_MARK = "警報"


def _parse_local(value: str | None, zone: ZoneInfo) -> datetime | None:
    """Horodatage local SANS decalage: on pose le fuseau nous-memes."""
    if not value:
        return None
    text = value.strip().replace("/", "-")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=zone).astimezone(UTC)


class JmaEewSource(JsonPollSource):
    """Alerte precoce japonaise (緊急地震速報).

    L'endpoint ne rend qu'UNE alerte: la derniere emise, qu'elle date de dix
    secondes ou de six heures. La fraicheur est donc jugee par le pipeline sur
    l'horodatage reel, pas sur le fait qu'on vienne de la lire.

    Une meme alerte est reemise plusieurs fois avec un `Serial` croissant, la
    magnitude et l'intensite se precisant a chaque envoi. La cle est l'`EventID`,
    donc ces revisions mettent a jour la meme entree au lieu de s'empiler.
    """

    name = "jma_eew"
    kind = "poll"
    url = "https://api.wolfx.jp/jma_eew.json"

    def parse_payload(self, data: Any) -> list[Event]:
        if not isinstance(data, dict):
            return []
        event_id = data.get("EventID")
        if not event_id:
            return []

        status = ((data.get("Issue") or {}).get("Status")) or ""
        if JMA_CANCELLED in status:
            log.info("EEW JMA %s annulee par la source", event_id)
            return []

        time = _parse_local(data.get("OriginTime"), JST) or _parse_local(
            data.get("AnnouncedTime"), JST
        )
        if time is None:
            return []

        # oui, la cle est bien orthographiee "Magunitude" dans leur API
        magnitude = data.get("Magunitude")
        shindo = str(data.get("MaxIntensity") or "").strip()
        place = data.get("Hypocenter") or "Japon"
        title = data.get("Title") or ""

        severity = severity_from_magnitude(
            float(magnitude) if isinstance(magnitude, (int, float)) else None
        )
        if shindo in SHINDO_SEVERITY:
            # l'intensite attendue au sol prime: c'est elle qui dit s'il faut
            # se mettre a l'abri
            severity = max(
                severity,
                SHINDO_SEVERITY[shindo],
                key=lambda s: list(Severity).index(s),
            )
        # une alerte (警報) vaut toujours au moins "fort", meme si la premiere
        # estimation de magnitude est basse: c'est le principe de l'alerte precoce
        if JMA_WARNING_MARK in title and severity is not Severity.EXTREME:
            severity = Severity.SEVERE

        return [
            Event(
                id=f"jma_eew:{event_id}",
                source="jma_eew",
                source_id=str(event_id),
                kind=Kind.EARTHQUAKE,
                time=time,
                lat=data.get("Latitude"),
                lon=data.get("Longitude"),
                depth_km=data.get("Depth"),
                magnitude=float(magnitude) if isinstance(magnitude, (int, float)) else None,
                mag_type="Mj",
                place=place,
                country="Japan",
                country_code="JP",
                severity=severity,
                alert=f"EEW shindo {shindo}" if shindo else "EEW",
                title=f"Alerte precoce -- {place} (shindo {shindo})" if shindo else place,
                url="https://www.jma.go.jp/bosai/map.html#contents=earthquake_map",
                raw={
                    "serial": data.get("Serial"),
                    "report_title": title,
                    "status": status,
                    "max_intensity": shindo,
                    "is_final": data.get("isFinal"),
                    "relay": "wolfx (non officiel)",
                },
            )
        ]


class CencSource(JsonPollSource):
    """CENC (Chine) -- la Chine continentale n'a aucune autre couverture ici.

    Le payload n'est pas un tableau mais un dictionnaire indexe `No1`, `No2`...
    Itérer sur les cles rendrait les chaines "No1", pas les evenements.
    """

    name = "cenc"
    kind = "poll"
    url = "https://api.wolfx.jp/cenc_eqlist.json"

    def parse_payload(self, data: Any) -> list[Event]:
        if not isinstance(data, dict):
            return []

        events: list[Event] = []
        for row in data.values():
            if not isinstance(row, dict):
                continue
            event_id = row.get("EventID")
            if not event_id:
                continue

            time = _parse_local(row.get("time"), CST)
            if time is None:
                continue

            def number(value) -> float | None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            magnitude = number(row.get("magnitude"))
            lat, lon = number(row.get("latitude")), number(row.get("longitude"))
            place = row.get("placeName") or row.get("location") or "Chine"

            events.append(
                Event(
                    id=f"cenc:{event_id}",
                    source="cenc",
                    source_id=str(event_id),
                    kind=Kind.EARTHQUAKE,
                    time=time,
                    lat=lat,
                    lon=lon,
                    depth_km=number(row.get("depth")),
                    magnitude=magnitude,
                    mag_type="M",
                    place=place,
                    country="China",
                    country_code="CN",
                    severity=severity_from_magnitude(magnitude),
                    # "reviewed" (revu par un analyste) vs "automatic"
                    alert=row.get("type"),
                    title=f"M {magnitude} -- {place}" if magnitude else place,
                    url="https://news.ceic.ac.cn/",
                    raw={
                        "type": row.get("type"),
                        "intensity": row.get("intensity"),
                        "relay": "wolfx (non officiel)",
                    },
                )
            )
        return events
