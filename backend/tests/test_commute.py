"""Unit tests for the route-matrix client in `app.wg_agent.commute`.

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


def _matrix_response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


def test_travel_times_parses_duration_seconds(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    canned = {
        "status": "OK",
        "rows": [{"elements": [
            {"status": "OK", "duration": {"value": 1080}},
            {"status": "OK", "duration": {"value": 600}},
        ]}],
    }

    async def fake_get(self, url, params=None):  # noqa: A002
        return _matrix_response(canned)

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
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

    async def fake_get(self, url, params=None):  # noqa: A002
        nonlocal call_count
        call_count += 1
        return _matrix_response(
            {"status": "OK", "rows": [{"elements": [{"status": "OK", "duration": {"value": 500}}]}]}
        )

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
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


def test_travel_times_waits_for_shared_google_maps_slots(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    wait_turn = AsyncMock()

    async def fake_get(self, url, params=None):  # noqa: A002
        return _matrix_response(
            {"status": "OK", "rows": [{"elements": [{"status": "OK", "duration": {"value": 500}}]}]}
        )

    with (
        patch.object(httpx.AsyncClient, "get", new=fake_get),
        patch.object(commute.google_maps, "wait_turn", new=wait_turn),
    ):
        asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=[_destinations()[0]],
                modes=["TRANSIT", "BICYCLE"],
            )
        )

    assert wait_turn.await_count == 2


def test_travel_times_http_error_returns_empty(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    fake_get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
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

    fake_get = AsyncMock()

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        out = asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=_destinations(),
                modes=["TRANSIT"],
            )
        )
    assert out == {}
    fake_get.assert_not_called()


def test_travel_times_skips_unreachable_pairs(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")

    canned = {
        "status": "OK",
        "rows": [{"elements": [
            {"status": "OK", "duration": {"value": 900}},
            {"status": "ZERO_RESULTS"},
        ]}],
    }

    async def fake_get(self, url, params=None):  # noqa: A002
        return _matrix_response(canned)

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        out = asyncio.run(
            commute.travel_times(
                origin=(48.1, 11.5),
                destinations=_destinations(),
                modes=["DRIVE"],
            )
        )
    assert out == {("ChIJ_TUM", "DRIVE"): 900}


def test_next_9am_weekday_ts_is_future_weekday_at_9am() -> None:
    import datetime, zoneinfo

    ts = commute._next_9am_weekday_ts()
    dt = datetime.datetime.fromtimestamp(ts, tz=zoneinfo.ZoneInfo("Europe/Berlin"))
    now = datetime.datetime.now(tz=zoneinfo.ZoneInfo("Europe/Berlin"))

    assert dt > now, "departure_time must be in the future"
    assert dt.hour == 9 and dt.minute == 0 and dt.second == 0, "must be at 09:00:00"
    assert dt.weekday() < 5, "must be a weekday (Mon–Fri)"


def test_departure_time_set_for_drive_and_transit(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")
    # Grasmaierstraße 25d, 80805 München — used as the listing origin
    origin = (48.1769, 11.5838)

    captured_params: list[dict] = []

    async def fake_get(self, url, params=None):  # noqa: A002
        captured_params.append(dict(params or {}))
        return _matrix_response(
            {"status": "OK", "rows": [{"elements": [{"status": "OK", "duration": {"value": 900}}]}]}
        )

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        asyncio.run(
            commute.travel_times(
                origin=origin,
                destinations=[_destinations()[0]],
                modes=["DRIVE", "TRANSIT", "BICYCLE"],
            )
        )

    by_mode = {p["mode"]: p for p in captured_params}
    assert "departure_time" in by_mode["driving"], "DRIVE must send departure_time"
    assert "departure_time" in by_mode["transit"], "TRANSIT must send departure_time"
    assert "departure_time" not in by_mode["bicycling"], "BICYCLE must NOT send departure_time"

    # departure_time must be an integer Unix timestamp, not the string "now"
    assert isinstance(by_mode["driving"]["departure_time"], int)
    assert isinstance(by_mode["transit"]["departure_time"], int)


def test_departure_time_is_future_9am(monkeypatch) -> None:
    import datetime, zoneinfo

    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "fake-key")
    origin = (48.1769, 11.5838)

    captured_params: list[dict] = []

    async def fake_get(self, url, params=None):  # noqa: A002
        captured_params.append(dict(params or {}))
        return _matrix_response(
            {"status": "OK", "rows": [{"elements": [{"status": "OK", "duration": {"value": 600}}]}]}
        )

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        asyncio.run(
            commute.travel_times(
                origin=origin,
                destinations=[_destinations()[0]],
                modes=["TRANSIT"],
            )
        )

    ts = captured_params[0]["departure_time"]
    dt = datetime.datetime.fromtimestamp(ts, tz=zoneinfo.ZoneInfo("Europe/Berlin"))
    assert dt.hour == 9
    assert dt.weekday() < 5


def test_modes_for_always_includes_transit() -> None:
    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=False, has_car=False)
    assert commute.modes_for(sp) == ["TRANSIT"]

    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=True, has_car=False)
    assert commute.modes_for(sp) == ["TRANSIT", "BICYCLE"]

    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=False, has_car=True)
    assert commute.modes_for(sp) == ["TRANSIT", "DRIVE"]

    sp = SearchProfile(city="München", max_rent_eur=900, has_bike=True, has_car=True)
    assert commute.modes_for(sp) == ["TRANSIT", "BICYCLE", "DRIVE"]
