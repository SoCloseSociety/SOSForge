"""Recherche de zone: nom de lieu -> coordonnees, via Nominatim (OpenStreetMap).

Pourquoi passer par le backend plutot que d'appeler Nominatim depuis le
navigateur: leur politique d'usage impose un User-Agent identifiant et **une
requete par seconde maximum**. Un appel direct depuis chaque onglet ouvert
violerait les deux, et nous ferait bannir. Ici, un seul point de sortie, cadence
et cache compris.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SOSForge/1.0 (+https://github.com/SoCloseSociety/SOSForge)"

# la politique Nominatim: une requete par seconde, absolument
_MIN_INTERVAL = 1.0
_last_call = 0.0
_lock = asyncio.Lock()

# une recherche de zone se repete beaucoup ("tokyo", "californie"): un cache
# borne evite d'aller les redemander
_cache: dict[str, list[dict]] = {}
_CACHE_MAX = 500


async def search(query: str, limit: int = 5) -> list[dict]:
    global _last_call

    key = query.strip().lower()
    if not key:
        return []
    if key in _cache:
        return _cache[key]

    async with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=12.0,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            ) as client:
                resp = await client.get(
                    NOMINATIM,
                    params={"q": query, "format": "jsonv2", "limit": str(limit)},
                )
                resp.raise_for_status()
                payload = resp.json() or []
        except Exception as exc:
            # une recherche qui echoue ne doit jamais casser la page: le filtre
            # textuel local, lui, continue de fonctionner
            log.warning("geocodage indisponible: %s", exc)
            return []

    results = []
    for row in payload:
        try:
            results.append(
                {
                    "name": row.get("display_name") or query,
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "type": row.get("type"),
                    # bbox Nominatim: [sud, nord, ouest, est], pas l'ordre GeoJSON
                    "bbox": [float(v) for v in row.get("boundingbox", [])] or None,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = results
    return results
