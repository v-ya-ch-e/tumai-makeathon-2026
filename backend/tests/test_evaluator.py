"""Matcher v2 evaluator unit tests.

Boundary asserts for every curve (`price_fit`, `size_fit`,
`commute_fit`, `availability_fit`, `wg_size_fit`, `tenancy_fit`,
`preference_fit`, `vibe_fit`, `quality_fit`, `upfront_cost_fit`) plus
the v2-specific behaviours: named multipliers, threshold matrix,
quality-excluded-from-`live`, OR availability missing-data,
`0.7·min + 0.3·mean` commute aggregator, weight-5 unknown cap,
all-unknown prefs → missing_data, cap-source in `score_reason`.

Pure-Python: no DB, no HTTP. `vibe_fit` patches `brain.vibe_score`.
"""

from __future__ import annotations

import asyncio
import math
import os
import pathlib
import sys
from datetime import date
from unittest.mock import patch

from cryptography.fernet import Fernet
from pydantic import HttpUrl, ValidationError

os.environ.setdefault("WG_SECRET_KEY", Fernet.generate_key().decode())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import evaluator  # noqa: E402
from app.wg_agent.brain import VibeJudgement  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    ComponentScore,
    Listing,
    NearbyPlace,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
)


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------


def _listing(**overrides) -> Listing:
    base = dict(
        id="lst",
        url=HttpUrl("https://www.wg-gesucht.de/lst.html"),
        title="Room",
        city="München",
        kind="wg",
    )
    base.update(overrides)
    return Listing(**base)


def _profile(**overrides) -> SearchProfile:
    base: dict = dict(
        city="München",
        max_rent_eur=900,
        min_rent_eur=400,
        min_size_m2=10,
        max_size_m2=30,
        min_wg_size=2,
        max_wg_size=5,
    )
    base.update(overrides)
    return SearchProfile(**base)


def _tum_anchor(**overrides) -> PlaceLocation:
    base = dict(
        label="TUM",
        place_id="ChIJ_TUM",
        lat=48.149,
        lng=11.568,
        max_commute_minutes=35,
    )
    base.update(overrides)
    return PlaceLocation(**base)


# -----------------------------------------------------------------------------
# §4 hard_filter — every veto path + the scored-anchor commute rule.
# -----------------------------------------------------------------------------


def test_hard_filter_all_clear() -> None:
    assert evaluator.hard_filter(_listing(), _profile()) is None


def test_hard_filter_far_over_budget_uses_named_multiplier() -> None:
    # PRICE_VETO_MULT = 1.5 against a 900€ cap → 1351€ vetoes.
    v = evaluator.hard_filter(_listing(price_eur=1351), _profile())
    assert v is not None and "far over budget" in v.reason


def test_hard_filter_one_euro_below_threshold_is_not_veto() -> None:
    # 900 * 1.5 = 1350 → 1350 stays.
    assert evaluator.hard_filter(_listing(price_eur=1350), _profile()) is None


def test_hard_filter_avoid_district_normalises_umlauts_and_dashes() -> None:
    v = evaluator.hard_filter(
        _listing(district="Schwabing-West"),
        _profile(avoid_districts=["schwabing west"]),
    )
    assert v is not None and "avoid list" in v.reason


def test_hard_filter_move_in_too_late() -> None:
    v = evaluator.hard_filter(
        _listing(available_from=date(2026, 9, 1)),
        _profile(move_in_until=date(2026, 6, 1)),
    )
    assert v is not None and "available too late" in v.reason


def test_hard_filter_must_have_structured_pref_explicit_false() -> None:
    v = evaluator.hard_filter(
        _listing(furnished=False),
        _profile(preferences=[PreferenceWeight(key="furnished", weight=5)]),
    )
    assert v is not None and "must-have 'furnished' missing" in v.reason


def test_hard_filter_must_have_structured_pref_unknown_does_NOT_veto() -> None:
    """v1's bug: unknown weight-5 should not veto (cap fires in §5.7)."""
    assert (
        evaluator.hard_filter(
            _listing(furnished=None),
            _profile(preferences=[PreferenceWeight(key="furnished", weight=5)]),
        )
        is None
    )


