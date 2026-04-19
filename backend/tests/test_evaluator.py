"""Scorecard evaluator unit tests.

Covers:
  * `hard_filter` — one row per veto path (budget, city, avoid-district,
    move-in, weight-5 structured veto) plus the all-clear case.
  * Each component function — boundary curves + `missing_data` path.
  * `compose` — weighted mean arithmetic, hard cap as minimum across
    components, clamp to [0, 1], veto short-circuit.
  * `vibe_fit` — graceful degradation on valid output, invalid JSON, and
    HTTP error.

Pure-Python: no DB, no HTTP. `vibe_fit` patches `brain.vibe_score`.
"""

from __future__ import annotations

import asyncio
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
from app.wg_agent.brain import VibeScore  # noqa: E402
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


# -----------------------------------------------------------------------------
# hard_filter
# -----------------------------------------------------------------------------


def test_hard_filter_all_clear() -> None:
    assert evaluator.hard_filter(_listing(), _profile()) is None


def test_hard_filter_far_over_budget() -> None:
    v = evaluator.hard_filter(_listing(price_eur=1400), _profile())
    assert v is not None
    assert "far over budget" in v.reason
    assert "1400" in v.reason


def test_hard_filter_slightly_over_budget_is_not_a_veto() -> None:
    assert evaluator.hard_filter(_listing(price_eur=980), _profile()) is None


def test_hard_filter_missing_price_is_not_a_veto() -> None:
    assert evaluator.hard_filter(_listing(price_eur=None), _profile()) is None


def test_hard_filter_accepts_muenchen_variants() -> None:
    """'München' (profile) vs 'Muenchen' (listing) must not veto."""
    assert (
        evaluator.hard_filter(_listing(city="Muenchen"), _profile(city="München"))
        is None
    )


def test_hard_filter_avoid_district() -> None:
    v = evaluator.hard_filter(
        _listing(district="Hasenbergl"),
        _profile(avoid_districts=["Hasenbergl"]),
    )
    assert v is not None
    assert "avoid list" in v.reason


def test_hard_filter_move_in_too_late() -> None:
    v = evaluator.hard_filter(
        _listing(available_from=date(2026, 9, 1)),
        _profile(move_in_until=date(2026, 6, 1)),
    )
    assert v is not None
    assert "available too late" in v.reason


def test_hard_filter_weight5_structured_veto() -> None:
    v = evaluator.hard_filter(
        _listing(furnished=False),
        _profile(preferences=[PreferenceWeight(key="furnished", weight=5)]),
    )
    assert v is not None
    assert "furnished" in v.reason


def test_hard_filter_weight5_unknown_structured_is_not_veto() -> None:
    """Unknown (None) is not a veto; only explicit False trips the filter."""
    assert (
        evaluator.hard_filter(
            _listing(furnished=None),
            _profile(preferences=[PreferenceWeight(key="furnished", weight=5)]),
        )
        is None
    )


def test_hard_filter_weight5_soft_pref_is_not_veto() -> None:
    """Soft tags (no structured field) never veto, even at weight 5;
    the component score applies a 0.4 cap instead."""
    assert (
        evaluator.hard_filter(
            _listing(description="Bright room, close to the park"),
            _profile(preferences=[PreferenceWeight(key="gym", weight=5)]),
        )
        is None
    )


# -----------------------------------------------------------------------------
# price_fit
# -----------------------------------------------------------------------------


def test_price_fit_missing_price_is_missing_data() -> None:
    c = evaluator.price_fit(_listing(price_eur=None), _profile())
    assert c.missing_data is True
    assert c.key == "price"


def test_price_fit_under_budget_stays_high() -> None:
    c = evaluator.price_fit(_listing(price_eur=700), _profile())
    assert 0.8 < c.score <= 1.0


def test_price_fit_at_budget_is_still_okay() -> None:
    c = evaluator.price_fit(_listing(price_eur=900), _profile())
    assert 0.75 <= c.score <= 0.8


def test_price_fit_over_budget_drops_fast() -> None:
    c = evaluator.price_fit(_listing(price_eur=1000), _profile())
    assert 0.0 < c.score < 0.7


