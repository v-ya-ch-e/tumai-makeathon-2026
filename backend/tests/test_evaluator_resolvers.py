"""Preference-resolver tests (one row per family).

`evaluator.preference_fit` routes each pref through one of four
families per MATCHER.md §3:

  * structured booleans   (§3.1)  →  `Listing.{furnished, pets_allowed, smoking_ok}`
  * Google Places nearby  (§3.2)  →  `places.PLACE_DISTANCE_BANDS`
  * description regex     (§3.3)  →  `KEYWORD_PREFERENCES` with word boundaries
  * LLM soft signals      (§3.4)  →  `vibe_fit.soft_signal_scores`

These tests exercise each family in isolation so a regression on one
resolver doesn't ride on the back of the aggregator's averaging
behaviour. Pure-Python: no DB, no HTTP.
"""

from __future__ import annotations

import math
import os
import pathlib
import sys

from cryptography.fernet import Fernet
from pydantic import HttpUrl

os.environ.setdefault("WG_SECRET_KEY", Fernet.generate_key().decode())
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import evaluator, places  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    Listing,
    NearbyPlace,
    PreferenceWeight,
    SearchProfile,
)


def _listing(**overrides) -> Listing:
    base = dict(
        id="lst",
        url=HttpUrl("https://www.wg-gesucht.de/lst.html"),
        title="Room",
        kind="wg",
    )
    base.update(overrides)
    return Listing(**base)


def _profile(prefs: list[PreferenceWeight]) -> SearchProfile:
    return SearchProfile(
        city="München",
        max_rent_eur=900,
        preferences=prefs,
    )


# -----------------------------------------------------------------------------
# §3.1 structured booleans
# -----------------------------------------------------------------------------


def test_structured_furnished_true_scores_one() -> None:
    c = evaluator.preference_fit(
        _listing(furnished=True),
        _profile([PreferenceWeight(key="furnished", weight=3)]),
    )
    assert c.score == 1.0


def test_structured_furnished_false_scores_zero() -> None:
    c = evaluator.preference_fit(
        _listing(furnished=False),
        _profile([PreferenceWeight(key="furnished", weight=3)]),
    )
    assert c.score == 0.0


def test_structured_furnished_unknown_is_dropped_for_nice_to_have() -> None:
    c = evaluator.preference_fit(
        _listing(furnished=None),
        _profile([PreferenceWeight(key="furnished", weight=2)]),
    )
    # Only one pref, weight ≤ 3, unknown → dropped → all unknown → missing.
    assert c.missing_data is True


def test_structured_non_smoking_inverts_smoking_ok_field() -> None:
    """Wizard tile is `non_smoking`; resolver inverts onto `Listing.smoking_ok`.

    A v1 silent bug — the structured veto and resolver did not apply
    because the tile name didn't match a `STRUCTURED_PREFERENCES` key.
    """
    smoking = evaluator.preference_fit(
        _listing(smoking_ok=True),
        _profile([PreferenceWeight(key="non_smoking", weight=3)]),
    )
    nonsmoking = evaluator.preference_fit(
        _listing(smoking_ok=False),
        _profile([PreferenceWeight(key="non_smoking", weight=3)]),
    )
    assert smoking.score == 0.0
    assert nonsmoking.score == 1.0


def test_structured_pet_friendly_routes_to_pets_allowed_field() -> None:
    yes = evaluator.preference_fit(
        _listing(pets_allowed=True),
        _profile([PreferenceWeight(key="pet_friendly", weight=3)]),
    )
    no = evaluator.preference_fit(
        _listing(pets_allowed=False),
        _profile([PreferenceWeight(key="pet_friendly", weight=3)]),
    )
    assert yes.score == 1.0 and no.score == 0.0


# -----------------------------------------------------------------------------
# §3.2 Google Places distance bands (PLACE_DISTANCE_BANDS pinned per category)
# -----------------------------------------------------------------------------


