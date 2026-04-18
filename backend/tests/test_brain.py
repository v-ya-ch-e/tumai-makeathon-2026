"""Prompt-builder tests for `brain._listing_summary` (no LLM calls).

The commute block must only appear when `travel_times` is truthy, and the
non-commute output must remain byte-for-byte identical to the pre-plan
format so we don't regress other prompts (draft/classify/reply).
"""

from __future__ import annotations

import pathlib
import sys

from pydantic import HttpUrl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent.brain import _listing_summary  # noqa: E402
from app.wg_agent.models import Listing, PlaceLocation  # noqa: E402


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