def test_price_fit_penalty_accelerates_after_budget() -> None:
    profile = _profile()
    c_at_budget = evaluator.price_fit(_listing(price_eur=900), profile)
    c_just_over = evaluator.price_fit(_listing(price_eur=950), profile)
    c_further_over = evaluator.price_fit(_listing(price_eur=1050), profile)
    assert c_just_over.score < c_at_budget.score
    assert c_further_over.score < c_just_over.score
    assert (c_at_budget.score - c_just_over.score) < (
        c_just_over.score - c_further_over.score
    )


# -----------------------------------------------------------------------------
# size_fit
# -----------------------------------------------------------------------------


def test_size_fit_grows_with_size() -> None:
    c_small = evaluator.size_fit(_listing(size_m2=18.0), _profile())
    c_big = evaluator.size_fit(_listing(size_m2=28.0), _profile())
    assert c_small.score < c_big.score <= 1.0


def test_size_fit_below_min() -> None:
    c = evaluator.size_fit(_listing(size_m2=6.0), _profile(min_size_m2=10))
    assert c.score == 0.0


def test_size_fit_at_or_above_preferred_size_is_full_score() -> None:
    c_mid = evaluator.size_fit(_listing(size_m2=33.0), _profile())
    c_far = evaluator.size_fit(_listing(size_m2=40.0), _profile())
    assert c_mid.score == 1.0
    assert c_far.score == 1.0


def test_size_fit_missing_data() -> None:
    c = evaluator.size_fit(_listing(size_m2=None), _profile())
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# wg_size_fit
# -----------------------------------------------------------------------------


def test_wg_size_fit_inside_band_is_1() -> None:
    c = evaluator.wg_size_fit(_listing(wg_size=3), _profile())
    assert c.score == 1.0


def test_wg_size_fit_one_off_is_half() -> None:
    c = evaluator.wg_size_fit(_listing(wg_size=6), _profile(max_wg_size=5))
    assert c.score == 0.5


def test_wg_size_fit_flat_mode_is_missing_data() -> None:
    c = evaluator.wg_size_fit(_listing(wg_size=3), _profile(mode="flat"))
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# availability_fit
# -----------------------------------------------------------------------------


def test_availability_fit_inside_window() -> None:
    c = evaluator.availability_fit(
        _listing(available_from=date(2026, 5, 15)),
        _profile(
            move_in_from=date(2026, 5, 1),
            move_in_until=date(2026, 6, 1),
        ),
    )
    assert c.score == 1.0


def test_availability_fit_after_window_ramps() -> None:
    c = evaluator.availability_fit(
        _listing(available_from=date(2026, 6, 8)),
        _profile(
            move_in_from=date(2026, 5, 1),
            move_in_until=date(2026, 6, 1),
        ),
    )
    assert 0.0 < c.score < 1.0


def test_availability_fit_two_weeks_past_is_zero() -> None:
    c = evaluator.availability_fit(
        _listing(available_from=date(2026, 6, 15)),
        _profile(
            move_in_from=date(2026, 5, 1),
            move_in_until=date(2026, 6, 1),
        ),
    )
    assert c.score == 0.0


def test_availability_fit_no_window_is_missing_data() -> None:
    c = evaluator.availability_fit(
        _listing(available_from=date(2026, 6, 1)),
        _profile(),  # no move_in_from / move_in_until
    )
    assert c.missing_data is True


# -----------------------------------------------------------------------------
# commute_fit
# -----------------------------------------------------------------------------


def test_commute_fit_missing_data_without_travel_times() -> None:
    tum = PlaceLocation(label="TUM", place_id="p1", lat=48.15, lng=11.57)
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), {})
    assert c.missing_data is True


def test_commute_fit_inside_budget_is_high() -> None:
    tum = PlaceLocation(
        label="TUM", place_id="p1", lat=48.15, lng=11.57, max_commute_minutes=40
    )
    # 15 min = 0.375 * 40, well under half-budget -> 1.0
    tt = {("p1", "TRANSIT"): 900}
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), tt)
    assert c.score == 1.0
    assert c.hard_cap is None


def test_commute_fit_at_budget_is_still_okay() -> None:
    tum = PlaceLocation(
        label="TUM", place_id="p1", lat=48.15, lng=11.57, max_commute_minutes=40
    )
    tt = {("p1", "TRANSIT"): 40 * 60}
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), tt)
    assert 0.75 <= c.score <= 0.8