def test_hard_filter_inverted_structured_non_smoking_vetoes_smoking_wg() -> None:
    """v1 bug fix: `non_smoking=5` against `smoking_ok=True` must veto.

    The wizard tile is `non_smoking`; v2's resolver inverts onto
    `Listing.smoking_ok`. v1 silently ignored this because the keys
    didn't match.
    """
    v = evaluator.hard_filter(
        _listing(smoking_ok=True),
        _profile(preferences=[PreferenceWeight(key="non_smoking", weight=5)]),
    )
    assert v is not None and "must-have 'non_smoking' missing" in v.reason


def test_hard_filter_commute_veto_requires_at_least_one_scored_anchor() -> None:
    """Empty matrix data must not vacuously veto (v1 bug)."""
    assert (
        evaluator.hard_filter(
            _listing(),
            _profile(main_locations=[_tum_anchor()]),
            travel_times={},
        )
        is None
    )


def test_hard_filter_commute_veto_fires_when_every_scored_anchor_far() -> None:
    # 80 min > 2 × 35 = 70 min budget → veto.
    v = evaluator.hard_filter(
        _listing(),
        _profile(main_locations=[_tum_anchor()]),
        travel_times={(("ChIJ_TUM", "TRANSIT")): 80 * 60},
    )
    assert v is not None and "no anchor reachable" in v.reason


def test_hard_filter_commute_veto_does_not_fire_when_one_anchor_is_fine() -> None:
    second = _tum_anchor(label="Job", place_id="ChIJ_JOB", max_commute_minutes=25)
    v = evaluator.hard_filter(
        _listing(),
        _profile(main_locations=[_tum_anchor(), second]),
        travel_times={
            ("ChIJ_TUM", "TRANSIT"): 80 * 60,
            ("ChIJ_JOB", "TRANSIT"): 20 * 60,
        },
    )
    assert v is None


# -----------------------------------------------------------------------------
# §5.1 price_fit — boundaries + market percentile + Kalt uplift
# -----------------------------------------------------------------------------


def test_price_fit_at_half_budget_is_perfect() -> None:
    c = evaluator.price_fit(_listing(price_eur=450), _profile(max_rent_eur=900))
    assert c.score == 1.0


def test_price_fit_at_budget_is_zero_point_six() -> None:
    c = evaluator.price_fit(_listing(price_eur=900), _profile(max_rent_eur=900))
    assert math.isclose(c.score, 0.6, abs_tol=1e-6)


def test_price_fit_twenty_percent_over_is_zero() -> None:
    c = evaluator.price_fit(_listing(price_eur=1080), _profile(max_rent_eur=900))
    assert math.isclose(c.score, 0.0, abs_tol=1e-6)


def test_price_fit_kalt_uplift_emits_evidence() -> None:
    c = evaluator.price_fit(
        _listing(price_eur=900, price_basis="kalt_uplift"),
        _profile(max_rent_eur=1000),
    )
    assert any("Kaltmiete" in e for e in c.evidence)


def test_price_fit_suspiciously_cheap_emits_evidence() -> None:
    # below 0.7 × min_rent_eur (400) → 280
    c = evaluator.price_fit(
        _listing(price_eur=270),
        _profile(max_rent_eur=900, min_rent_eur=400),
    )
    assert any("suspiciously cheap" in e for e in c.evidence)


def test_price_fit_missing_price_is_missing_data() -> None:
    c = evaluator.price_fit(_listing(price_eur=None), _profile())
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# §5.2 size_fit — monotone, continuous at lo
# -----------------------------------------------------------------------------


def test_size_fit_at_lo_is_zero_point_six() -> None:
    c = evaluator.size_fit(_listing(size_m2=10.0), _profile(min_size_m2=10, max_size_m2=30))
    assert math.isclose(c.score, 0.6, abs_tol=1e-6)


