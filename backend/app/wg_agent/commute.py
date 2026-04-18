"""Google Maps distance-matrix client for per-mode commute times.

Called from `HuntEngine.run_find_only` right after an anonymous scrape,
once a listing has coordinates. For every `(main_location, mode)` pair
we issue one Distance Matrix request per mode and return a flat dict of
seconds.

Designed to fail soft: a missing `GOOGLE_MAPS_SERVER_KEY`, HTTP errors,
malformed responses, or per-pair routing failures all result in the
affected pairs being absent from the returned dict. Callers can treat
the dict as authoritative ("if it's not in here, we don't know").
"""

from __future__ import annotations

import datetime
import logging
import os
import zoneinfo
from typing import Optional, Sequence

import httpx

from . import google_maps
from .models import PlaceLocation, SearchProfile

logger = logging.getLogger(__name__)

MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
_TIMEOUT = httpx.Timeout(4.0, connect=3.0)

ALLOWED_MODES = ("DRIVE", "BICYCLE", "TRANSIT")
_MODE_MAP = {"DRIVE": "driving", "BICYCLE": "bicycling", "TRANSIT": "transit"}
_MUNICH_TZ = zoneinfo.ZoneInfo("Europe/Berlin")


def _next_9am_weekday_ts() -> int:
    """Return Unix timestamp for the next 9 AM weekday in Munich (CET/CEST).

    Used as departure_time so Distance Matrix always estimates rush-hour
    conditions regardless of when the matcher loop runs.
    """
    now = datetime.datetime.now(tz=_MUNICH_TZ)
    candidate = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    while candidate.weekday() >= 5:  # Saturday=5, Sunday=6
        candidate += datetime.timedelta(days=1)
    return int(candidate.timestamp())


def modes_for(sp: SearchProfile) -> list[str]:
    """Derive the internal travel-mode list from the user's profile.

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


def _latlng(lat: float, lng: float) -> str:
    return f"{lat},{lng}"


def _parse_time_seconds(raw: object) -> Optional[int]:
    """Distance Matrix puts seconds under `duration.value`."""
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned.endswith("s"):
            cleaned = cleaned[:-1]
        try:
            return int(float(cleaned))
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
    """Call Distance Matrix once for a single mode."""
    mode_param = _MODE_MAP.get(mode)
    if mode_param is None:
        return {}
    params = {
        "origins": _latlng(origin[0], origin[1]),
        "destinations": "|".join(_latlng(d.lat, d.lng) for d in destinations),
        "mode": mode_param,
        "language": "de",
        "units": "metric",
        "key": api_key,
    }
    if mode in ("DRIVE", "TRANSIT"):
        params["departure_time"] = _next_9am_weekday_ts()

    try:
        await google_maps.wait_turn()
        response = await client.get(MATRIX_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Google distance-matrix HTTP error for mode=%s: %s", mode, exc)
        return {}
    except ValueError as exc:
        logger.warning("Google distance-matrix returned non-JSON for mode=%s: %s", mode, exc)
        return {}

    if payload.get("status") != "OK":
        logger.warning(
            "Google distance-matrix status for mode=%s: %r",
            mode,
            payload.get("status"),
        )
        return {}
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        logger.warning("Google distance-matrix unexpected shape for mode=%s: %r", mode, payload)
        return {}
    first_row = rows[0]
    elements = first_row.get("elements") if isinstance(first_row, dict) else None
    if not isinstance(elements, list):
        logger.warning("Google distance-matrix row shape invalid for mode=%s: %r", mode, payload)
        return {}

    out: dict[str, int] = {}
    for idx, element in enumerate(elements):
        if not isinstance(element, dict) or idx >= len(destinations):
            continue
        if element.get("status") != "OK":
            continue
        duration = element.get("duration") or {}
        seconds = _parse_time_seconds(duration.get("value"))
        if seconds is None:
            seconds = _parse_time_seconds(duration.get("text"))
        if seconds is None:
            continue
        out[destinations[idx].place_id] = seconds
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