def test_commute_fit_way_over_budget_sets_hard_cap() -> None:
    tum = PlaceLocation(
        label="TUM", place_id="p1", lat=48.15, lng=11.57, max_commute_minutes=30
    )
    # 80 min = 2.67 * 30, well past 1.5 * budget -> hard cap
    tt = {("p1", "TRANSIT"): 80 * 60}
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), tt)
    assert c.score == 0.0
    assert c.hard_cap == 0.3


def test_commute_fit_penalty_accelerates_after_budget() -> None:
    tum = PlaceLocation(
        label="TUM", place_id="p1", lat=48.15, lng=11.57, max_commute_minutes=40
    )
    at_budget = evaluator.commute_fit(
        _listing(), _profile(main_locations=[tum]), {("p1", "TRANSIT"): 40 * 60}
    )
    just_over = evaluator.commute_fit(
        _listing(), _profile(main_locations=[tum]), {("p1", "TRANSIT"): 45 * 60}
    )
    further_over = evaluator.commute_fit(
        _listing(), _profile(main_locations=[tum]), {("p1", "TRANSIT"): 55 * 60}
    )
    assert just_over.score < at_budget.score
    assert further_over.score < just_over.score
    assert (at_budget.score - just_over.score) < (
        just_over.score - further_over.score
    )


def test_commute_fit_picks_fastest_mode() -> None:
    tum = PlaceLocation(
        label="TUM", place_id="p1", lat=48.15, lng=11.57, max_commute_minutes=30
    )
    # TRANSIT is 45 min, BICYCLE is 15 min -> fastest wins -> 1.0
    tt = {("p1", "TRANSIT"): 2700, ("p1", "BICYCLE"): 900}
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), tt)
    assert c.score == 1.0


def test_commute_fit_averages_across_locations() -> None:
    tum = PlaceLocation(
        label="TUM", place_id="p1", lat=48.15, lng=11.57, max_commute_minutes=40
    )
    sendling = PlaceLocation(
        label="Sendling", place_id="p2", lat=48.12, lng=11.55, max_commute_minutes=40
    )
    # One is 1.0 (10 min), one is ~0.8 (40 min) -> average ~0.9
    tt = {("p1", "TRANSIT"): 600, ("p2", "TRANSIT"): 2400}
    c = evaluator.commute_fit(
        _listing(), _profile(main_locations=[tum, sendling]), tt
    )
    assert 0.89 <= c.score <= 0.91


def test_commute_fit_uses_default_budget_when_none() -> None:
    tum = PlaceLocation(label="TUM", place_id="p1", lat=48.15, lng=11.57)
    # Default budget 40 min; 20 min = half -> 1.0
    tt = {("p1", "TRANSIT"): 1200}
    c = evaluator.commute_fit(_listing(), _profile(main_locations=[tum]), tt)
    assert c.score == 1.0


# -----------------------------------------------------------------------------
# preference_fit
# -----------------------------------------------------------------------------


def test_preference_fit_no_prefs_is_missing_data() -> None:
    c = evaluator.preference_fit(_listing(), _profile(preferences=[]))
    assert c.missing_data is True


def test_preference_fit_structured_field_present() -> None:
    c = evaluator.preference_fit(
        _listing(furnished=True),
        _profile(preferences=[PreferenceWeight(key="furnished", weight=3)]),
    )
    assert c.score == 1.0


def test_preference_fit_soft_tag_keyword_hit() -> None:
    c = evaluator.preference_fit(
        _listing(description="Sonniges Zimmer mit Balkon und Blick."),
        _profile(preferences=[PreferenceWeight(key="balcony", weight=3)]),
    )
    assert c.score == 1.0


def test_preference_fit_weight5_soft_tag_missing_sets_cap() -> None:
    """Weight-5 soft preference that's clearly absent caps to 0.4."""
    c = evaluator.preference_fit(
        _listing(description="Kleines, ruhiges Zimmer ohne Extras."),
        _profile(preferences=[PreferenceWeight(key="gym", weight=5)]),
    )
    assert c.hard_cap == 0.4
    assert c.score < 0.5


def test_preference_fit_unknown_description_gives_half_credit() -> None:
    """No description text: can't tell -> neutral half credit (not a veto)."""
    c = evaluator.preference_fit(
        _listing(description=None),
        _profile(preferences=[PreferenceWeight(key="gym", weight=3)]),
    )
    assert c.score == 0.5
    assert c.hard_cap is None


