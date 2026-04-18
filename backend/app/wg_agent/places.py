"""Google Places API client for nearby preference-oriented amenities.

The onboarding wizard collects neighbourhood preferences such as
`supermarket`, `gym`, or `park`. This module resolves those keys against
real nearby places around a listing's coordinates so preference scoring
can use actual distance data instead of only keyword hits in the listing
description.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Optional, Sequence

import httpx

from . import google_maps
from .models import NearbyPlace, PreferenceWeight

logger = logging.getLogger(__name__)

NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
SEARCH_RADIUS_M = 2000
PARK_RADIUS_M = 5000  # parks can be large; wider radius to catch Englischer Garten etc.
_TIMEOUT = httpx.Timeout(4.0, connect=3.0)
_CACHE_LIMIT = 4096
_cache: dict[str, Optional[NearbyPlace]] = {}

# Maps UI preference keys to (label, primary_types, text_query, radius_override).
# - primary_types: used with searchNearby as `includedPrimaryTypes` — only places
#   whose *primary* Google type matches are returned. This prevents false positives
#   (e.g. gas stations that carry "supermarket" as a secondary type, or indoor
#   atriums tagged as secondary "park"). Empty tuple → use text_query instead.
# - text_query: for searchText when type-based search is insufficient.
# - radius_override: search radius in metres; None → SEARCH_RADIUS_M.
PREFERENCE_PLACE_CATEGORIES: dict[str, tuple[str, tuple[str, ...], Optional[str], Optional[int]]] = {
    "supermarket": ("Supermarket", ("supermarket", "grocery_store"), None, None),
    "gym": ("Gym", ("gym",), None, None),
    "park": ("Park", ("park", "national_park"), None, PARK_RADIUS_M),
    "cafe": ("Cafe", ("cafe", "coffee_shop"), None, None),
    "bars": ("Bars", ("bar", "pub"), None, None),
    "library": ("Library", ("library",), None, None),
    "coworking": ("Coworking", tuple(), "coworking space", None),
    "nightlife": ("Nightlife", ("night_club", "bar", "pub"), None, None),
    "green_space": ("Green space", ("park", "national_park"), None, PARK_RADIUS_M),
    "public_transport": (
        "Public transport",
        ("transit_station", "subway_station", "train_station", "bus_station"),
        None,
        None,
    ),
}


def supports_preference(key: str) -> bool:
    return key in PREFERENCE_PLACE_CATEGORIES


def _placeholder(key: str, *, searched: bool) -> Optional[NearbyPlace]:
    spec = PREFERENCE_PLACE_CATEGORIES.get(key)
    if spec is None:
        return None
    label, _primary_types, _text_query, _radius = spec
    return NearbyPlace(key=key, label=label, searched=searched)


def _cache_key(lat: float, lng: float, key: str) -> str:
    return f"{key}:{round(lat, 5)}:{round(lng, 5)}"


def _unique_supported_keys(preferences: Sequence[PreferenceWeight]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for pref in preferences:
        if pref.key in seen or not supports_preference(pref.key):
            continue
        seen.add(pref.key)
        out.append(pref.key)
    return out


async def _fetch_one(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    origin: tuple[float, float],
    key: str,
) -> Optional[NearbyPlace]:
    spec = PREFERENCE_PLACE_CATEGORIES.get(key)
    if spec is None:
        return None
    label, primary_types, text_query, radius_override = spec
    radius = radius_override if radius_override is not None else SEARCH_RADIUS_M
    lat, lng = origin
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.location,places.primaryType,places.types",
    }
    if text_query is not None:
        url = TEXT_URL
        body = {
            "textQuery": text_query,
            "pageSize": 1,
            "rankPreference": "DISTANCE",
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius),
                }
            },
        }
    else:
        url = NEARBY_URL
        # includedPrimaryTypes (not includedTypes) restricts to places whose
        # *primary* Google type matches — this prevents secondary-type false
        # positives such as gas stations tagged as secondary "supermarket".
        body = {
            "includedPrimaryTypes": list(primary_types),
            "maxResultCount": 1,
            "rankPreference": "DISTANCE",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius),
                }
            },
        }
    try:
        await google_maps.wait_turn()
        response = await client.post(url, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Google places HTTP error for key=%s: %s", key, exc)
        return _placeholder(key, searched=False)
    except ValueError as exc:
        logger.warning("Google places returned non-JSON for key=%s: %s", key, exc)
        return _placeholder(key, searched=False)

    results = payload.get("places")
    if not isinstance(results, list):
        logger.warning("Google places unexpected shape for key=%s: %r", key, payload)
        return _placeholder(key, searched=False)
    if not results:
        return NearbyPlace(key=key, label=label, searched=True)

    place = results[0]
    if not isinstance(place, dict):
        return NearbyPlace(key=key, label=label, searched=True)

    display_name = place.get("displayName") or {}
    location = place.get("location") or {}
    category = place.get("primaryType")
    categories_out = place.get("types")
    distance_m = _distance_meters(
        lat,
        lng,
        location.get("latitude"),
        location.get("longitude"),
    )
    if distance_m is not None and distance_m > radius:
        return NearbyPlace(key=key, label=label, searched=True)
    return NearbyPlace(
        key=key,
        label=label,
        searched=True,
        distance_m=distance_m,
        place_name=(
            str(display_name.get("text"))[:160]
            if isinstance(display_name, dict) and display_name.get("text")
            else None
        ),
        category=(
            str(category)
            if isinstance(category, str)
            else (
                str(categories_out[0])
                if isinstance(categories_out, list) and categories_out
                else None
            )
        ),
    )


def _distance_meters(
    origin_lat: float,
    origin_lng: float,
    place_lat: object,
    place_lng: object,
) -> Optional[int]:
    if not isinstance(place_lat, (int, float)) or not isinstance(place_lng, (int, float)):
        return None
    lat1 = math.radians(origin_lat)
    lng1 = math.radians(origin_lng)
    lat2 = math.radians(float(place_lat))
    lng2 = math.radians(float(place_lng))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return int(6371000 * c)


async def nearby_places(
    *,
    origin: tuple[float, float],
    preferences: Sequence[PreferenceWeight],
) -> dict[str, NearbyPlace]:
    """Return nearby-place facts for supported preference keys.

    Missing API key or network failures degrade to `{}` so callers can
    treat the absence of a key as "unknown" rather than "missing nearby".
    """
    supported = _unique_supported_keys(preferences)
    if not supported:
        return {}
    api_key = os.environ.get("GOOGLE_MAPS_SERVER_KEY")
    if not api_key:
        return {
            key: item
            for key in supported
            if (item := _placeholder(key, searched=False)) is not None
        }

    out: dict[str, NearbyPlace] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for key in supported:
            ck = _cache_key(origin[0], origin[1], key)
            if ck in _cache:
                cached = _cache[ck]
                if cached is not None:
                    out[key] = cached
                continue
            item = await _fetch_one(
                client=client,
                api_key=api_key,
                origin=origin,
                key=key,
            )
            if len(_cache) >= _CACHE_LIMIT:
                _cache.clear()
            _cache[ck] = item
            if item is not None:
                out[key] = item
    return out