def test_size_fit_at_hi_is_perfect() -> None:
    c = evaluator.size_fit(_listing(size_m2=30.0), _profile(min_size_m2=10, max_size_m2=30))
    assert c.score == 1.0


def test_size_fit_below_lo_is_monotone_no_overshoot() -> None:
    """v1 had `size = lo - 1` scoring higher than `size = lo`."""
    profile = _profile(min_size_m2=12, max_size_m2=30)
    at_lo = evaluator.size_fit(_listing(size_m2=12.0), profile).score
    just_below = evaluator.size_fit(_listing(size_m2=11.0), profile).score
    assert just_below <= at_lo


def test_size_fit_zero_is_zero() -> None:
    c = evaluator.size_fit(_listing(size_m2=0.0), _profile(min_size_m2=12))
    assert c.score == 0.0


def test_size_fit_lo_equals_hi_does_not_blow_up() -> None:
    """Defensive: `min_size_m2 == max_size_m2` falls back to a 1 m² band."""
    c = evaluator.size_fit(
        _listing(size_m2=10.0),
        _profile(min_size_m2=10, max_size_m2=10),
    )
    assert 0.0 <= c.score <= 1.0


# -----------------------------------------------------------------------------
# §5.3 commute_fit — sub-score curve + 0.7·min + 0.3·mean aggregator
# -----------------------------------------------------------------------------


def test_commute_at_budget_is_zero_point_six() -> None:
    tum = _tum_anchor(max_commute_minutes=35)
    tt = {("ChIJ_TUM", "TRANSIT"): 35 * 60}
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), tt)
    assert math.isclose(c.score, 0.6, abs_tol=1e-6)


def test_commute_aggregator_min_dominates_one_bad_anchor() -> None:
    """v1 used arithmetic mean; v2 uses 0.7·min + 0.3·mean.

    With anchors at 0 min and 53 min on a 35-min budget:
      sub_perfect = 1.0
      sub_bad = max(0, 0.6 - 1.2 * (53/35 - 1)) ≈ 0.0 (right at 1.5×)
    Aggregator: 0.7 × 0 + 0.3 × 0.5 = 0.15 — much harsher than the
    arithmetic mean (~0.5).
    """
    tum = _tum_anchor(label="TUM", place_id="ChIJ_TUM", max_commute_minutes=35)
    job = _tum_anchor(label="Job", place_id="ChIJ_JOB", max_commute_minutes=35)
    tt = {
        ("ChIJ_TUM", "TRANSIT"): 0,
        ("ChIJ_JOB", "TRANSIT"): 53 * 60,  # right at 1.5× budget
    }
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum, job]), tt)
    assert c.score < 0.5


def test_commute_hard_cap_fires_at_one_point_five_times_budget() -> None:
    tum = _tum_anchor(max_commute_minutes=35)
    tt = {("ChIJ_TUM", "TRANSIT"): 60 * 60}  # 60 min > 1.5 × 35
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), tt)
    assert c.hard_cap == evaluator.COMMUTE_HARD_CAP


def test_commute_partial_data_still_scored_with_evidence() -> None:
    tum = _tum_anchor(label="TUM", place_id="ChIJ_TUM")
    job = _tum_anchor(label="Job", place_id="ChIJ_JOB")
    tt = {("ChIJ_TUM", "TRANSIT"): 18 * 60}  # only TUM has data
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum, job]), tt)
    assert c.missing_data is False
    assert any("1 of 2 anchors" in e for e in c.evidence)


def test_commute_no_anchors_is_missing_data() -> None:
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[]), {})
    assert c.missing_data is True


def test_commute_no_routing_data_is_missing_data() -> None:
    tum = _tum_anchor()
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), {})
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# §5.4 availability_fit — OR'd missing-data condition
# -----------------------------------------------------------------------------


def test_availability_inside_window_is_perfect() -> None:
    c = evaluator.availability_fit(
        _listing(available_from=date(2026, 9, 15)),
        _profile(move_in_from=date(2026, 9, 1), move_in_until=date(2026, 9, 30)),
    )
    assert c.score == 1.0


