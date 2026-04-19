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

from openai import RateLimitError
from pydantic import ValidationError

from . import brain, places
from .models import ComponentScore, Listing, NearbyPlace, SearchProfile

logger = logging.getLogger(__name__)


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


# -----------------------------------------------------------------------------
# Configuration (weights + keyword table)
# -----------------------------------------------------------------------------


DEFAULT_COMMUTE_BUDGET_MIN = 40
PRICE_HARD_VETO_MULTIPLIER = 1.5
NEARBY_PLACE_COMFORT_M = 400
NEARBY_PLACE_OK_M = 1000

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


@dataclass(frozen=True)
class PreferenceSignal:
    score: Optional[float]
    evidence: str


# -----------------------------------------------------------------------------
# Hard filter
# -----------------------------------------------------------------------------


def hard_filter(listing: Listing, profile: SearchProfile) -> Optional[VetoResult]:
    """Return a veto if the listing can't possibly match, else None.

    Vetoes short-circuit: no components, no LLM call, score pinned at 0.0.

    Rent is treated as a soft cutoff first: slightly over-budget listings stay
    in the pool and are punished by the price curve instead of being rejected
    immediately. Only listings far beyond the stated budget are vetoed outright.
    """
    if (
        listing.price_eur is not None
        and listing.price_eur > int(profile.max_rent_eur * PRICE_HARD_VETO_MULTIPLIER)
    ):
        return VetoResult(
            reason=(
                f"far over budget (€{listing.price_eur} > "
                f"€{int(profile.max_rent_eur * PRICE_HARD_VETO_MULTIPLIER)})"
            )
        )
    # not needed, if we filter in wg gesucht for city first, also missfires often
    # if (
    #     listing.city
    #     and profile.city
    #     and _normalize_city(listing.city) != _normalize_city(profile.city)
    # ):
    #     return VetoResult(
    #         reason=f"wrong city ({listing.city} != {profile.city})"
    #     )
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
    hi = profile.max_rent_eur
    if hi <= 0:
        return ComponentScore(
            key="price",
            score=0.0,
            weight=weight,
            evidence=["no rent budget configured"],
            missing_data=True,
        )
    score = _descending_cutoff_curve(p, hi)
    if p <= hi:
        evidence = [f"€{p} within budget (€{hi} max)"]
    else:
        evidence = [f"€{p} above budget (€{hi} max) with accelerated penalty"]
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
    hi = float(max(profile.max_size_m2, profile.min_size_m2))
    if s >= hi:
        score = 1.0
        evidence = [f"{s:.0f} m² at or above preferred size {hi:.0f} m²"]
    elif s >= lo:
        span = max(1e-6, hi - lo)
        score = 0.8 + 0.2 * ((s - lo) / span)
        evidence = [f"{s:.0f} m² above minimum {lo:.0f} m²"]
    else:
        score = _ascending_cutoff_curve(s, lo)
        evidence = [f"{s:.0f} m² below minimum {lo:.0f} m²"]
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
        evidence = [f"{n}-person WG within preferred range ({lo}-{hi} people)"]
    elif n == lo - 1 or n == hi + 1:
        score = 0.5
        evidence = [f"{n}-person WG just outside preferred range ({lo}-{hi} people)"]
    else:
        score = 0.0
        evidence = [f"{n}-person WG outside preferred range ({lo}-{hi} people)"]
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
            evidence=[f"Available from {af}, within your move-in window"],
        )
    # Outside window: ramp down over the next 14 days either way.
    days_off = 0
    if mf is not None and af < mf:
        days_off = (mf - af).days
    elif mu is not None and af > mu:
        days_off = (af - mu).days
    score = max(0.0, 1.0 - days_off / 14.0)
    evidence = [f"Available from {af} ({days_off} days outside your move-in window)"]
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
            f"{loc.label}: {minutes} min by {(fastest_mode or '').lower()} "
            f"(target: {budget} min)"
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
    """Gentle decay up to the budget, then a much steeper post-budget drop."""
    return _descending_cutoff_curve(minutes, budget, comfort_ratio=0.5)