def test_supermarket_distance_within_comfort_scores_one() -> None:
    nearby = {
        "supermarket": NearbyPlace(
            key="supermarket",
            label="Supermarket",
            searched=True,
            distance_m=300,  # ≤ 400 m comfort
            place_name="REWE",
        )
    }
    c = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="supermarket", weight=3)]),
        nearby_places=nearby,
    )
    assert c.score == 1.0


def test_supermarket_distance_at_ok_scores_point_six() -> None:
    nearby = {
        "supermarket": NearbyPlace(
            key="supermarket",
            label="Supermarket",
            searched=True,
            distance_m=900,  # ok_m for supermarket
            place_name="REWE",
        )
    }
    c = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="supermarket", weight=3)]),
        nearby_places=nearby,
    )
    assert math.isclose(c.score, 0.6, abs_tol=1e-6)


def test_supermarket_distance_at_max_scores_zero() -> None:
    nearby = {
        "supermarket": NearbyPlace(
            key="supermarket",
            label="Supermarket",
            searched=True,
            distance_m=1500,  # max_m
            place_name="REWE",
        )
    }
    c = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="supermarket", weight=3)]),
        nearby_places=nearby,
    )
    assert math.isclose(c.score, 0.0, abs_tol=1e-6)


def test_public_transport_uses_tighter_band_than_supermarket() -> None:
    """500 m → 0.6 for transit, but 0.6 for supermarket only at 900 m."""
    nearby = {
        "public_transport": NearbyPlace(
            key="public_transport",
            label="Public transport",
            searched=True,
            distance_m=500,
            place_name="U6 Studentenstadt",
        )
    }
    c = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="public_transport", weight=3)]),
        nearby_places=nearby,
    )
    assert math.isclose(c.score, 0.6, abs_tol=1e-6)


def test_park_uses_widest_band_2km_still_scores() -> None:
    nearby = {
        "park": NearbyPlace(
            key="park",
            label="Park",
            searched=True,
            distance_m=2000,  # ok_m for park
            place_name="Englischer Garten",
        )
    }
    c = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="park", weight=3)]),
        nearby_places=nearby,
    )
    assert math.isclose(c.score, 0.6, abs_tol=1e-6)


def test_places_lookup_unsearched_is_unknown() -> None:
    """API key missing / failed lookup → `searched=False` → `None` signal."""
    nearby = {
        "supermarket": NearbyPlace(
            key="supermarket",
            label="Supermarket",
            searched=False,
        )
    }
    c = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="supermarket", weight=3)]),
        nearby_places=nearby,
    )
    # Single weight-3 unknown → dropped → all unknown → missing.
    assert c.missing_data is True


def test_places_no_match_inside_radius_scores_zero_with_evidence() -> None:
    nearby = {
        "supermarket": NearbyPlace(
            key="supermarket",
            label="Supermarket",
            searched=True,
            distance_m=None,  # nothing inside max_m
        )
    }
    c = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="supermarket", weight=3)]),
        nearby_places=nearby,
    )
    assert c.score == 0.0
    assert any("none found" in e for e in c.evidence)


# -----------------------------------------------------------------------------
# §3.3 keyword regex with word boundaries
# -----------------------------------------------------------------------------


def test_balcony_matches_balkon_and_terrasse() -> None:
    c1 = evaluator.preference_fit(
        _listing(description="Süd-Balkon mit Blick"),
        _profile([PreferenceWeight(key="balcony", weight=3)]),
    )
    c2 = evaluator.preference_fit(
        _listing(description="Schöne Terrasse vorhanden"),
        _profile([PreferenceWeight(key="balcony", weight=3)]),
    )
    assert c1.score == 1.0 and c2.score == 1.0


def test_garden_does_NOT_match_bahnhof() -> None:
    """v1 bug: substring scan caught `hof` inside `Bahnhof`. v2 uses `\\bword\\b`."""
    c = evaluator.preference_fit(
        _listing(description="5 Min zum Hauptbahnhof"),
        _profile([PreferenceWeight(key="garden", weight=3)]),
    )
    assert c.score == 0.0