def test_availability_seven_days_outside_window_is_eight_tenths() -> None:
    c = evaluator.availability_fit(
        _listing(available_from=date(2026, 10, 7)),
        _profile(move_in_from=date(2026, 9, 1), move_in_until=date(2026, 9, 30)),
    )
    assert c.score == 0.8


def test_availability_missing_listing_date_is_missing() -> None:
    c = evaluator.availability_fit(
        _listing(available_from=None),
        _profile(move_in_from=date(2026, 9, 1)),
    )
    assert c.missing_data is True


def test_availability_missing_user_window_is_missing_too() -> None:
    """v1 bug: pseudocode and prose disagreed. v2: missing if either."""
    c = evaluator.availability_fit(
        _listing(available_from=date(2026, 9, 1)),
        _profile(move_in_from=None, move_in_until=None),
    )
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# §5.5 wg_size_fit
# -----------------------------------------------------------------------------


def test_wg_size_inside_band_is_perfect() -> None:
    c = evaluator.wg_size_fit(
        _listing(wg_size=3),
        _profile(min_wg_size=2, max_wg_size=5),
    )
    assert c.score == 1.0


def test_wg_size_off_by_one_is_zero_point_six() -> None:
    c = evaluator.wg_size_fit(
        _listing(wg_size=6),
        _profile(min_wg_size=2, max_wg_size=5),
    )
    assert c.score == 0.6


def test_wg_size_lone_roommate_is_floored_when_min_is_two() -> None:
    c = evaluator.wg_size_fit(
        _listing(wg_size=1),
        _profile(min_wg_size=2, max_wg_size=5),
    )
    assert c.score == 0.0


def test_wg_size_flat_search_is_missing_data() -> None:
    c = evaluator.wg_size_fit(
        _listing(wg_size=2),
        _profile(mode="flat"),
    )
    assert c.missing_data is True


def test_wg_size_flat_listing_is_missing_data() -> None:
    c = evaluator.wg_size_fit(
        _listing(kind="flat", wg_size=1),
        _profile(),
    )
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# §5.6 tenancy_fit — needs explicit evidence (no silent 1.0)
# -----------------------------------------------------------------------------


def test_tenancy_with_long_explicit_window_is_perfect() -> None:
    """13-month window so the 365-day / 30.44-day quantisation lands
    cleanly above the 12-month threshold."""
    c = evaluator.tenancy_fit(
        _listing(
            available_from=date(2026, 9, 1),
            available_to=date(2027, 10, 1),
        ),
        _profile(),
    )
    assert c.score == 1.0


def test_tenancy_open_ended_label_scores_full_via_llm() -> None:
    c = evaluator.tenancy_fit(
        _listing(available_from=date(2026, 9, 1), available_to=None),
        _profile(),
        tenancy_label="open_ended",
    )
    assert c.score == 1.0


def test_tenancy_no_end_date_no_label_is_missing_data() -> None:
    """v1 silently scored 1.0; v2 must report missing_data."""
    c = evaluator.tenancy_fit(
        _listing(available_from=date(2026, 9, 1), available_to=None),
        _profile(),
        tenancy_label="unknown",
    )
    assert c.missing_data is True


def test_tenancy_short_with_long_intent_scores_zero() -> None:
    c = evaluator.tenancy_fit(
        _listing(available_from=date(2026, 9, 1), available_to=None),
        _profile(desired_min_months=12),
        tenancy_label="short_term",
    )
    assert c.score == 0.0


# -----------------------------------------------------------------------------
# §5.7 preference_fit — four-family resolver + new caps
# -----------------------------------------------------------------------------


def test_preferences_no_prefs_is_missing_data() -> None:
    c = evaluator.preference_fit(_listing(), _profile(preferences=[]))
    assert c.missing_data is True