def _descending_cutoff_curve(
    value: float, cutoff: float, *, comfort_ratio: float = 0.0
) -> float:
    """Return a hinge curve with a gentle in-band slope and a steep tail.

    `comfort_ratio` creates an optional full-score plateau before the cutoff.
    This lets commute stay perfect while it is comfortably inside budget, while
    price can still reward cheaper listings across the full in-budget range.
    """
    if cutoff <= 0:
        return 0.0
    ratio = max(0.0, float(value) / float(cutoff))
    comfort_ratio = max(0.0, min(comfort_ratio, 0.95))
    if ratio <= comfort_ratio:
        return 1.0
    if ratio <= 1.0:
        span = max(1e-6, 1.0 - comfort_ratio)
        normalized = (ratio - comfort_ratio) / span
        score = 1.0 - 0.2 * (normalized ** 1.3)
    else:
        over = ratio - 1.0
        score = 0.8 - 1.5 * over - 3.5 * (over ** 2)
    return max(0.0, min(1.0, score))


def _ascending_cutoff_curve(value: float, cutoff: float) -> float:
    """Mirrored version of `_descending_cutoff_curve` for benefit metrics.

    Below the cutoff the score drops quickly, while anything at or above the
    cutoff stays strong. We use it for size, where bigger is better until the
    user's preferred threshold is reached.
    """
    if cutoff <= 0:
        return 1.0
    ratio = max(0.0, float(value) / float(cutoff))
    if ratio >= 1.0:
        return 1.0
    shortfall = 1.0 - ratio
    score = 0.8 - 1.5 * shortfall - 3.5 * (shortfall ** 2)
    return max(0.0, min(1.0, score))


