"""End-to-end integration tests for the v2 evaluator.

Each test fabricates a `Listing` + `SearchProfile` + `travel_times` +
`nearby_places` combination, mocks `brain.vibe_score`, and asserts the
full `EvaluationResult` (score band, veto behavior, cap source,
match/mismatch reasons). These complement the unit tests in
`test_evaluator.py` (boundary curves) and `test_evaluator_resolvers.py`
(per-family routing) by exercising real listing-shape inputs the way
the matcher loop does.

Pure-Python: no DB, no HTTP. `brain.vibe_score` is mocked.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from datetime import date
from unittest.mock import patch

from cryptography.fernet import Fernet
from pydantic import HttpUrl

os.environ.setdefault("WG_SECRET_KEY", Fernet.generate_key().decode())
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import evaluator  # noqa: E402
from app.wg_agent.brain import VibeJudgement  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    Listing,
    NearbyPlace,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
)


def _vibe(
    *,
    fit_score: float = 0.7,
    scam: float = 0.0,
    tenancy: str = "long_term",
    soft: dict[str, float] | None = None,
    green: list[str] | None = None,
    red: list[str] | None = None,
) -> VibeJudgement:
    return VibeJudgement(
        fit_score=fit_score,
        evidence=["mock"],
        flatmate_vibe="3 students",
        lifestyle_match="matches profile",
        red_flags=red or [],
        green_flags=green or [],
        soft_signal_scores=soft or {},
        tenancy_label=tenancy,
        scam_severity=scam,
    )


def _run(listing: Listing, profile: SearchProfile, **kw):
    judgement = kw.pop("vibe", _vibe())
    coro = evaluator.evaluate(listing, profile, **kw)
    async def _w():
        with patch(
            "app.wg_agent.evaluator.brain.vibe_score", return_value=judgement
        ):
            return await coro
    return asyncio.run(_w())


def _listing(**overrides) -> Listing:
    base = dict(
        id="lst",
        url=HttpUrl("https://www.wg-gesucht.de/lst.html"),
        title="Room",
        kind="wg",
        city="München",
        district="Schwabing",
        lat=48.16,
        lng=11.58,
    )
    base.update(overrides)
    return Listing(**base)


_TUM = PlaceLocation(
    label="TUM", place_id="ChIJ_TUM", lat=48.149, lng=11.568, max_commute_minutes=35
)


def _profile(**overrides) -> SearchProfile:
    base: dict = dict(
        city="München",
        max_rent_eur=900,
        min_rent_eur=400,
        min_size_m2=10,
        max_size_m2=30,
        min_wg_size=2,
        max_wg_size=5,
        main_locations=[_TUM],
        move_in_from=date(2026, 9, 1),
        move_in_until=date(2026, 9, 30),
    )
    base.update(overrides)
    return SearchProfile(**base)


# -----------------------------------------------------------------------------
# 1. Perfect listing — should clear 0.85.
# -----------------------------------------------------------------------------


def test_perfect_listing_scores_high() -> None:
    listing = _listing(
        price_eur=600,
        size_m2=20.0,
        wg_size=3,
        available_from=date(2026, 9, 15),
        available_to=date(2027, 10, 15),
        description="Helles Zimmer in Schwabing, 20 m², 600 €, Kaution 2 Monatsmieten, "
        * 5,
        photo_urls=["a", "b", "c"],
        deposit_months=2.0,
        price_basis="warm",
    )
    res = _run(
        listing,
        _profile(),
        travel_times={("ChIJ_TUM", "TRANSIT"): 18 * 60},
        nearby_places={},
        vibe=_vibe(fit_score=1.0),
    )
    assert res.score >= 0.85
    assert res.veto_reason is None


# -----------------------------------------------------------------------------
# 2. Far-commute deal-breaker — capped at 0.45 even with great everything else.
# -----------------------------------------------------------------------------


def test_far_commute_dealbreaker_caps_listing() -> None:
    listing = _listing(
        price_eur=600,
        size_m2=22.0,
        wg_size=3,
        available_from=date(2026, 9, 15),
        available_to=date(2027, 9, 15),
        description="x" * 800,
        photo_urls=["a", "b", "c"],
        deposit_months=2.0,
    )
    # 60 min > 1.5 × 35 → cap fires; 60 < 2 × 35 = 70 → no veto.
    res = _run(
        listing,
        _profile(),
        travel_times={("ChIJ_TUM", "TRANSIT"): 60 * 60},
    )
    assert res.match_score <= evaluator.COMMUTE_HARD_CAP
    assert res.cap_source is not None
    assert res.cap_source.component_key == "commute"


# -----------------------------------------------------------------------------
# 3. Missing must-have structured pref — vetoed before LLM.
# -----------------------------------------------------------------------------


def test_must_have_furnished_missing_vetoes_before_llm() -> None:
    listing = _listing(
        price_eur=700, size_m2=20, wg_size=3, furnished=False
    )
    profile = _profile(
        preferences=[PreferenceWeight(key="furnished", weight=5)]
    )
    res = _run(listing, profile)
    assert res.score == 0.0
    assert "must-have" in (res.veto_reason or "")


# -----------------------------------------------------------------------------
# 4. Sublet vs long-term student intent — tenancy_fit drops it.
# -----------------------------------------------------------------------------


def test_short_sublet_with_long_intent_pulls_score_down() -> None:
    listing = _listing(
        price_eur=600,
        size_m2=18,
        wg_size=3,
        available_from=date(2026, 9, 1),
        available_to=date(2026, 10, 1),  # 1-month sublet
        description="Zwischenmiete 4 Wochen",
        photo_urls=["a"],
    )
    res = _run(
        listing,
        _profile(desired_min_months=12),
        travel_times={("ChIJ_TUM", "TRANSIT"): 18 * 60},
    )
    tenancy = next(c for c in res.components if c.key == "tenancy")
    assert tenancy.score == 0.0


# -----------------------------------------------------------------------------
# 5. Kalt uplift — evidence string surfaced.
# -----------------------------------------------------------------------------


def test_kalt_uplift_evidence_visible_in_price_component() -> None:
    listing = _listing(
        price_eur=900,
        size_m2=20,
        wg_size=3,
        price_basis="kalt_uplift",
        available_from=date(2026, 9, 15),
        photo_urls=["a"],
    )
    res = _run(listing, _profile(max_rent_eur=1000))
    price = next(c for c in res.components if c.key == "price")
    assert any("Kaltmiete" in e for e in price.evidence)


# -----------------------------------------------------------------------------
# 6. Suspiciously cheap — evidence string surfaced.
# -----------------------------------------------------------------------------


def test_suspiciously_cheap_evidence_visible() -> None:
    listing = _listing(
        price_eur=200,
        size_m2=18,
        wg_size=3,
        photo_urls=["a"],
    )
    res = _run(listing, _profile(max_rent_eur=900, min_rent_eur=400))
    price = next(c for c in res.components if c.key == "price")
    assert any("suspiciously cheap" in e for e in price.evidence)


# -----------------------------------------------------------------------------
# 7. District vetoed — score 0.0, no LLM call.
# -----------------------------------------------------------------------------


def test_avoid_district_vetoes_with_normalisation() -> None:
    listing = _listing(district="Schwabing-West", price_eur=600, size_m2=18)
    profile = _profile(avoid_districts=["schwabing west"])  # different case + no dash
    res = _run(listing, profile)
    assert res.score == 0.0
    assert "avoid list" in (res.veto_reason or "")


# -----------------------------------------------------------------------------
# 8. No photos but otherwise perfect — quality dips, match still solid.
# -----------------------------------------------------------------------------


def test_no_photos_but_perfect_fit_still_ranks_well() -> None:
    listing = _listing(
        price_eur=600,
        size_m2=20,
        wg_size=3,
        available_from=date(2026, 9, 15),
        available_to=date(2027, 10, 15),
        description="x" * 800 + " 600 € 20 m² Kaution verfügbar Stadtteil ",
        photo_urls=[],
    )
    res = _run(
        listing,
        _profile(),
        travel_times={("ChIJ_TUM", "TRANSIT"): 18 * 60},
        vibe=_vibe(fit_score=1.0),
    )
    quality = next(c for c in res.components if c.key == "quality")
    assert quality.score < 0.9   # photos dock it
    assert res.match_score >= 0.9   # but match isn't affected
    assert res.score >= 0.7


# -----------------------------------------------------------------------------
# 9. Scam-flagged listing — vibe cap pulls it low.
# -----------------------------------------------------------------------------


def test_scam_flagged_listing_caps_vibe_at_zero_three() -> None:
    listing = _listing(
        price_eur=600,
        size_m2=20,
        wg_size=3,
        available_from=date(2026, 9, 15),
        photo_urls=["a"],
    )
    res = _run(
        listing,
        _profile(),
        travel_times={("ChIJ_TUM", "TRANSIT"): 18 * 60},
        vibe=_vibe(
            fit_score=0.95,
            scam=0.85,
            red=["asks for deposit by Western Union"],
        ),
    )
    vibe = next(c for c in res.components if c.key == "vibe")
    assert vibe.hard_cap == evaluator.SCAM_VIBE_HARD_CAP
    # Scam red flag should also surface in mismatch_reasons via the
    # extra_mismatch_reasons channel.
    assert any("Western Union" in r for r in res.mismatch_reasons)


# -----------------------------------------------------------------------------
# 10. Almost-no-data listing — still produces a valid result, no crash.
# -----------------------------------------------------------------------------


def test_minimal_data_listing_still_evaluates_safely() -> None:
    """When the scraper returns mostly nulls, the engine should not crash.

    Several components go `missing_data=True`; the remaining ones (vibe,
    quality) still produce a valid 0..1 final score.
    """
    listing = _listing(
        price_eur=None,
        size_m2=None,
        wg_size=None,
        available_from=None,
        district=None,
        description=None,
        photo_urls=[],
    )
    profile = _profile(main_locations=[], move_in_from=None, move_in_until=None)
    res = _run(listing, profile)
    assert 0.0 <= res.score <= 1.0
    # Several components must be missing — at least price, size, wg_size,
    # availability, commute, tenancy, upfront_cost.
    missing = [c.key for c in res.components if c.missing_data]
    assert "price" in missing and "size" in missing and "commute" in missing


# -----------------------------------------------------------------------------
# Final-blend math sanity (cheap regression check across 4 corners)
# -----------------------------------------------------------------------------


def test_final_blend_math_matches_spec_constants() -> None:
    """`final = 0.85 · match + 0.15 · quality` per MATCHER.md §6."""
    assert evaluator.QUALITY_BLEND_WEIGHT == 0.15
    # Pure-Python sanity: feed compose synthetic components and check
    # the exact post-blend value against a hand calculation.
    from app.wg_agent.models import ComponentScore
    components = [
        ComponentScore(key="price", score=1.0, weight=2.0),
        ComponentScore(key="commute", score=1.0, weight=2.5),
        ComponentScore(key="quality", score=0.5, weight=0.0),
    ]
    res = evaluator.compose(components)
    assert res.match_score == 1.0
    expected = 0.85 * 1.0 + 0.15 * 0.5
    assert abs(res.score - expected) < 1e-9
