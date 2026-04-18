"""Nearby-place lookup tests for `app.wg_agent.places`."""

from __future__ import annotations

import asyncio
import pathlib
import sys
from typing import Any

import httpx
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import places  # noqa: E402
from app.wg_agent.models import PreferenceWeight  # noqa: E402


def _reset_cache() -> None:
    places._cache.clear()


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=None, response=None  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payloads: list[dict[str, Any]], counter: dict[str, int]) -> None:
        self._payloads = payloads
        self._counter = counter

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        idx = self._counter["calls"]
        self._counter["calls"] += 1
        return _FakeResponse(self._payloads[idx])


def test_nearby_places_returns_matches_and_caches(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")
    counter = {"calls": 0}
    payloads = [
        {
            "places": [
                {
                    "displayName": {"text": "Fit Star"},
                    "location": {"latitude": 48.151879, "longitude": 11.568},
                    "primaryType": "gym",
                    "types": ["gym"],
                }
            ]
        }
    ]

    def fake_async_client(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient(payloads, counter)

    monkeypatch.setattr(places.httpx, "AsyncClient", fake_async_client)

    prefs = [PreferenceWeight(key="gym", weight=5)]
    first = asyncio.run(places.nearby_places(origin=(48.149, 11.568), preferences=prefs))
    second = asyncio.run(places.nearby_places(origin=(48.149, 11.568), preferences=prefs))

    assert first["gym"].distance_m == 320
    assert first["gym"].place_name == "Fit Star"
    assert second["gym"].distance_m == 320
    assert counter["calls"] == 1


def test_nearby_places_marks_not_found_inside_radius(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")
    counter = {"calls": 0}

    def fake_async_client(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient([{"places": []}], counter)

    monkeypatch.setattr(places.httpx, "AsyncClient", fake_async_client)

    out = asyncio.run(
        places.nearby_places(
            origin=(48.149, 11.568),
            preferences=[PreferenceWeight(key="park", weight=4)],
        )
    )
    assert out["park"].searched is True
    assert out["park"].distance_m is None


def test_nearby_places_waits_for_shared_google_maps_slot(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")
    counter = {"calls": 0}

    def fake_async_client(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient([{"places": []}], counter)

    wait_turn = AsyncMock()
    monkeypatch.setattr(places.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(places.google_maps, "wait_turn", wait_turn)

    asyncio.run(
        places.nearby_places(
            origin=(48.149, 11.568),
            preferences=[PreferenceWeight(key="park", weight=4)],
        )
    )

    wait_turn.assert_awaited_once()


def test_nearby_places_skips_network_without_key(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.delenv("GOOGLE_MAPS_SERVER_KEY", raising=False)

    def fail_client(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("httpx should not be touched when key is unset")

    monkeypatch.setattr(places.httpx, "AsyncClient", fail_client)

    out = asyncio.run(
        places.nearby_places(
            origin=(48.149, 11.568),
            preferences=[PreferenceWeight(key="gym", weight=4)],
        )
    )
    assert out["gym"].searched is False
    assert out["gym"].distance_m is None


def test_nearby_places_marks_lookup_unavailable_on_http_error(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")

    class BoomClient:
        async def __aenter__(self) -> "BoomClient":
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(places.httpx, "AsyncClient", lambda *_a, **_kw: BoomClient())

    out = asyncio.run(
        places.nearby_places(
            origin=(48.149, 11.568),
            preferences=[PreferenceWeight(key="gym", weight=4)],
        )
    )

    assert out["gym"].searched is False
    assert out["gym"].distance_m is None
