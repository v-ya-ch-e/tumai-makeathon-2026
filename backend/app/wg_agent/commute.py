"""Google Routes API client for per-mode commute times.

Called from `HuntEngine.run_find_only` right after an anonymous scrape,
once a listing has geocoded `(lat, lng)`. For every `(main_location, mode)`
pair we POST to `computeRouteMatrix` (one call per travel mode, since
`travelMode` is a per-request field) and return a flat dict of seconds.

Designed to fail soft: a missing `GOOGLE_MAPS_SERVER_KEY`, HTTP errors,
non-OK responses, or per-element errors all result in the affected pairs
being absent from the returned dict. Callers can treat the dict as
authoritative ("if it's not in here, we don't know").
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Sequence

import httpx

from .models import PlaceLocation, SearchProfile

logger = logging.getLogger(__name__)

ROUTES_URL = (
    "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
)
_FIELD_MASK = "originIndex,destinationIndex,duration,condition"
_TIMEOUT = httpx.Timeout(4.0, connect=3.0)

ALLOWED_MODES = ("DRIVE", "BICYCLE", "TRANSIT")


def modes_for(sp: SearchProfile) -> list[str]:
    """Derive the Routes API `travelMode` list from the user's profile.

    TRANSIT is always requested; DRIVE/BICYCLE are added iff the user has a
    car/bike. Order matters only for consistent logging; callers treat the
    returned dict by `(place_id, mode)` regardless.
    """
    modes = ["TRANSIT"]
    if sp.has_bike:
        modes.append("BICYCLE")
    if sp.has_car:
        modes.append("DRIVE")
    return modes


def _waypoint(lat: float, lng: float) -> dict:
    return {
        "waypoint": {
            "location": {"latLng": {"latitude": lat, "longitude": lng}}
        }
    }


def _parse_duration_seconds(raw: object) -> Optional[int]:
    """Routes API returns durations as `"<int>s"`; tolerate int fallbacks."""
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.endswith("s"):
        try:
            return int(float(raw[:-1]))
        except ValueError:
            return None
    return None


async def _fetch_mode(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    origin: tuple[float, float],
    destinations: Sequence[PlaceLocation],
    mode: str,
) -> dict[str, int]:
    """Call computeRouteMatrix once for a single mode. Returns
    `{destination.place_id: seconds}` for reachable destinations only."""
    body = {
        "origins": [_waypoint(origin[0], origin[1])],
        "destinations": [_waypoint(d.lat, d.lng) for d in destinations],
        "travelMode": mode,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
    }

    try:
        response = await client.post(ROUTES_URL, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Routes API HTTP error for mode=%s: %s", mode, exc)
        return {}
    except ValueError as exc:
        logger.warning("Routes API returned non-JSON for mode=%s: %s", mode, exc)
        return {}

    if not isinstance(payload, list):
        logger.warning("Routes API unexpected shape for mode=%s: %r", mode, payload)
        return {}

    out: dict[str, int] = {}
    for element in payload:
        if not isinstance(element, dict):
            continue
        if element.get("condition") != "ROUTE_EXISTS":
            continue
        dest_idx = element.get("destinationIndex")
        if not isinstance(dest_idx, int) or not (0 <= dest_idx < len(destinations)):
            continue
        seconds = _parse_duration_seconds(element.get("duration"))
        if seconds is None:
            continue
        out[destinations[dest_idx].place_id] = seconds
    return out


async def travel_times(
    *,
    origin: tuple[float, float],
    destinations: Sequence[PlaceLocation],
    modes: Sequence[str],
) -> dict[tuple[str, str], int]:
    """Return `{(place_id, mode): seconds}` for reachable pairs.

    Missing pairs (no route, filtered mode, API failure) are simply absent
    so callers can distinguish "no route" from "not requested" by checking
    membership in `modes` vs. the returned dict.
    """
    if not destinations or not modes:
        return {}
    api_key = os.environ.get("GOOGLE_MAPS_SERVER_KEY")
    if not api_key:
        return {}

    valid_modes = [m for m in modes if m in ALLOWED_MODES]
    if not valid_modes:
        return {}

    out: dict[tuple[str, str], int] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for mode in valid_modes:
            per_mode = await _fetch_mode(
                client=client,
                api_key=api_key,
                origin=origin,
                destinations=destinations,
                mode=mode,
            )
            for place_id, seconds in per_mode.items():
                out[(place_id, mode)] = seconds
    return out
