"""Live views near an event.

Two tiers, deliberately:

1. **Deep links** (always available, no key, no network call): we compute URLs
   that open Windy, YouTube, NASA Worldview or Google Maps already centered on
   the event's coordinates. This is what works everywhere, immediately.
2. **Real webcams** (optional): if `SOS_WINDY_API_KEY` is set, we query the
   Windy webcams API to list public cameras around the point, with thumbnail
   and link.

We ONLY touch cameras deliberately published by their owners through an
official API. No aggregator of accidentally open streams.
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
    """Views of the area that open without any API key."""
    query = quote_plus(f"{place} live")
    day = (when or "")[:10]

    return [
        {
            "id": "windy-webcams",
            "label": "Windy webcams",
            "detail": "public cameras around the point",
            "url": f"https://www.windy.com/-Webcams/webcams?webcams,{lat:.4f},{lon:.4f},9",
        },
        {
            "id": "youtube-live",
            "label": "YouTube live",
            "detail": "search for live streams over the area",
            "url": f"https://www.youtube.com/results?search_query={query}&sp=EgJAAQ%253D%253D",
        },
        {
            "id": "nasa-worldview",
            "label": "Satellite imagery",
            "detail": "NASA Worldview, today's VIIRS pass",
            "url": (
                "https://worldview.earthdata.nasa.gov/?v="
                f"{lon - 3:.3f},{lat - 2:.3f},{lon + 3:.3f},{lat + 2:.3f}"
                "&l=VIIRS_NOAA20_CorrectedReflectance_TrueColor,Reference_Labels_15m"
                + (f"&t={day}" if day else "")
            ),
        },
        {
            "id": "google-maps",
            "label": "Satellite view",
            "detail": "Google Maps centered on the epicenter",
            "url": f"https://www.google.com/maps/@{lat:.4f},{lon:.4f},11z/data=!3m1!1e3",
        },
    ]


async def windy_webcams(lat: float, lon: float, radius_km: int = 100, limit: int = 8) -> list[dict]:
    """Public Windy cameras. Returns an empty list if no key is configured or
    if the API misbehaves: a supplementary view must never make an event's
    detail page fail."""
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
        log.warning("windy webcams unavailable: %s", exc)
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
