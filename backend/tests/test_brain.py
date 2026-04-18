"""Prompt-builder tests for `brain._listing_summary` and the
weighted-preferences block on `_requirements_summary` (no LLM calls).

The commute block must only appear when `travel_times` is truthy, and the
non-commute output must remain byte-for-byte identical to the pre-plan
format so we don't regress other prompts (draft/classify/reply).
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch

from pydantic import HttpUrl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import brain  # noqa: E402
from app.wg_agent.brain import _listing_summary, _requirements_summary  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    Listing,
    NearbyPlace,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
)


def _listing() -> Listing:
    return Listing(
        id="lst1",
        url=HttpUrl("https://www.wg-gesucht.de/lst1.html"),
        title="Sonniges Zimmer",
        city="München",
        district="Maxvorstadt",
        price_eur=720,
        size_m2=18.0,
        wg_size=3,
    )


def test_listing_summary_includes_commute_block() -> None:
    tum = PlaceLocation(label="TUM", place_id="placeA", lat=48.149, lng=11.568)
    out = _listing_summary(
        _listing(),
        travel_times={("placeA", "TRANSIT"): 1200},
        main_locations=[tum],
    )
    assert "Commute times (one-way):" in out
    assert "transit 20 min" in out
    assert "TUM" in out


def test_listing_summary_default_matches_baseline() -> None:
    """Without travel_times, the output must be identical to the pre-plan format."""
    baseline = "\n".join(
        [
            "ID: lst1",
            "Title: Sonniges Zimmer",
            "City/district: München / Maxvorstadt",
            "Rent: 720 €",
            "Size: 18.0 m²",
            "WG size: 3er",
        ]
    )
    assert _listing_summary(_listing()) == baseline
    assert _listing_summary(_listing(), travel_times=None) == baseline
    assert _listing_summary(_listing(), travel_times={}) == baseline


def test_listing_summary_picks_shortest_mode_ordering() -> None:
    """Per-location line lists modes fastest-first so the drawer's rendering
    agrees with the LLM's 'fastest mode' reasoning."""
    tum = PlaceLocation(label="TUM", place_id="placeA", lat=48.149, lng=11.568)
    out = _listing_summary(
        _listing(),
        travel_times={
            ("placeA", "TRANSIT"): 1500,
            ("placeA", "BICYCLE"): 900,
        },
        main_locations=[tum],
    )
    bike_idx = out.index("bike 15 min")
    transit_idx = out.index("transit 25 min")
    assert bike_idx < transit_idx


def test_commute_block_renders_max_budget() -> None:
    """A main location with `max_commute_minutes` must show `(max N min)` so
    the LLM can compare each mode's time to the user's budget."""
    tum = PlaceLocation(
        label="TUM",
        place_id="placeA",
        lat=48.149,
        lng=11.568,
        max_commute_minutes=25,
    )
    out = _listing_summary(
        _listing(),
        travel_times={("placeA", "TRANSIT"): 1200},
        main_locations=[tum],
    )
    assert "max 25 min" in out
    assert "TUM" in out


def test_listing_summary_includes_nearby_places_block() -> None:
    out = _listing_summary(
        _listing(),
        nearby_places={
            "gym": NearbyPlace(
                key="gym",
                label="Gym",
                searched=True,
                distance_m=320,
                place_name="Fit Star",
            )
        },
        preferences=[PreferenceWeight(key="gym", weight=5)],
    )
    assert "Nearby preference places:" in out
    assert "Fit Star" in out
    assert "320 m away" in out


def _sp(preferences: list[PreferenceWeight]) -> SearchProfile:
    return SearchProfile(
        city="München",
        max_rent_eur=900,
        preferences=preferences,
    )


def test_requirements_summary_includes_preferences_block() -> None:
    sp = _sp(
        [
            PreferenceWeight(key="gym", weight=4),
            PreferenceWeight(key="quiet_area", weight=5),
        ]
    )
    out = _requirements_summary(sp)
    assert "Preferences (1=nice, 5=must-have)" in out
    assert "gym (4)" in out
    assert "quiet_area (5)" in out


def test_requirements_summary_omits_preferences_line_when_empty() -> None:
    """No preferences means no Preferences line in the prompt so existing
    integration tests without prefs keep their exact wording."""
    out = _requirements_summary(_sp([]))
    assert "Preferences" not in out


def test_client_ignores_local_openai_base_url(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:60992")

    with patch("app.wg_agent.brain.OpenAI") as client:
        brain._client()

    client.assert_called_once_with(api_key="test-key")


def test_client_keeps_non_local_openai_base_url(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    with patch("app.wg_agent.brain.OpenAI") as client:
        brain._client()

    client.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
    )