def test_quiet_area_negative_overrides_positive() -> None:
    """`unruhig` flips the score to 0.0 even if `ruhig` is also present."""
    c = evaluator.preference_fit(
        _listing(description="Lage ist ruhig… ähm, eigentlich sehr unruhig"),
        _profile([PreferenceWeight(key="quiet_area", weight=3)]),
    )
    assert c.score == 0.0


def test_keyword_unknown_when_description_empty() -> None:
    c = evaluator.preference_fit(
        _listing(description=""),
        _profile([PreferenceWeight(key="balcony", weight=4)]),  # ≥ 4 → imputed 0.4
    )
    assert math.isclose(c.score, 0.4, abs_tol=1e-6)


def test_keyword_score_zero_when_token_absent_in_nonempty_text() -> None:
    c = evaluator.preference_fit(
        _listing(description="Schönes Apartment im Zentrum"),
        _profile([PreferenceWeight(key="dishwasher", weight=3)]),
    )
    assert c.score == 0.0


def test_keyword_word_boundary_avoids_balkonien_joke_word() -> None:
    """Defensive regex check — `balkonien` != `balkon` (joke German for vacation)."""
    c = evaluator.preference_fit(
        _listing(description="Reise nach Balkonien geplant"),
        _profile([PreferenceWeight(key="balcony", weight=3)]),
    )
    assert c.score == 0.0


# -----------------------------------------------------------------------------
# §3.4 LLM soft signals
# -----------------------------------------------------------------------------


def test_llm_soft_signal_passthrough() -> None:
    c = evaluator.preference_fit(
        _listing(description="LGBT-friendly WG, all welcome"),
        _profile([PreferenceWeight(key="lgbt_friendly", weight=4)]),
        soft_signal_scores={"lgbt_friendly": 0.9},
    )
    # Single pref with score 0.9 → 0.9 / 4 * 4 = 0.9
    assert math.isclose(c.score, 0.9, abs_tol=1e-6)


def test_llm_soft_signal_missing_falls_back_to_unknown() -> None:
    """When vibe didn't return the key, the resolver returns None.

    For a weight-4 pref: imputed 0.4. For weight-5: imputed 0.4 plus
    cap. For weight ≤ 3: dropped from denominator.
    """
    c_weight3 = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="lgbt_friendly", weight=3)]),
        soft_signal_scores={},
    )
    assert c_weight3.missing_data is True

    c_weight4 = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="lgbt_friendly", weight=4)]),
        soft_signal_scores={},
    )
    assert math.isclose(c_weight4.score, 0.4, abs_tol=1e-6)

    c_weight5 = evaluator.preference_fit(
        _listing(),
        _profile([PreferenceWeight(key="lgbt_friendly", weight=5)]),
        soft_signal_scores={},
    )
    assert c_weight5.hard_cap == evaluator.PREF_HARD_CAP_WEIGHT5_UNK


def test_wg_gender_is_routed_to_llm_family() -> None:
    """`wg_gender` → LLM_PREFERENCES → score from `soft_signal_scores`."""
    c_match = evaluator.preference_fit(
        _listing(description="nur Frauen-WG"),
        _profile([PreferenceWeight(key="wg_gender", weight=4)]),
        soft_signal_scores={"wg_gender": 1.0},
    )
    c_exclude = evaluator.preference_fit(
        _listing(description="nur Frauen-WG"),
        _profile([PreferenceWeight(key="wg_gender", weight=4)]),
        soft_signal_scores={"wg_gender": 0.0},
    )
    assert c_match.score == 1.0 and c_exclude.score == 0.0


# -----------------------------------------------------------------------------
# Sanity: pinned `PLACE_DISTANCE_BANDS` matches what the resolver consumes
# -----------------------------------------------------------------------------


def test_place_distance_bands_are_pinned_per_category() -> None:
    """Spec §3.2 fix: `(comfort, ok, max)` triples are imported from `places`,
    so the proof tests, production code, and docs cannot drift."""
    bands = places.PLACE_DISTANCE_BANDS
    assert bands["public_transport"] == (200, 500, 800)
    assert bands["park"] == (800, 2000, 5000)
    assert bands["supermarket"] == (400, 900, 1500)
