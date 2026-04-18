"""Scorecard evaluation engine for WG-Gesucht listings.

Replaces the single-LLM-call path (`brain.score_listing`) with a hybrid
pipeline:

  1. `hard_filter`   -> deterministic vetoes (over budget, wrong city, …)
  2. component functions (`price_fit`, `size_fit`, `commute_fit`,
     `availability_fit`, `wg_size_fit`, `preference_fit`)    -> pure code
  3. `vibe_fit`      -> one LLM call via `brain.vibe_score` (prose only)
  4. `compose`       -> weighted mean + hard caps + clamp

The point of this split: hard numeric facts (rent, move-in, commute
budgets) are judged by code that can be unit-tested, and the LLM only
decides the things it's actually good at. See ADR-015 in
`docs/DECISIONS.md`.

All component functions return `ComponentScore(score, weight, evidence,
hard_cap, missing_data)` and are pure (except `vibe_fit`, which calls
the network and catches). `evaluate` is the single facade the engine
calls; it returns an `EvaluationResult` used by the engine to mutate the
domain `Listing` and persist via `repo.save_score`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from . import brain
from .models import ComponentScore, Listing, SearchProfile

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Configuration (weights + keyword table)
# -----------------------------------------------------------------------------


DEFAULT_COMMUTE_BUDGET_MIN = 40

# Composition weights. These add up to 1.0 after filtering `missing_data`
# components out. Tuned for "numeric fit dominates, vibe is a tie-breaker".
COMPONENT_WEIGHTS: dict[str, float] = {
    "price": 2.0,
    "size": 1.0,
    "wg_size": 0.5,
    "availability": 1.0,
    "commute": 2.0,
    "preferences": 1.5,
    "vibe": 1.0,
}


# Preferences the UI offers but the listing's structured fields can't
# confirm (so we substring-scan the description). Synonyms keep the
# match resilient to German/English phrasing.
PREFERENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gym": ("gym", "fitness", "fitnessstudio"),
    "park": ("park", "grünanlage", "wiese"),
    "balcony": ("balkon", "balcony", "terrasse", "terrace"),
    "elevator": ("aufzug", "elevator", "lift"),
    "garden": ("garten", "garden"),
    "quiet_area": ("ruhig", "quiet", "ruhige"),
    "public_transport": ("u-bahn", "s-bahn", "tram", "bus", "öpnv", "mvv"),
    "dishwasher": ("spülmaschine", "geschirrspüler", "dishwasher"),
    "washing_machine": ("waschmaschine", "washing machine"),
    "internet": ("internet", "wlan", "wifi", "glasfaser"),
    "bike_storage": ("fahrrad", "bike storage", "radkeller"),
    "parking": ("parkplatz", "parking", "garage", "tiefgarage"),
}


# Structured boolean preferences (resolved against `Listing` fields
# directly, no keyword scan). Keys must match the UI tile ids.
STRUCTURED_PREFERENCES: dict[str, str] = {
    "furnished": "furnished",
    "pets_allowed": "pets_allowed",
    "smoking_ok": "smoking_ok",
}


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class VetoResult:
    reason: str


@dataclass
class EvaluationResult:
    """Output of `evaluate`.

    Consumed by `HuntEngine.run_find_only`: the engine mutates the domain
    `Listing`, then passes fields through `repo.save_score`.
    """

    score: float
    components: list[ComponentScore]
    veto_reason: Optional[str]
    summary: str
    match_reasons: list[str]
    mismatch_reasons: list[str]


# -----------------------------------------------------------------------------
# Hard filter
# -----------------------------------------------------------------------------


def hard_filter(listing: Listing, profile: SearchProfile) -> Optional[VetoResult]:
    """Return a veto if the listing can't possibly match, else None.

    Vetoes short-circuit: no components, no LLM call, score pinned at 0.0.
    """
    if listing.price_eur is not None and listing.price_eur > profile.max_rent_eur:
        return VetoResult(
            reason=f"over budget (€{listing.price_eur} > €{profile.max_rent_eur})"
        )
    if (
        listing.city
        and profile.city
        and _normalize_city(listing.city) != _normalize_city(profile.city)
    ):
        return VetoResult(
            reason=f"wrong city ({listing.city} != {profile.city})"
        )
    if (
        listing.district
        and listing.district in profile.avoid_districts
    ):
        return VetoResult(reason=f"district on avoid list ({listing.district})")
    if (
        profile.move_in_until is not None
        and listing.available_from is not None
        and listing.available_from > profile.move_in_until
    ):
        return VetoResult(
            reason=(
                f"available too late ({listing.available_from} > "
                f"{profile.move_in_until})"
            )
        )
    for pref in profile.preferences:
        if pref.weight != 5:
            continue
        attr = STRUCTURED_PREFERENCES.get(pref.key)
        if attr is None:
            continue
        listing_val = getattr(listing, attr, None)
        # Only veto on an explicit False from the listing, not None (unknown).
        if listing_val is False:
            return VetoResult(
                reason=f"must-have '{pref.key}' missing"
            )
    return None


def _normalize_city(name: str) -> str:
    lowered = name.strip().lower()
    # Handle the "München"/"Muenchen" pair (and other umlaut variants)
    # without pulling in a full transliteration library.
    return (
        lowered.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


# -----------------------------------------------------------------------------
# Component functions
# -----------------------------------------------------------------------------


def price_fit(listing: Listing, profile: SearchProfile) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["price"]
    if listing.price_eur is None:
        return ComponentScore(
            key="price",
            score=0.0,
            weight=weight,
            evidence=["price unknown"],
            missing_data=True,
        )
    p = listing.price_eur
    lo = profile.min_rent_eur
    hi = profile.max_rent_eur
    if hi <= 0:
        return ComponentScore(
            key="price",
            score=0.0,
            weight=weight,
            evidence=["no rent budget configured"],
            missing_data=True,
        )
    comfortable = max(lo, int(0.85 * hi))
    if p > hi:
        score = 0.0
        evidence = [f"€{p} over budget (cap €{hi})"]
    elif p >= lo and p <= comfortable:
        score = 1.0
        evidence = [f"€{p} within comfortable band (≤ €{comfortable})"]
    elif p < lo:
        # Suspiciously cheap but not a veto: small penalty.
        under = max(1, lo - p)
        score = max(0.0, 1.0 - under / max(lo, 1))
        evidence = [f"€{p} below min rent €{lo}"]
    else:
        # comfortable < p <= hi: linear ramp 1 -> 0.
        span = max(1, hi - comfortable)
        score = max(0.0, 1.0 - (p - comfortable) / span)
        evidence = [f"€{p} near cap €{hi}"]
    return ComponentScore(
        key="price", score=score, weight=weight, evidence=evidence
    )


def size_fit(listing: Listing, profile: SearchProfile) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["size"]
    if listing.size_m2 is None:
        return ComponentScore(
            key="size",
            score=0.0,
            weight=weight,
            evidence=["size unknown"],
            missing_data=True,
        )
    s = float(listing.size_m2)
    lo = float(profile.min_size_m2)
    hi = float(profile.max_size_m2)
    ramp_up_end = lo + 5.0
    ramp_down_end = hi * 1.25
    if s < lo:
        score = 0.0
        evidence = [f"{s:.0f} m² below min {lo:.0f} m²"]
    elif s < ramp_up_end:
        score = (s - lo) / (ramp_up_end - lo) if ramp_up_end > lo else 1.0
        evidence = [f"{s:.0f} m² just above min"]
    elif s <= hi:
        score = 1.0
        evidence = [f"{s:.0f} m² inside target band"]
    elif s <= ramp_down_end:
        span = max(1e-6, ramp_down_end - hi)
        score = max(0.0, 1.0 - (s - hi) / span)
        evidence = [f"{s:.0f} m² above target {hi:.0f} m²"]
    else:
        score = 0.0
        evidence = [f"{s:.0f} m² well above target {hi:.0f} m²"]
    return ComponentScore(
        key="size", score=score, weight=weight, evidence=evidence
    )


def wg_size_fit(listing: Listing, profile: SearchProfile) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["wg_size"]
    if profile.mode == "flat":
        return ComponentScore(
            key="wg_size",
            score=0.0,
            weight=weight,
            evidence=["looking for a flat, WG size irrelevant"],
            missing_data=True,
        )
    if listing.wg_size is None:
        return ComponentScore(
            key="wg_size",
            score=0.0,
            weight=weight,
            evidence=["WG size unknown"],
            missing_data=True,
        )
    n = listing.wg_size
    lo = profile.min_wg_size
    hi = profile.max_wg_size
    if lo <= n <= hi:
        score = 1.0
        evidence = [f"{n}-person WG inside target {lo}..{hi}"]
    elif n == lo - 1 or n == hi + 1:
        score = 0.5
        evidence = [f"{n}-person WG one off from target {lo}..{hi}"]
    else:
        score = 0.0
        evidence = [f"{n}-person WG outside target {lo}..{hi}"]
    return ComponentScore(
        key="wg_size", score=score, weight=weight, evidence=evidence
    )


def availability_fit(
    listing: Listing, profile: SearchProfile
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["availability"]
    if listing.available_from is None:
        return ComponentScore(
            key="availability",
            score=0.0,
            weight=weight,
            evidence=["no availability date"],
            missing_data=True,
        )
    if profile.move_in_from is None and profile.move_in_until is None:
        return ComponentScore(
            key="availability",
            score=0.0,
            weight=weight,
            evidence=["no move-in window configured"],
            missing_data=True,
        )
    af = listing.available_from
    mf = profile.move_in_from
    mu = profile.move_in_until
    # Inside the window (inclusive): perfect fit.
    if (mf is None or af >= mf) and (mu is None or af <= mu):
        return ComponentScore(
            key="availability",
            score=1.0,
            weight=weight,
            evidence=[f"available {af} inside move-in window"],
        )
    # Outside window: ramp down over the next 14 days either way.
    days_off = 0
    if mf is not None and af < mf:
        days_off = (mf - af).days
    elif mu is not None and af > mu:
        days_off = (af - mu).days
    score = max(0.0, 1.0 - days_off / 14.0)
    evidence = [f"available {af} ({days_off} days off window)"]
    return ComponentScore(
        key="availability", score=score, weight=weight, evidence=evidence
    )


def commute_fit(
    listing: Listing,
    profile: SearchProfile,
    travel_times: dict[tuple[str, str], int],
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["commute"]
    if not profile.main_locations:
        return ComponentScore(
            key="commute",
            score=0.0,
            weight=weight,
            evidence=["no main locations configured"],
            missing_data=True,
        )
    if not travel_times:
        return ComponentScore(
            key="commute",
            score=0.0,
            weight=weight,
            evidence=["no commute data"],
            missing_data=True,
        )
    per_location_scores: list[float] = []
    evidence: list[str] = []
    hard_cap: Optional[float] = None
    for loc in profile.main_locations:
        fastest_secs: Optional[int] = None
        fastest_mode: Optional[str] = None
        for (pid, mode), secs in travel_times.items():
            if pid != loc.place_id:
                continue
            if fastest_secs is None or secs < fastest_secs:
                fastest_secs = secs
                fastest_mode = mode
        if fastest_secs is None:
            continue
        minutes = round(fastest_secs / 60)
        budget = loc.max_commute_minutes or DEFAULT_COMMUTE_BUDGET_MIN
        sub_score = _commute_curve(minutes, budget)
        per_location_scores.append(sub_score)
        evidence.append(
            f"{loc.label}: {minutes} min ({(fastest_mode or '').lower()}) "
            f"vs budget {budget} min"
        )
        if minutes > budget * 1.5:
            hard_cap = 0.3 if hard_cap is None else min(hard_cap, 0.3)
    if not per_location_scores:
        return ComponentScore(
            key="commute",
            score=0.0,
            weight=weight,
            evidence=["no commute matches for configured locations"],
            missing_data=True,
        )
    score = sum(per_location_scores) / len(per_location_scores)
    return ComponentScore(
        key="commute",
        score=score,
        weight=weight,
        evidence=evidence,
        hard_cap=hard_cap,
    )


def _commute_curve(minutes: int, budget: int) -> float:
    """Piecewise linear: 1.0 at 0.5*budget, 0.5 at budget, 0 at 1.5*budget.

    Beyond 1.5*budget clamps to 0. Under 0.5*budget clamps to 1.0.
    """
    if budget <= 0:
        return 0.0
    half = budget * 0.5
    cap = budget * 1.5
    if minutes <= half:
        return 1.0
    if minutes <= budget:
        # 1.0 at half -> 0.5 at budget
        return 1.0 - 0.5 * (minutes - half) / (budget - half)
    if minutes <= cap:
        # 0.5 at budget -> 0.0 at 1.5*budget
        return 0.5 * (1.0 - (minutes - budget) / (cap - budget))
    return 0.0


def preference_fit(
    listing: Listing, profile: SearchProfile
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["preferences"]
    if not profile.preferences:
        return ComponentScore(
            key="preferences",
            score=0.0,
            weight=weight,
            evidence=["no preferences configured"],
            missing_data=True,
        )
    description_lower = (listing.description or "").lower()
    weighted_sum = 0.0
    total_weight = 0.0
    evidence: list[str] = []
    hard_cap: Optional[float] = None
    for pref in profile.preferences:
        total_weight += pref.weight
        present = _preference_present(pref.key, listing, description_lower)
        if present is True:
            weighted_sum += pref.weight
            evidence.append(f"{pref.key} present (weight {pref.weight})")
        elif present is False and pref.weight == 5:
            evidence.append(f"{pref.key} missing (must-have)")
            hard_cap = 0.4 if hard_cap is None else min(hard_cap, 0.4)
        elif present is False:
            evidence.append(f"{pref.key} missing (weight {pref.weight})")
        else:
            # Unknown: neutral half credit so "can't tell" doesn't read as
            # a straight negative.
            weighted_sum += 0.5 * pref.weight
            evidence.append(f"{pref.key} unknown")
    score = weighted_sum / total_weight if total_weight > 0 else 0.0
    return ComponentScore(
        key="preferences",
        score=score,
        weight=weight,
        evidence=evidence[:6],
        hard_cap=hard_cap,
    )


def _preference_present(
    key: str, listing: Listing, description_lower: str
) -> Optional[bool]:
    """Return True/False if we can tell, else None (unknown).

    Structured fields (`furnished`, `pets_allowed`, `smoking_ok`) return
    True/False/None directly from the listing row. Soft tags scan the
    description text; a missing token returns False only when the
    description is non-empty, otherwise None.
    """
    attr = STRUCTURED_PREFERENCES.get(key)
    if attr is not None:
        return getattr(listing, attr, None)
    synonyms = PREFERENCE_KEYWORDS.get(key, (key,))
    if not description_lower:
        return None
    for token in synonyms:
        if token.lower() in description_lower:
            return True
    return False


async def vibe_fit(listing: Listing, profile: SearchProfile) -> ComponentScore:
    """Run `brain.vibe_score` off the event loop; degrade gracefully."""
    weight = COMPONENT_WEIGHTS["vibe"]
    try:
        out = await asyncio.to_thread(brain.vibe_score, listing, profile)
    except (ValidationError, ValueError) as exc:
        logger.warning("vibe_fit: bad JSON from LLM: %s", exc)
        return ComponentScore(
            key="vibe",
            score=0.0,
            weight=weight,
            evidence=["vibe check skipped: invalid LLM output"],
            missing_data=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("vibe_fit: LLM call failed: %s", exc)
        return ComponentScore(
            key="vibe",
            score=0.0,
            weight=weight,
            evidence=["vibe check skipped: LLM error"],
            missing_data=True,
        )
    score = max(0.0, min(1.0, float(out.score)))
    evidence = [str(e)[:120] for e in (out.evidence or [])][:4] or ["vibe match"]
    return ComponentScore(
        key="vibe", score=score, weight=weight, evidence=evidence
    )


# -----------------------------------------------------------------------------
# Composition
# -----------------------------------------------------------------------------


def compose(
    components: list[ComponentScore],
    *,
    veto: Optional[VetoResult] = None,
) -> EvaluationResult:
    """Combine components into one score plus derived UI fields.

    If `veto` is set, return score 0.0 and put the reason in `summary` /
    `veto_reason`. Otherwise: weighted mean over components whose
    `missing_data == False`, then apply the minimum of every non-null
    `hard_cap`, then clamp to [0, 1].
    """
    if veto is not None:
        return EvaluationResult(
            score=0.0,
            components=components,
            veto_reason=veto.reason,
            summary=f"Rejected: {veto.reason}",
            match_reasons=[],
            mismatch_reasons=[veto.reason],
        )
    live = [c for c in components if not c.missing_data and c.weight > 0]
    if not live:
        return EvaluationResult(
            score=0.0,
            components=components,
            veto_reason=None,
            summary="No data to score",
            match_reasons=[],
            mismatch_reasons=["no evaluable components"],
        )
    weight_total = sum(c.weight for c in live)
    weighted = sum(c.score * c.weight for c in live)
    raw = weighted / weight_total if weight_total > 0 else 0.0
    caps = [c.hard_cap for c in components if c.hard_cap is not None]
    capped = min([raw, *caps]) if caps else raw
    final = max(0.0, min(1.0, capped))

    match_reasons, mismatch_reasons = _reasons_from_components(components)
    summary = _summary_from_components(final, components, capped < raw)
    return EvaluationResult(
        score=final,
        components=components,
        veto_reason=None,
        summary=summary,
        match_reasons=match_reasons,
        mismatch_reasons=mismatch_reasons,
    )


def _reasons_from_components(
    components: list[ComponentScore],
) -> tuple[list[str], list[str]]:
    """Derive the legacy `match_reasons` / `mismatch_reasons` lists.

    A component scoring >= 0.7 contributes to `match_reasons`; <= 0.3 to
    `mismatch_reasons`. `missing_data` components are skipped entirely.
    """
    match: list[str] = []
    mismatch: list[str] = []
    for c in components:
        if c.missing_data:
            continue
        if c.score >= 0.7 and c.evidence:
            match.append(c.evidence[0])
        elif c.score <= 0.3 and c.evidence:
            mismatch.append(c.evidence[0])
    return match[:6], mismatch[:6]


def _summary_from_components(
    final: float, components: list[ComponentScore], capped: bool
) -> str:
    """One-sentence paraphrase of the breakdown for `score_reason`."""
    live = [c for c in components if not c.missing_data]
    if not live:
        return f"Scored {final:.2f} (no data)"
    top_positive = max(live, key=lambda c: c.score)
    top_negative = min(live, key=lambda c: c.score)
    bits: list[str] = []
    if top_positive.evidence and top_positive.score >= 0.7:
        bits.append(f"strong {top_positive.key}: {top_positive.evidence[0]}")
    if top_negative.evidence and top_negative.score <= 0.4 and top_negative.key != top_positive.key:
        bits.append(f"weak {top_negative.key}: {top_negative.evidence[0]}")
    if capped:
        bits.append("capped by must-have rule")
    detail = "; ".join(bits) if bits else "mixed fit across components"
    return f"Score {final:.2f}: {detail}"


def breakdown_detail(components: list[ComponentScore]) -> Optional[str]:
    """Compact one-line detail for the `evaluate` action's `detail` field."""
    live = [c for c in components if not c.missing_data]
    if not live:
        return None
    return " · ".join(f"{c.key} {c.score:.2f}" for c in live)


