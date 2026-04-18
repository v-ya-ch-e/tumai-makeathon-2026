"""Unit tests for the Routes API client in `app.wg_agent.commute`.

Monkey-patches `httpx.AsyncClient.post` so no network I/O happens and we
exercise the happy path, graceful HTTP failure, and the no-key short-circuit.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx

from app.wg_agent import commute  # noqa: E402
from app.wg_agent.models import PlaceLocation, SearchProfile  # noqa: E402


def _destinations() -> list[PlaceLocation]:
    return [
        PlaceLocation(
            label="TUM", place_id="ChIJ_TUM", lat=48.149, lng=11.568
        ),
        PlaceLocation(
            label="Hauptbahnhof",
            place_id="ChIJ_HBF",
            lat=48.140,
            lng=11.558,
        ),
    ]


def _matrix_response(payload: list[dict[str, Any]]) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


def test_travel_times_parses_duration_seconds(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    canned = [
        {
            "originIndex": 0,
            "destinationIndex": 0,
            "duration": "1080s",
            "condition": "ROUTE_EXISTS",
        },
        {
            "originIndex": 0,
            "destinationIndex": 1,
            "duration": "600s",
            "condition": "ROUTE_EXISTS",
        },
    ]

    async def fake_post(self, url, json=None, headers=None):  # noqa: A002
        return _matrix_response(canned)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        out = asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=_destinations(),
                modes=["TRANSIT"],
            )
        )
    assert out == {("ChIJ_TUM", "TRANSIT"): 1080, ("ChIJ_HBF", "TRANSIT"): 600}


def test_travel_times_calls_once_per_mode(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    call_count = 0

    async def fake_post(self, url, json=None, headers=None):  # noqa: A002
        nonlocal call_count
        call_count += 1
        return _matrix_response(
            [
                {
                    "originIndex": 0,
                    "destinationIndex": 0,
                    "duration": "500s",
                    "condition": "ROUTE_EXISTS",
                }
            ]
        )

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        out = asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=[_destinations()[0]],
                modes=["TRANSIT", "BICYCLE"],
            )
        )
    assert call_count == 2
    assert out == {
        ("ChIJ_TUM", "TRANSIT"): 500,
        ("ChIJ_TUM", "BICYCLE"): 500,
    }


def test_travel_times_http_error_returns_empty(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    fake_post = AsyncMock(side_effect=httpx.ConnectError("boom"))

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        out = asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=_destinations(),
                modes=["TRANSIT"],
            )
        )
    assert out == {}


def test_travel_times_without_api_key_skips_network(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_MAPS_SERVER_KEY", raising=False)

    fake_post = AsyncMock()

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        out = asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=_destinations(),
                modes=["TRANSIT"],
            )
        )
    assert out == {}
    fake_post.assert_not_called()


def test_travel_times_skips_unreachable_pairs(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    canned = [
        {
            "originIndex": 0,
            "destinationIndex": 0,
            "duration": "900s",
            "condition": "ROUTE_EXISTS",
        },
        {
            "originIndex": 0,
            "destinationIndex": 1,
            "condition": "ROUTE_NOT_FOUND",
        },
    ]

    async def fake_post(self, url, json=None, headers=None):  # noqa: A002
        return _matrix_response(canned)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        out = asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=_destinations(),
                modes=["DRIVE"],
            )
        )
    assert out == {("ChIJ_TUM", "DRIVE"): 900}


def test_modes_for_always_includes_transit() -> None:
    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=False, has_car=False)
    assert commute.modes_for(sp) == ["TRANSIT"]

    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=True, has_car=False)
    assert commute.modes_for(sp) == ["TRANSIT", "BICYCLE"]

    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=False, has_car=True)
    assert commute.modes_for(sp) == ["TRANSIT", "DRIVE"]

    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=True, has_car=True)
    assert commute.modes_for(sp) == ["TRANSIT", "BICYCLE", "DRIVE"]