def test_preferences_unknown_nice_to_have_is_dropped_from_denominator() -> None:
    """One known weight-5 great + three unknown weight-2 → 1.0 (v1 → ~0.59)."""
    c = evaluator.preference_fit(
        _listing(furnished=True, description=""),
        _profile(
            preferences=[
                PreferenceWeight(key="furnished", weight=5),
                PreferenceWeight(key="balcony", weight=2),
                PreferenceWeight(key="bike_storage", weight=2),
                PreferenceWeight(key="dishwasher", weight=2),
            ]
        ),
    )
    assert c.score == 1.0


def test_preferences_weight5_known_missing_caps_at_zero_five() -> None:
    c = evaluator.preference_fit(
        _listing(furnished=False, description="Schwabing — Park nebenan"),
        _profile(
            preferences=[
                PreferenceWeight(key="furnished", weight=5),
                PreferenceWeight(key="park", weight=3),
            ],
        ),
    )
    # Note: the structured pref is False → `hard_filter` would veto, but
    # this test calls `preference_fit` directly.
    assert c.hard_cap == evaluator.PREF_HARD_CAP_WEIGHT5


def test_preferences_weight5_unknown_caps_at_zero_six() -> None:
    """Closes v1's escape route: unknown must-have now caps."""
    c = evaluator.preference_fit(
        _listing(description=""),
        _profile(
            preferences=[
                PreferenceWeight(key="lgbt_friendly", weight=5),
                PreferenceWeight(key="park", weight=3),
            ],
        ),
    )
    assert c.hard_cap == evaluator.PREF_HARD_CAP_WEIGHT5_UNK


def test_preferences_all_unknown_nice_to_have_is_missing_data() -> None:
    c = evaluator.preference_fit(
        _listing(description=""),
        _profile(
            preferences=[
                PreferenceWeight(key="balcony", weight=2),
                PreferenceWeight(key="park", weight=1),
            ],
        ),
    )
    assert c.missing_data is True


def test_preferences_inverted_non_smoking_routes_to_smoking_ok_field() -> None:
    """v1 bug fix: `non_smoking` reads `Listing.smoking_ok` and inverts."""
    c_nonsmoke = evaluator.preference_fit(
        _listing(smoking_ok=False),
        _profile(preferences=[PreferenceWeight(key="non_smoking", weight=3)]),
    )
    c_smoke = evaluator.preference_fit(
        _listing(smoking_ok=True),
        _profile(preferences=[PreferenceWeight(key="non_smoking", weight=3)]),
    )
    assert c_nonsmoke.score == 1.0 and c_smoke.score == 0.0


def test_preferences_pet_friendly_routes_to_pets_allowed_field() -> None:
    c_yes = evaluator.preference_fit(
        _listing(pets_allowed=True),
        _profile(preferences=[PreferenceWeight(key="pet_friendly", weight=3)]),
    )
    c_no = evaluator.preference_fit(
        _listing(pets_allowed=False),
        _profile(preferences=[PreferenceWeight(key="pet_friendly", weight=3)]),
    )
    assert c_yes.score == 1.0 and c_no.score == 0.0


def test_preferences_keyword_garden_word_boundary_excludes_bahnhof() -> None:
    c = evaluator.preference_fit(
        _listing(description="5 Min zum Hauptbahnhof, ruhige Lage"),
        _profile(preferences=[PreferenceWeight(key="garden", weight=3)]),
    )
    # `hof` inside `Bahnhof` must NOT match.
    assert c.score == 0.0


def test_preferences_keyword_quiet_area_negative_overrides_positive() -> None:
    c = evaluator.preference_fit(
        _listing(description="Lage ist sehr unruhig leider"),
        _profile(preferences=[PreferenceWeight(key="quiet_area", weight=3)]),
    )
    assert c.score == 0.0


def test_preferences_llm_soft_signal_score_used_when_provided() -> None:
    c = evaluator.preference_fit(
        _listing(description="LGBT-friendly WG, all welcome"),
        _profile(preferences=[PreferenceWeight(key="lgbt_friendly", weight=4)]),
        soft_signal_scores={"lgbt_friendly": 1.0},
    )
    assert c.score == 1.0


# -----------------------------------------------------------------------------
# §5.8 vibe_fit — graceful degradation
# -----------------------------------------------------------------------------


