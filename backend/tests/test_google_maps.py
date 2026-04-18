"""Unit tests for shared Google Maps request throttling."""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import google_maps  # noqa: E402


def test_wait_turn_spaces_calls(monkeypatch) -> None:
    google_maps._reset_state()
    monkeypatch.setenv("GOOGLE_MAPS_MAX_RPS", "8")

    now = 100.0
    slept: list[float] = []

    def fake_now() -> float:
        return now

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        slept.append(seconds)
        now += seconds

    monkeypatch.setattr(google_maps, "_now", fake_now)
    monkeypatch.setattr(google_maps, "_sleep", fake_sleep)

    asyncio.run(google_maps.wait_turn())
    asyncio.run(google_maps.wait_turn())
    asyncio.run(google_maps.wait_turn())

    assert slept == [0.125, 0.125]


def test_wait_turn_uses_default_for_bad_env(monkeypatch) -> None:
    google_maps._reset_state()
    monkeypatch.setenv("GOOGLE_MAPS_MAX_RPS", "not-a-number")

    now = 50.0
    slept: list[float] = []

    def fake_now() -> float:
        return now

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        slept.append(seconds)
        now += seconds

    monkeypatch.setattr(google_maps, "_now", fake_now)
    monkeypatch.setattr(google_maps, "_sleep", fake_sleep)

    asyncio.run(google_maps.wait_turn())
    asyncio.run(google_maps.wait_turn())

    assert slept == [0.125]