# -----------------------------------------------------------------------------
# Top-level facade
# -----------------------------------------------------------------------------


async def evaluate(
    listing: Listing,
    profile: SearchProfile,
    *,
    travel_times: Optional[dict[tuple[str, str], int]] = None,
) -> EvaluationResult:
    """End-to-end: veto check, all components, vibe LLM call, compose.

    The engine calls this once per new listing inside `run_find_only`.
    Vetoed listings never hit the LLM.
    """
    veto = hard_filter(listing, profile)
    if veto is not None:
        return compose([], veto=veto)

    tt = travel_times or {}
    components: list[ComponentScore] = [
        price_fit(listing, profile),
        size_fit(listing, profile),
        wg_size_fit(listing, profile),
        availability_fit(listing, profile),
        commute_fit(listing, profile, tt),
        preference_fit(listing, profile),
    ]
    components.append(await vibe_fit(listing, profile))
    return compose(components)


__all__ = [
    "COMPONENT_WEIGHTS",
    "DEFAULT_COMMUTE_BUDGET_MIN",
    "EvaluationResult",
    "VetoResult",
    "availability_fit",
    "breakdown_detail",
    "commute_fit",
    "compose",
    "evaluate",
    "hard_filter",
    "preference_fit",
    "price_fit",
    "size_fit",
    "vibe_fit",
    "wg_size_fit",
]