def preference_fit(
    listing: Listing,
    profile: SearchProfile,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
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
    nearby = nearby_places or {}
    weighted_sum = 0.0
    total_weight = 0.0
    evidence: list[str] = []
    hard_cap: Optional[float] = None
    for pref in profile.preferences:
        total_weight += pref.weight
        signal = _preference_signal(
            pref.key,
            listing,
            description_lower,
            nearby,
        )
        if signal.score is None:
            weighted_sum += 0.5 * pref.weight
            evidence.append(f"{signal.evidence} (unknown)")
            continue

        weighted_sum += signal.score * pref.weight
        evidence.append(f"{signal.evidence} (weight {pref.weight})")
        if pref.weight == 5 and signal.score <= 0.2:
            hard_cap = 0.4 if hard_cap is None else min(hard_cap, 0.4)
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


def _preference_signal(
    key: str,
    listing: Listing,
    description_lower: str,
    nearby_places: dict[str, NearbyPlace],
) -> PreferenceSignal:
    attr = STRUCTURED_PREFERENCES.get(key)
    if attr is not None:
        value = getattr(listing, attr, None)
        if value is True:
            return PreferenceSignal(1.0, f"{key} present")
        if value is False:
            return PreferenceSignal(0.0, f"{key} missing")
        return PreferenceSignal(None, f"{key} not stated")

    nearby = nearby_places.get(key)
    if nearby is not None and nearby.searched:
        if nearby.distance_m is None:
            return PreferenceSignal(
                0.0,
                f"{nearby.label}: none found within {places.SEARCH_RADIUS_M // 1000} km",
            )
        place_name = nearby.place_name or "nearest match"
        return PreferenceSignal(
            _nearby_place_curve(nearby.distance_m),
            f"{nearby.label}: {nearby.distance_m} m to {place_name}",
        )

    present = _preference_present(key, listing, description_lower)
    if present is True:
        return PreferenceSignal(1.0, f"{key} mentioned in listing")
    if present is False:
        return PreferenceSignal(0.0, f"{key} missing from listing")
    return PreferenceSignal(None, f"{key} unclear")


def _nearby_place_curve(distance_m: int) -> float:
    """Piecewise linear score for "nearby" amenities.

    1.0 inside the comfort radius, 0.5 at the "okay" radius, and 0.0 at
    the search radius.
    """
    if distance_m <= 0:
        return 1.0
    if distance_m <= NEARBY_PLACE_COMFORT_M:
        return 1.0
    if distance_m <= NEARBY_PLACE_OK_M:
        span = max(1, NEARBY_PLACE_OK_M - NEARBY_PLACE_COMFORT_M)
        return 1.0 - 0.5 * (distance_m - NEARBY_PLACE_COMFORT_M) / span
    if distance_m <= places.SEARCH_RADIUS_M:
        span = max(1, places.SEARCH_RADIUS_M - NEARBY_PLACE_OK_M)
        return 0.5 * (1.0 - (distance_m - NEARBY_PLACE_OK_M) / span)
    return 0.0


def _has_vibe_signal(
    profile: SearchProfile,
    nearby_places: Optional[dict[str, NearbyPlace]],
) -> bool:
    """Return True when the profile carries anything the LLM could judge.

    The vibe prompt explicitly tells the model to return 0.5 with
    "not enough vibe information" when the student has no notes, no
    district preferences, and no nearby-place context. We do that check
    in code so we don't spend an OpenAI request (and a slice of the daily
    RPD quota) on listings where the answer is already determined.
    """
    if (profile.notes or "").strip():
        return True
    if profile.preferred_districts or profile.avoid_districts:
        return True
    if profile.preferences:
        return True
    if nearby_places and any(p.searched for p in nearby_places.values()):
        return True
    return False


async def vibe_fit(
    listing: Listing,
    profile: SearchProfile,
    *,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
) -> ComponentScore:
    """Run `brain.vibe_score` off the event loop; degrade gracefully."""
    weight = COMPONENT_WEIGHTS["vibe"]
    if not _has_vibe_signal(profile, nearby_places) or not (listing.description or "").strip():
        return ComponentScore(
            key="vibe",
            score=0.5,
            weight=weight,
            evidence=["not enough vibe information"],
            missing_data=True,
        )
    try:
        out = await asyncio.to_thread(
            brain.vibe_score,
            listing,
            profile,
            nearby_places=nearby_places or {},
        )
    except RateLimitError as exc:
        logger.warning("vibe_fit: LLM rate-limited: %s", exc)
        return ComponentScore(
            key="vibe",
            score=0.0,
            weight=weight,
            evidence=["vibe check skipped: LLM rate limit"],
            missing_data=True,
        )
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


def _component_label(key: str) -> str:
    labels = {
        "price": "price",
        "size": "size",
        "wg_size": "WG size",
        "availability": "availability",
        "commute": "commute",
        "preferences": "preferences",
        "vibe": "vibe",
    }
    return labels.get(key, key.replace("_", " "))


def _summary_from_components(
    final: float, components: list[ComponentScore], capped: bool
) -> str:
    """One-sentence paraphrase of the breakdown for `score_reason`."""
    live = [c for c in components if not c.missing_data]
    if not live:
        return f"Scored {_pct(final)} (no data)"
    top_positive = max(live, key=lambda c: c.score)
    top_negative = min(live, key=lambda c: c.score)
    bits: list[str] = []
    if top_positive.evidence and top_positive.score >= 0.7:
        bits.append(f"strong {_component_label(top_positive.key)} fit: {top_positive.evidence[0]}")
    if top_negative.evidence and top_negative.score <= 0.4 and top_negative.key != top_positive.key:
        bits.append(f"weak {_component_label(top_negative.key)} fit: {top_negative.evidence[0]}")
    if capped:
        bits.append("capped by must-have rule")
    detail = "; ".join(bits) if bits else "mixed fit across components"
    return f"Score {_pct(final)}: {detail}"


def breakdown_detail(components: list[ComponentScore]) -> Optional[str]:
    """Compact one-line detail for the `evaluate` action's `detail` field."""
    live = [c for c in components if not c.missing_data]
    if not live:
        return None
    return " · ".join(f"{c.key} {_pct(c.score)}" for c in live)


# -----------------------------------------------------------------------------
# Top-level facade
# -----------------------------------------------------------------------------


async def evaluate(
    listing: Listing,
    profile: SearchProfile,
    *,
    travel_times: Optional[dict[tuple[str, str], int]] = None,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
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
        preference_fit(listing, profile, nearby_places),
    ]
    components.append(await vibe_fit(listing, profile, nearby_places=nearby_places))
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
