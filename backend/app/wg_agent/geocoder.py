"""Server-side Google Geocoding API client for listing addresses.

Called from `anonymous_scrape_listing` so every persisted `ListingRow`
can carry `(lat, lng)` for future commute-aware scoring. Designed to
fail soft: missing key, HTTP errors, or empty results all return
`None` rather than raising, so scrape pipelines stay resilient.

Uses an in-process dict cache keyed on the normalized address string
to avoid re-billing the same listing across runs (the same wg-gesucht
listing appears on every rescan).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"
_CACHE_LIMIT = 1024
_cache: dict[str, Optional[tuple[float, float]]] = {}


def _cache_key(address: str) -> str:
    return address.strip().lower()


async def geocode(address: str) -> Optional[tuple[float, float]]:
    """Return `(lat, lng)` for `address`, or `None` if it can't be resolved.

    Never raises. Skips the network entirely when `GOOGLE_MAPS_SERVER_KEY`
    is unset (dev default).
    """
    if not address or not address.strip():
        return None

    key = _cache_key(address)
    if key in _cache:
        return _cache[key]

    api_key = os.environ.get("GOOGLE_MAPS_SERVER_KEY")
    if not api_key:
        return None

    params = {
        "address": address,
        "key": api_key,
        "region": "de",
        "components": "country:DE",
    }

    result: Optional[tuple[float, float]] = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=3.0)) as client:
            response = await client.get(_ENDPOINT, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Geocoding HTTP error for %r: %s", address, exc)
    except ValueError as exc:
        logger.warning("Geocoding returned non-JSON for %r: %s", address, exc)
    else:
        status = payload.get("status")
        results = payload.get("results") or []
        if status != "OK" or not results:
            if status and status != "ZERO_RESULTS":
                logger.warning("Geocoding status=%s for %r", status, address)
        else:
            location = results[0].get("geometry", {}).get("location") or {}
            lat = location.get("lat")
            lng = location.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                result = (float(lat), float(lng))

    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = result
    return result