def test_preference_fit_weighted_sum() -> None:
    """gym missing (weight 2), park present (weight 4) ->
    (0*2 + 1*4) / 6 = 0.666…"""
    c = evaluator.preference_fit(
        _listing(description="Ruhiger Park direkt vor der Tür."),
        _profile(
            preferences=[
                PreferenceWeight(key="gym", weight=2),
                PreferenceWeight(key="park", weight=4),
            ]
        ),
    )
    assert abs(c.score - (4 / 6)) < 0.01


def test_preference_fit_place_pref_uses_nearby_distance() -> None:
    c = evaluator.preference_fit(
        _listing(description="Kleines Zimmer ohne weitere Lageinfos."),
        _profile(preferences=[PreferenceWeight(key="gym", weight=4)]),
        nearby_places={
            "gym": NearbyPlace(
                key="gym",
                label="Gym",
                searched=True,
                distance_m=320,
                place_name="Fit Star",
            )
        },
    )
    assert c.score == 1.0
    assert any("320 m" in e for e in c.evidence)


def test_preference_fit_weight5_nearby_missing_sets_cap() -> None:
    c = evaluator.preference_fit(
        _listing(description="Helles Zimmer."),
        _profile(preferences=[PreferenceWeight(key="supermarket", weight=5)]),
        nearby_places={
            "supermarket": NearbyPlace(
                key="supermarket",
                label="Supermarket",
                searched=True,
                distance_m=None,
            )
        },
    )
    assert c.hard_cap == 0.4
    assert c.score == 0.0


# -----------------------------------------------------------------------------
# vibe_fit
# -----------------------------------------------------------------------------


def _vibe_listing() -> Listing:
    return _listing(description="Quiet flat near the park, lots of light.")


def _vibe_profile() -> SearchProfile:
    return _profile(notes="prefer quiet neighborhoods with parks")


def test_vibe_fit_happy_path() -> None:
    async def _run() -> ComponentScore:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score",
            return_value=VibeScore(score=0.7, evidence=["likes quiet"]),
        ):
            return await evaluator.vibe_fit(_vibe_listing(), _vibe_profile())

    c = asyncio.run(_run())
    assert c.score == 0.7
    assert c.evidence == ["likes quiet"]
    assert c.missing_data is False


def test_vibe_fit_short_circuits_when_no_signal() -> None:
    """Profile + listing with no vibe inputs must not hit the LLM at all."""

    async def _run() -> ComponentScore:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score",
            side_effect=AssertionError("LLM must not be called"),
        ):
            return await evaluator.vibe_fit(_listing(), _profile())

    c = asyncio.run(_run())
    assert c.score == 0.5
    assert c.missing_data is True
    assert c.evidence == ["not enough vibe information"]


def test_vibe_fit_invalid_json_degrades() -> None:
    async def _run() -> ComponentScore:
        def boom(*_a, **_kw):
            raise ValidationError.from_exception_data(
                "VibeScore", [{"type": "missing", "loc": ("score",), "input": {}}]
            )

        with patch(
            "app.wg_agent.evaluator.brain.vibe_score", side_effect=boom
        ):
            return await evaluator.vibe_fit(_vibe_listing(), _vibe_profile())

    c = asyncio.run(_run())
    assert c.missing_data is True
    assert c.score == 0.0


def test_vibe_fit_http_error_degrades() -> None:
    async def _run() -> ComponentScore:
        def boom(*_a, **_kw):
            raise RuntimeError("openai down")

        with patch(
            "app.wg_agent.evaluator.brain.vibe_score", side_effect=boom
        ):
            return await evaluator.vibe_fit(_vibe_listing(), _vibe_profile())

    c = asyncio.run(_run())
    assert c.missing_data is True
    assert any("LLM error" in e for e in c.evidence)


def test_vibe_fit_rate_limit_surfaces_distinct_message() -> None:
    from openai import RateLimitError
    import httpx

    async def _run() -> ComponentScore:
        def boom(*_a, **_kw):
            raise RateLimitError(
                message="rate limit",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
                ),
                body=None,
            )

        with patch("app.wg_agent.evaluator.brain.vibe_score", side_effect=boom):
            return await evaluator.vibe_fit(_vibe_listing(), _vibe_profile())

    c = asyncio.run(_run())
    assert c.missing_data is True
    assert any("rate limit" in e for e in c.evidence)