def test_vibe_fit_happy_path_returns_outcome_with_side_channels() -> None:
    judgement = VibeJudgement(
        fit_score=0.7,
        evidence=["likes quiet"],
        flatmate_vibe="3 students",
        lifestyle_match="matches profile",
        red_flags=[],
        green_flags=["LGBT-friendly mentioned"],
        soft_signal_scores={"lgbt_friendly": 0.9},
        tenancy_label="long_term",
        scam_severity=0.0,
    )

    async def _run() -> evaluator.VibeOutcome:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score",
            return_value=judgement,
        ):
            return await evaluator.vibe_fit(_listing(), _profile())

    out = asyncio.run(_run())
    assert math.isclose(out.component.score, 0.7, abs_tol=1e-6)
    assert out.tenancy_label == "long_term"
    assert out.soft_signal_scores == {"lgbt_friendly": 0.9}
    assert out.green_flags == ["LGBT-friendly mentioned"]


def test_vibe_fit_high_scam_severity_caps_component_at_zero_three() -> None:
    judgement = VibeJudgement(
        fit_score=0.9,
        evidence=["nice text"],
        scam_severity=0.85,
    )

    async def _run() -> evaluator.VibeOutcome:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score",
            return_value=judgement,
        ):
            return await evaluator.vibe_fit(_listing(), _profile())

    out = asyncio.run(_run())
    assert out.component.hard_cap == evaluator.SCAM_VIBE_HARD_CAP


def test_vibe_fit_invalid_json_degrades_to_missing_data() -> None:
    def boom(*_a, **_k):
        raise ValidationError.from_exception_data(title="VibeJudgement", line_errors=[])

    async def _run() -> evaluator.VibeOutcome:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score", side_effect=boom
        ):
            return await evaluator.vibe_fit(_listing(), _profile())

    out = asyncio.run(_run())
    assert out.component.missing_data is True


def test_vibe_fit_http_error_degrades_to_missing_data() -> None:
    def boom(*_a, **_k):
        raise RuntimeError("HTTP 500")

    async def _run() -> evaluator.VibeOutcome:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score", side_effect=boom
        ):
            return await evaluator.vibe_fit(_listing(), _profile())

    out = asyncio.run(_run())
    assert out.component.missing_data is True


# -----------------------------------------------------------------------------
# §5.9 quality_fit — never missing
# -----------------------------------------------------------------------------


def test_quality_fit_full_listing_scores_high() -> None:
    long = (
        "Helles Zimmer in Schwabing, 18 m² für 750 €, Kaution 2 Monatsmieten, "
        "verfügbar ab 1. September. Stadtteil mit guter Anbindung. "
    ) * 5
    c = evaluator.quality_fit(
        _listing(
            description=long,
            photo_urls=["a", "b", "c"],
            available_from=date(2026, 9, 1),
            available_to=date(2027, 9, 1),
        ),
    )
    assert c.score >= 0.95
    assert c.missing_data is False  # never missing


def test_quality_fit_one_photo_softer_than_v1() -> None:
    """v3 fix #5: 1 photo → 0.8 multiplier (was 0.6 in v1)."""
    long = "x" * 700 + " 750 € 18 m² Kaution verfügbar Stadtteil "
    c = evaluator.quality_fit(_listing(description=long, photo_urls=["a"]))
    # Expected: 0.45 * 1.0 + 0.25 * 0.8 + 0.15 * 0.0 + 0.15 * 1.0 = 0.80
    # (no available_from/_to/tenancy_label → availability_clarity=0)
    assert c.score >= 0.7


def test_quality_fit_zero_photos_no_description_floors_low() -> None:
    c = evaluator.quality_fit(_listing(description="", photo_urls=[]))
    assert c.score < 0.4


# -----------------------------------------------------------------------------
# §5.10 upfront_cost_fit
# -----------------------------------------------------------------------------


def test_upfront_cost_fit_typical_two_month_deposit_is_perfect() -> None:
    c = evaluator.upfront_cost_fit(_listing(deposit_months=2.0))
    assert c.score == 1.0


