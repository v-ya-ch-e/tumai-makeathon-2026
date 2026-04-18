"""Geocoder unit tests: cache, error paths, and key-gating."""

from __future__ import annotations

import asyncio
import pathlib
import sys
from typing import Any

import httpx
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import geocoder  # noqa: E402


def _reset_cache() -> None:
    geocoder._cache.clear()


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
    def __init__(self, payload: dict[str, Any], counter: dict[str, int]) -> None:
        self._payload = payload
        self._counter = counter

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def get(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        self._counter["calls"] += 1
        return _FakeResponse(self._payload)


def test_geocode_returns_coords_and_caches(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")

    payload = {
        "status": "OK",
        "results": [{"geometry": {"location": {"lat": 48.149, "lng": 11.568}}}],
    }
    counter = {"calls": 0}

    def fake_async_client(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient(payload, counter)

    monkeypatch.setattr(geocoder.httpx, "AsyncClient", fake_async_client)

    first = asyncio.run(geocoder.geocode("TUM"))
    second = asyncio.run(geocoder.geocode("TUM"))

    assert first == (48.149, 11.568)
    assert second == (48.149, 11.568)
    assert counter["calls"] == 1


def test_geocode_returns_none_on_zero_results(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")

    counter = {"calls": 0}

    def fake_async_client(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient({"status": "ZERO_RESULTS", "results": []}, counter)

    monkeypatch.setattr(geocoder.httpx, "AsyncClient", fake_async_client)

    assert asyncio.run(geocoder.geocode("nowhere-xyz")) is None


def test_geocode_waits_for_shared_google_maps_slot(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")

    counter = {"calls": 0}

    def fake_async_client(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient(
            {"status": "OK", "results": [{"geometry": {"location": {"lat": 48.149, "lng": 11.568}}}]},
            counter,
        )

    wait_turn = AsyncMock()
    monkeypatch.setattr(geocoder.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(geocoder.google_maps, "wait_turn", wait_turn)

    assert asyncio.run(geocoder.geocode("TUM")) == (48.149, 11.568)
    wait_turn.assert_awaited_once()


def test_geocode_returns_none_on_bad_payload(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")

    counter = {"calls": 0}

    def fake_async_client(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return _FakeClient(
            {"status": "OK", "bad": "shape"},
            counter,
        )

    monkeypatch.setattr(geocoder.httpx, "AsyncClient", fake_async_client)

    assert asyncio.run(geocoder.geocode("Marienplatz, München")) is None


def test_geocode_swallows_http_error(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")

    class BoomClient:
        async def __aenter__(self) -> "BoomClient":
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def get(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(geocoder.httpx, "AsyncClient", lambda *_a, **_kw: BoomClient())

    assert asyncio.run(geocoder.geocode("Marienplatz, München")) is None


def test_geocode_skips_when_key_unset(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.delenv("GOOGLE_MAPS_SERVER_KEY", raising=False)

    def fail_client(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("httpx should not be touched when key is unset")

    monkeypatch.setattr(geocoder.httpx, "AsyncClient", fail_client)

    assert asyncio.run(geocoder.geocode("Marienplatz, München")) is None
