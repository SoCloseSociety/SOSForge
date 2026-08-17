"""Vues en direct a proximite d'un evenement.

Deux niveaux, volontairement:

1. **Les liens profonds** (toujours disponibles, aucune cle, aucun appel reseau):
   on calcule des URLs qui ouvrent Windy, YouTube, NASA Worldview ou Google Maps
   deja centres sur les coordonnees de l'evenement. C'est ce qui marche partout
   et tout de suite.
2. **Les webcams reelles** (optionnel): si `SOS_WINDY_API_KEY` est renseignee, on
   interroge l'API webcams de Windy pour lister les cameras publiques autour du
   point, avec vignette et lien.

On ne touche QUE des cameras publiees volontairement par leurs proprietaires via
une API officielle. Pas d'agregateur de flux ouverts par accident.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

WINDY_API = "https://api.windy.com/webcams/api/v3/webcams"


def deep_links(lat: float, lon: float, place: str, when: str | None = None) -> list[dict[str, str]]:
    """Des vues de la zone qui s'ouvrent sans aucune cle d'API."""
    query = quote_plus(f"{place} live")
    day = (when or "")[:10]

    return [
        {
            "id": "windy-webcams",
            "label": "Webcams Windy",
            "detail": "cameras publiques autour du point",
            "url": f"https://www.windy.com/-Webcams/webcams?webcams,{lat:.4f},{lon:.4f},9",
        },
        {
            "id": "youtube-live",
            "label": "Direct YouTube",
            "detail": "recherche de flux en direct sur la zone",
            "url": f"https://www.youtube.com/results?search_query={query}&sp=EgJAAQ%253D%253D",
        },
        {
            "id": "nasa-worldview",
            "label": "Imagerie satellite",
            "detail": "NASA Worldview, passage VIIRS du jour",
            "url": (
                "https://worldview.earthdata.nasa.gov/?v="
                f"{lon - 3:.3f},{lat - 2:.3f},{lon + 3:.3f},{lat + 2:.3f}"
                "&l=VIIRS_NOAA20_CorrectedReflectance_TrueColor,Reference_Labels_15m"
                + (f"&t={day}" if day else "")
            ),
        },
        {
            "id": "google-maps",
            "label": "Vue satellite",
            "detail": "Google Maps centre sur l'epicentre",
            "url": f"https://www.google.com/maps/@{lat:.4f},{lon:.4f},11z/data=!3m1!1e3",
        },
    ]


async def windy_webcams(lat: float, lon: float, radius_km: int = 100, limit: int = 8) -> list[dict]:
    """Cameras publiques Windy. Retourne une liste vide si aucune cle n'est
    configuree ou si l'API repond mal: une vue d'appoint ne doit jamais faire
    echouer la fiche d'un evenement."""
    if not settings.windy_api_key:
        return []

    params = {
        "nearby": f"{lat},{lon},{radius_km}",
        "include": "images,urls,location",
        "limit": str(limit),
    }
    headers = {"x-windy-api-key": settings.windy_api_key, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
            resp = await client.get(WINDY_API, params=params)
            resp.raise_for_status()
            payload: Any = resp.json()
    except Exception as exc:
        log.warning("webcams windy indisponibles: %s", exc)
        return []

    cameras = []
    for cam in (payload or {}).get("webcams") or []:
        location = cam.get("location") or {}
        images = (cam.get("images") or {}).get("current") or {}
        cameras.append(
            {
                "id": str(cam.get("webcamId")),
                "title": cam.get("title") or "webcam",
                "city": location.get("city"),
                "country": location.get("country"),
                "lat": location.get("latitude"),
                "lon": location.get("longitude"),
                "status": cam.get("status"),
                "updated": cam.get("lastUpdatedOn"),
                "thumbnail": images.get("preview") or images.get("thumbnail"),
                "url": (cam.get("urls") or {}).get("detail"),
            }
        )
    return cameras