def test_upfront_cost_fit_high_deposit_drops_score() -> None:
    c = evaluator.upfront_cost_fit(_listing(deposit_months=5.0))
    assert c.score == 0.2


def test_upfront_cost_fit_high_buyout_drops_score() -> None:
    c = evaluator.upfront_cost_fit(
        _listing(deposit_months=2.0, furniture_buyout_eur=6000)
    )
    # 1.0 × 0.3 multiplier
    assert math.isclose(c.score, 0.3, abs_tol=1e-6)


def test_upfront_cost_fit_no_data_is_missing() -> None:
    c = evaluator.upfront_cost_fit(_listing())
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# §6 compose — quality excluded from `live`, caps stack via min, defensive cap drop
# -----------------------------------------------------------------------------


def _c(key: str, score: float, weight: float, **kw) -> ComponentScore:
    return ComponentScore(key=key, score=score, weight=weight, **kw)


def test_compose_quality_is_NOT_double_counted() -> None:
    """The biggest v2 fix: quality enters only via the post-blend.

    Two listings with identical match-score but different quality must
    differ by exactly QUALITY_BLEND_WEIGHT in the final score.
    """
    base = [
        _c("price", 1.0, 2.0),
        _c("commute", 1.0, 2.5),
    ]
    a = base + [_c("quality", 1.0, 0.0)]
    b = base + [_c("quality", 0.0, 0.0)]
    res_a = evaluator.compose(a)
    res_b = evaluator.compose(b)
    assert res_a.match_score == res_b.match_score == 1.0
    assert math.isclose(
        res_a.score - res_b.score,
        evaluator.QUALITY_BLEND_WEIGHT,
        abs_tol=1e-9,
    )


def test_compose_drops_missing_from_denominator_and_post_blend() -> None:
    components = [
        _c("price", 1.0, 2.0),
        _c("commute", 0.5, 2.5),
        _c("vibe", 0.0, 1.5, missing_data=True),
        _c("quality", 0.5, 0.0),
    ]
    expected_match = (1.0 * 2.0 + 0.5 * 2.5) / (2.0 + 2.5)
    res = evaluator.compose(components)
    assert math.isclose(res.match_score, expected_match, abs_tol=1e-9)
    assert math.isclose(
        res.score,
        0.85 * expected_match + 0.15 * 0.5,
        abs_tol=1e-9,
    )


def test_compose_caps_stack_via_min() -> None:
    components = [
        _c("commute", 1.0, 2.5, hard_cap=evaluator.COMMUTE_HARD_CAP),
        _c(
            "preferences",
            1.0,
            1.5,
            hard_cap=evaluator.PREF_HARD_CAP_WEIGHT5,
            evidence=["cap reason: missing must-have 'gym' [engine]"],
        ),
        _c("price", 1.0, 2.0),
        _c("quality", 1.0, 0.0),
    ]
    res = evaluator.compose(components)
    assert math.isclose(res.match_score, evaluator.COMMUTE_HARD_CAP, abs_tol=1e-9)


def test_compose_caps_from_missing_components_are_dropped() -> None:
    """Defensive: a missing vibe with stale cap=0.30 must NOT apply."""
    components = [
        _c("price", 1.0, 2.0),
        _c("commute", 1.0, 2.5),
        _c("vibe", 0.0, 1.5, missing_data=True, hard_cap=0.30),
        _c("quality", 1.0, 0.0),
    ]
    res = evaluator.compose(components)
    assert res.match_score == 1.0


def test_compose_veto_short_circuits_to_zero() -> None:
    res = evaluator.compose(
        [_c("price", 1.0, 2.0)],
        veto=evaluator.VetoResult(reason="far over budget"),
    )
    assert res.score == 0.0
    assert res.veto_reason == "far over budget"


def test_compose_cap_source_named_in_score_reason() -> None:
    components = [
        _c("price", 0.95, 2.0),
        _c(
            "commute",
            0.95,
            2.5,
            hard_cap=evaluator.COMMUTE_HARD_CAP,
            evidence=["cap reason: deal-breaker commute to Marienplatz [engine]"],
        ),
        _c("quality", 1.0, 0.0),
    ]
    res = evaluator.compose(components)
    assert "capped at 0.45" in res.summary
    assert "deal-breaker commute to Marienplatz" in res.summary


