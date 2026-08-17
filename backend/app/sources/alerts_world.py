"""Alertes officielles hors USA.

Le trou que ces deux sources ferment: SOSForge n'avait d'alertes meteo, crues et
tempetes que pour les Etats-Unis (`nws`). Le reste du monde n'avait que GDACS,
qui ne voit que les catastrophes majeures.

- **Meteoalarm** agrege les vigilances des services meteo nationaux europeens,
  en CAP, un flux par pays.
- **l'agregat CAP de l'OMM** couvre le reste (Inde, Chine, Indonesie, Amerique
  du Sud...) en un seul appel.

Aucune des deux ne porte de coordonnees: les zones sont decrites par des codes
administratifs (NUTS3 en Europe). Les evenements sortent donc sans position, ce
que le modele accepte -- ils s'affichent dans le flux, pas sur la carte.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.models.event import Event, Kind, Severity, to_utc
from app.sources.base import Emit, Source
from app.sources.regional import USER_AGENT, JsonPollSource

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ Meteoalarm

METEOALARM_API = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-{country}"

# Les pays les plus exposes et les plus peuples de la zone couverte. La liste
# est volontairement courte: c'est un GET par pays et par cycle, et Meteoalarm
# n'expose aucun flux paneuropeen (`feeds-europe` renvoie 404).
METEOALARM_COUNTRIES = (
    "france",
    "italy",
    "spain",
    "germany",
    "greece",
    "portugal",
    "united-kingdom",
    "poland",
    "netherlands",
    "croatia",
)

# `awareness_type` est un code standard Meteoalarm, en anglais, la ou `event`
# est redige dans la langue du pays. C'est donc lui qui doit piloter le type.
AWARENESS_TYPE_KIND = {
    "1": Kind.STORM,  # vent
    "2": Kind.STORM,  # neige, verglas
    "3": Kind.STORM,  # orages
    "4": Kind.OTHER,  # brouillard
    "5": Kind.HEAT,  # temperature haute
    "6": Kind.STORM,  # temperature basse
    "7": Kind.FLOOD,  # phenomene cotier
    "8": Kind.WILDFIRE,  # feu de foret
    "9": Kind.OTHER,  # avalanches
    "10": Kind.STORM,  # pluie
    "11": Kind.FLOOD,  # inondation
    "12": Kind.FLOOD,  # pluie-inondation
}

# niveau 1 vert (aucun danger) -> 4 rouge (danger majeur)
AWARENESS_LEVEL_SEVERITY = {
    "1": Severity.INFO,
    "2": Severity.MODERATE,
    "3": Severity.SEVERE,
    "4": Severity.EXTREME,
}


def _parameters(info: dict) -> dict[str, str]:
    return {
        p.get("valueName"): p.get("value")
        for p in info.get("parameter") or []
        if p.get("valueName")
    }


def _level_of(event: Event) -> int:
    """Niveau Meteoalarm (1 vert -> 4 rouge) relu depuis le brut."""
    raw = (event.raw.get("awareness_level") or "").split(";")[0].strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _pick_info(blocks: list[dict]) -> dict | None:
    """Une alerte Meteoalarm porte le meme contenu deux fois: langue locale et
    anglais. Sans ce choix, chaque vigilance produisait deux evenements."""
    if not blocks:
        return None
    for block in blocks:
        if (block.get("language") or "").lower().startswith("en"):
            return block
    return blocks[0]


def parse_meteoalarm(warning: dict, country: str) -> Event | None:
    alert = warning.get("alert") or {}
    identifier = alert.get("identifier")
    info = _pick_info(alert.get("info") or [])
    if not identifier or not info:
        return None

    params = _parameters(info)
    level, awareness_type = None, None
    if params.get("awareness_level"):
        # format compose: "1; green; Minor"
        level = params["awareness_level"].split(";")[0].strip()
    if params.get("awareness_type"):
        awareness_type = params["awareness_type"].split(";")[0].strip()

    severity = AWARENESS_LEVEL_SEVERITY.get(level or "", Severity.INFO)
    kind = AWARENESS_TYPE_KIND.get(awareness_type or "", Kind.OTHER)

    # `AllClear` veut dire que la vigilance est LEVEE. Comme les bulletins
    # tsunami "pas de danger", elle s'affiche mais n'alerte pas.
    lifted = "AllClear" in (info.get("responseType") or [])
    if lifted:
        severity = Severity.INFO

    areas = info.get("area") or []
    place = ", ".join(a.get("areaDesc", "") for a in areas[:3] if a.get("areaDesc"))
    time = to_utc(info.get("onset")) or to_utc(info.get("effective"))
    if time is None:
        return None

    return Event(
        id=f"meteoalarm:{identifier}",
        source="meteoalarm",
        source_id=identifier,
        kind=kind,
        time=time,
        place=place or country.replace("-", " ").title(),
        country=country.replace("-", " "),
        severity=severity,
        # une vigilance court jusqu'a son expiration: c'est une alerte en cours
        ongoing=not lifted,
        alert=("levee" if lifted else (params.get("awareness_level") or "").split(";")[-1].strip())
        or None,
        title=info.get("headline") or info.get("event") or place,
        url=info.get("web"),
        raw={
            "event": info.get("event"),
            "awareness_level": params.get("awareness_level"),
            "awareness_type": params.get("awareness_type"),
            "expires": info.get("expires"),
            "areas": len(areas),
        },
    )


class MeteoalarmSource(Source):
    """Un GET par pays et par cycle, sequentiels: leur serveur n'a pas a subir
    dix requetes simultanees toutes les cinq minutes.

    **Seuil de gravite obligatoire.** Sans lui, dix pays europeens rendent plus
    de 2000 vigilances par cycle -- essentiellement du jaune "orages possibles"
    -- qui evincent seismes et tsunamis du buffer. SOSForge montre des
    evenements, pas le bulletin meteo: on ne garde que l'orange et le rouge,
    c'est-a-dire un danger reel pour les personnes.
    """

    name = "meteoalarm"
    kind = "poll"

    def __init__(
        self,
        poll_seconds: float = 300.0,
        countries: tuple[str, ...] | None = None,
        min_level: int = 3,
    ):
        super().__init__()
        self.poll_seconds = poll_seconds
        self.countries = countries or METEOALARM_COUNTRIES
        self.min_level = min_level

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=25.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                seen, alive = 0, 0
                for country in self.countries:
                    try:
                        resp = await client.get(METEOALARM_API.format(country=country))
                        resp.raise_for_status()
                        alive += 1
                        kept = 0
                        for warning in (resp.json() or {}).get("warnings") or []:
                            event = parse_meteoalarm(warning, country)
                            if event and _level_of(event) >= self.min_level:
                                kept += 1
                                await emit(event)
                        seen += kept
                        # on marque la sante a CHAQUE pays: dix GET sequentiels
                        # prennent plus d'une minute, et la source paraissait
                        # morte pendant tout son premier cycle
                        self.health.ok(kept)
                    except Exception as exc:
                        self.health.fail(exc)
                        log.warning("meteoalarm %s: %s", country, exc)
                # la sante est deja marquee pays par pays au-dessus; la remarquer
                # ici additionnait le meme total une seconde fois
                if not alive:
                    log.warning("meteoalarm: aucun pays joignable sur ce cycle")
                await asyncio.sleep(self.poll_seconds)


# ------------------------------------------------------------------- OMM (WMO)

# `s`, `u`, `c` encodent severity / urgency / certainty CAP par leur rang
# (1 = le plus grave), 0 quand le pays ne l'a pas renseigne.
WMO_SEVERITY = {
    1: Severity.EXTREME,
    2: Severity.SEVERE,
    3: Severity.MODERATE,
    4: Severity.MINOR,
}

# Les motifs sont cherches sur des MOTS entiers: en sous-chaine, "Flash Flood"
# contenait "ash" et devenait une alerte volcanique.
WMO_KIND_PATTERNS: list[tuple[tuple[str, ...], Kind]] = [
    (("tsunami",), Kind.TSUNAMI),
    (("volcano", "volcanic", "ash", "ashfall"), Kind.VOLCANO),
    (("cyclone", "hurricane", "typhoon", "tropical"), Kind.CYCLONE),
    (("flood", "flooding", "inundation", "crue"), Kind.FLOOD),
    (("fire", "wildfire", "bushfire"), Kind.WILDFIRE),
    (("earthquake", "seismic"), Kind.EARTHQUAKE),
    (("heat", "hot"), Kind.HEAT),
    (("drought",), Kind.DROUGHT),
    (("rain", "storm", "wind", "snow", "thunder", "gale", "blizzard"), Kind.STORM),
]


def classify_wmo(event_name: str) -> Kind:
    words = set(re.findall(r"[a-z]+", (event_name or "").lower()))
    for patterns, kind in WMO_KIND_PATTERNS:
        if words & set(patterns):
            return kind
    return Kind.OTHER


def parse_wmo(item: dict) -> Event | None:
    item_id = item.get("id")
    if not item_id:
        return None

    # `sent` et `effective` sont sans fuseau et en UTC
    time = to_utc((item.get("sent") or "").replace(" ", "T")) or to_utc(
        (item.get("effective") or "").replace(" ", "T")
    )
    if time is None:
        return None

    def rank(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    severity = WMO_SEVERITY.get(rank(item.get("s")) or 0, Severity.INFO)

    # l'identifiant est prefixe du code pays ISO2 ("IN-...", "CN-..."): c'est la
    # seule indication de pays du flux, et elle suffit a poser un drapeau
    prefix = str(item_id).split("-", 1)[0]
    country_code = prefix.upper() if len(prefix) == 2 and prefix.isalpha() else None
    event_name = item.get("event") or "alerte"
    place = item.get("areaDesc") or ""

    return Event(
        id=f"wmo:{item_id}",
        source="wmo",
        source_id=str(item_id),
        kind=classify_wmo(event_name),
        time=time,
        place=place[:120] or event_name,
        country_code=country_code,
        severity=severity,
        ongoing=True,
        alert=event_name.lower(),
        title=item.get("headline") or event_name,
        # le JSON agrege a montre des horaires incoherents avec le CAP source:
        # pour toute heure critique, c'est le CAP qui fait foi
        url=f"https://severeweather.wmo.int/v2/cap-alerts/{item.get('url')}"
        if item.get("url")
        else None,
        raw={
            "event": event_name,
            "expires": item.get("expires"),
            "urgency_rank": rank(item.get("u")),
            "certainty_rank": rank(item.get("c")),
            "member": item.get("mid"),
        },
    )


class WmoCapSource(JsonPollSource):
    """Agregat CAP mondial de l'Organisation meteorologique mondiale.

    Un megaoctet par appel: on envoie `If-Modified-Since` pour obtenir un 304
    tant que le fichier n'a pas bouge, plutot que de retelecharger 2200 alertes
    toutes les cinq minutes.
    """

    name = "wmo"
    kind = "poll"
    url = "https://severeweather.wmo.int/json/wmo_all.json"

    def __init__(self, poll_seconds: float = 300.0, max_severity_rank: int = 1):
        super().__init__(poll_seconds)
        self._last_modified: str | None = None
        # `s` est un rang CAP: 1 = Extreme, 2 = Severe. Au-dela on entre dans le
        # bulletin meteo courant, et l'agregat en contient 2250 par cycle -- de
        # quoi remplir le buffer a lui seul et enterrer tout le reste.
        self.max_severity_rank = max_severity_rank

    def parse_payload(self, data: Any) -> list[Event]:
        events = []
        for item in (data or {}).get("items") or []:
            try:
                rank = int(item.get("s"))
            except (TypeError, ValueError):
                continue
            if not 1 <= rank <= self.max_severity_rank:
                continue
            event = parse_wmo(item)
            if event:
                events.append(event)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=60.0, headers=headers, follow_redirects=True
        ) as client:
            while True:
                try:
                    conditional = dict(headers)
                    if self._last_modified:
                        conditional["If-Modified-Since"] = self._last_modified

                    resp = await client.get(self.url, headers=conditional)
                    if resp.status_code == 304:
                        self.health.ok()
                    else:
                        resp.raise_for_status()
                        self._last_modified = resp.headers.get("last-modified")
                        events = self.parse_payload(resp.json())
                        for event in events:
                            await emit(event)
                        self.health.ok(len(events))
                except Exception as exc:
                    self.health.fail(exc)
                    log.warning("wmo: %s", exc)
                await asyncio.sleep(self.poll_seconds)
