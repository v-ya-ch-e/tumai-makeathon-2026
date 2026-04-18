"""Shared Google Maps Platform request throttling.

The backend uses Google Geocoding, Distance Matrix, and Places APIs.
Multiple concurrent hunts can otherwise burst into quota or QPS limits,
so this module spaces requests process-wide behind a single async gate.
"""

from __future__ import annotations

import asyncio
import os
import time

DEFAULT_MAX_RPS = 8.0
_next_slot_at = 0.0
_lock = asyncio.Lock()


def _now() -> float:
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def max_rps() -> float:
    raw = os.environ.get("GOOGLE_MAPS_MAX_RPS", "").strip()
    if not raw:
        return DEFAULT_MAX_RPS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MAX_RPS
    if value <= 0:
        return DEFAULT_MAX_RPS
    return value


async def wait_turn() -> None:
    """Space Google Maps requests so aggregate traffic stays bounded."""
    global _next_slot_at

    interval = 1.0 / max_rps()
    async with _lock:
        now = _now()
        wait_for = _next_slot_at - now
        if wait_for > 0:
            await _sleep(wait_for)
            now = _now()
        _next_slot_at = max(_next_slot_at, now) + interval


def _reset_state() -> None:
    """Test helper."""
    global _next_slot_at
    _next_slot_at = 0.0