def test_compose_middle_band_falls_back_to_top_and_bottom_components() -> None:
    """When no live component is ≥0.7 or ≤0.3 the drawer still gets reasons."""
    components = [
        _c("price", 0.5, 2.0, evidence=["€700 in budget [listing]"]),
        _c("commute", 0.55, 2.5, evidence=["TUM: 25 min by transit [google]"]),
        _c("quality", 0.5, 0.0),
    ]
    res = evaluator.compose(components)
    assert res.match_reasons   # non-empty fallback
    assert res.mismatch_reasons


def test_compose_no_live_returns_zero() -> None:
    components = [
        _c("price", 0.9, 2.0, missing_data=True),
        _c("quality", 1.0, 0.0),
    ]
    res = evaluator.compose(components)
    assert res.score == 0.0
    assert "No data" in res.summary


# -----------------------------------------------------------------------------
# evaluate (top-level facade)
# -----------------------------------------------------------------------------


def test_evaluate_perfect_listing_with_full_data_scores_near_one() -> None:
    judgement = VibeJudgement(
        fit_score=1.0,
        evidence=["great vibe"],
        green_flags=["LGBT-friendly"],
        scam_severity=0.0,
        tenancy_label="long_term",
        soft_signal_scores={},
    )
    listing = _listing(
        price_eur=600,
        size_m2=20.0,
        wg_size=3,
        district="Schwabing",
        available_from=date(2026, 9, 15),
        available_to=date(2027, 9, 15),
        description="x" * 800 + " 600 € 20 m² Kaution verfügbar Stadtteil ",
        photo_urls=["a", "b", "c"],
        deposit_months=2.0,
    )
    profile = _profile(
        max_rent_eur=900,
        move_in_from=date(2026, 9, 1),
        move_in_until=date(2026, 9, 30),
        main_locations=[_tum_anchor()],
    )
    tt = {("ChIJ_TUM", "TRANSIT"): 18 * 60}

    async def _run() -> evaluator.EvaluationResult:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score", return_value=judgement
        ):
            return await evaluator.evaluate(
                listing, profile, travel_times=tt, nearby_places={}
            )

    res = asyncio.run(_run())
    assert res.score >= 0.85
    assert res.veto_reason is None


def test_evaluate_vetoed_listing_short_circuits_no_llm() -> None:
    listing = _listing(price_eur=2000)  # > 1.5 × 900
    profile = _profile()

    async def _run() -> evaluator.EvaluationResult:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score",
            side_effect=AssertionError("vibe must not be called"),
        ):
            return await evaluator.evaluate(listing, profile)

    res = asyncio.run(_run())
    assert res.score == 0.0
    assert "far over budget" in (res.veto_reason or "")


def test_evaluate_uses_vibe_tenancy_label_when_no_available_to() -> None:
    """The vibe LLM's tenancy_label flows into tenancy_fit for listings
    that lack `available_to` — closing v1's "scraper missed it →
    silently 1.0" hole."""
    judgement = VibeJudgement(
        fit_score=0.8,
        evidence=["nice"],
        scam_severity=0.0,
        tenancy_label="short_term",
        soft_signal_scores={},
    )
    listing = _listing(
        price_eur=600,
        size_m2=18.0,
        wg_size=3,
        available_from=date(2026, 9, 1),
        available_to=None,
        description="Zwischenmiete 4 Wochen.",
        photo_urls=["a"],
    )
    profile = _profile(desired_min_months=12)

    async def _run() -> evaluator.EvaluationResult:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score", return_value=judgement
        ):
            return await evaluator.evaluate(listing, profile)

    res = asyncio.run(_run())
    tenancy = next(c for c in res.components if c.key == "tenancy")
    assert tenancy.score == 0.0
    assert tenancy.missing_data is False