# -----------------------------------------------------------------------------
# compose
# -----------------------------------------------------------------------------


def _cs(key: str, score: float, **kw) -> ComponentScore:
    return ComponentScore(
        key=key,
        score=score,
        weight=kw.pop("weight", 1.0),
        evidence=kw.pop("evidence", [f"{key} evidence"]),
        **kw,
    )


def test_compose_weighted_mean() -> None:
    components = [
        _cs("a", 1.0, weight=2.0),
        _cs("b", 0.0, weight=2.0),
    ]
    result = evaluator.compose(components)
    assert abs(result.score - 0.5) < 1e-9


def test_compose_skips_missing_data() -> None:
    components = [
        _cs("a", 1.0, weight=1.0),
        _cs("b", 0.0, weight=1.0, missing_data=True),
    ]
    result = evaluator.compose(components)
    assert result.score == 1.0


def test_compose_applies_minimum_hard_cap() -> None:
    """Two hard caps present: the final score is pinned by the smaller one."""
    components = [
        _cs("a", 1.0, weight=1.0, hard_cap=0.6),
        _cs("b", 1.0, weight=1.0, hard_cap=0.4),
    ]
    result = evaluator.compose(components)
    assert result.score == 0.4


def test_compose_clamps_to_unit_interval() -> None:
    """Even if a hard cap were set weirdly, clamp still applies."""
    components = [_cs("a", 1.0, weight=1.0, hard_cap=0.0)]
    result = evaluator.compose(components)
    assert result.score == 0.0


def test_compose_veto_short_circuits() -> None:
    result = evaluator.compose([], veto=evaluator.VetoResult(reason="over budget"))
    assert result.score == 0.0
    assert result.veto_reason == "over budget"
    assert "Rejected" in result.summary
    assert "over budget" in result.mismatch_reasons


def test_compose_all_missing_data_is_no_data_score() -> None:
    result = evaluator.compose(
        [_cs("a", 0.5, weight=1.0, missing_data=True)]
    )
    assert result.score == 0.0
    assert "no data" in result.summary.lower() or "no evaluable" in " ".join(
        result.mismatch_reasons
    )


def test_compose_derives_match_reasons_from_strong_components() -> None:
    components = [
        _cs("price", 0.9, weight=1.0, evidence=["€700 within band"]),
        _cs("size", 0.2, weight=1.0, evidence=["small room"]),
        _cs("commute", 0.5, weight=1.0, evidence=["20 min"]),
    ]
    result = evaluator.compose(components)
    assert "€700 within band" in result.match_reasons
    assert "small room" in result.mismatch_reasons
    # 0.5 component neither matches nor mismatches.
    assert "20 min" not in result.match_reasons
    assert "20 min" not in result.mismatch_reasons


# -----------------------------------------------------------------------------
# evaluate (end-to-end facade)
# -----------------------------------------------------------------------------


def test_evaluate_veto_short_circuits_before_llm() -> None:
    """If hard_filter vetoes, `brain.vibe_score` must not be called."""

    async def _run() -> evaluator.EvaluationResult:
        with patch("app.wg_agent.evaluator.brain.vibe_score") as vs:
            res = await evaluator.evaluate(
                _listing(price_eur=2000),
                _profile(max_rent_eur=900),
            )
            assert vs.call_count == 0
            return res

    result = asyncio.run(_run())
    assert result.score == 0.0
    assert result.veto_reason is not None
    assert "over budget" in result.veto_reason


def test_evaluate_runs_all_components_on_happy_path() -> None:
    async def _run() -> evaluator.EvaluationResult:
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score",
            return_value=VibeScore(score=0.6, evidence=["match"]),
        ):
            return await evaluator.evaluate(
                _listing(price_eur=700, size_m2=18.0, wg_size=3),
                _profile(),
                travel_times={},  # commute -> missing_data, no crash
            )

    result = asyncio.run(_run())
    keys = {c.key for c in result.components}
    assert keys == {
        "price",
        "size",
        "wg_size",
        "availability",
        "commute",
        "preferences",
        "vibe",
    }
    assert result.veto_reason is None
    assert 0.0 <= result.score <= 1.0
