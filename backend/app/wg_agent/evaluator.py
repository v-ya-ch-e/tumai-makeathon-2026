"""Scorecard evaluator (Matcher v2) — single source of truth for scoring.

Reference: `docs/MATCHER.md`. Curve boundaries and the composition
contract are pinned in `tests/test_evaluator.py`; per-family preference
resolution in `tests/test_evaluator_resolvers.py`; ten realistic
end-to-end profiles in `tests/test_evaluator_integration.py`.

Pipeline (one call per `(user, listing)` pair, owned by
`UserAgent.run_match_pass`):

    1. `hard_filter` -> binary veto. Score pinned at 0.0 on failure;
       no LLM call, no caps.
    2. `vibe_fit`    -> single LLM call (when the user has §3.4 LLM-
       resolved preferences, this runs first so its `soft_signal_scores`
       and `tenancy_label` can feed the deterministic components).
    3. Eight deterministic component functions (`price_fit`, `size_fit`,
       `wg_size_fit`, `availability_fit`, `commute_fit`,
       `preference_fit`, `tenancy_fit`, `upfront_cost_fit`) plus the
       absolute `quality_fit`.
    4. `compose` -> weighted mean over `live` components (excluding
       `quality`), then `min(raw, *caps)`, then post-blend with quality
       at `0.85·match + 0.15·quality`.

Every `ComponentScore.evidence[]` entry ends with a provenance tag in
square brackets — `[google]`, `[listing]`, `[llm]`, or `[engine]` —
so the drawer can render confidence accordingly.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from . import brain, places
from .market import MarketContext
from .models import (
    ComponentScore,
    Listing,
    NearbyPlace,
    SearchProfile,
)


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Constants — pinned per MATCHER.md §4 + §3.2 + §5.x.
# -----------------------------------------------------------------------------


# Hard-filter and cap multipliers (MATCHER.md §4 threshold matrix).
PRICE_VETO_MULT           = 1.5
COMMUTE_VETO_MULT         = 2.0
COMMUTE_CAP_MULT          = 1.5
PREF_HARD_CAP_WEIGHT5     = 0.5
PREF_HARD_CAP_WEIGHT5_UNK = 0.6
PREF_HARD_CAP_WEIGHT4     = 0.7
COMMUTE_HARD_CAP          = 0.45
SCAM_VIBE_HARD_CAP        = 0.30
SCAM_VIBE_CAP_THRESHOLD   = 0.7

# Defaults.
DEFAULT_COMMUTE_BUDGET_MIN = 35

# Weights (MATCHER.md §7). `quality` is intentionally `0.0` in the
# weighted-mean step — it only enters via the §6 post-blend. Listed
# here for drawer-visibility and so changing one column is one diff.
COMPONENT_WEIGHTS: dict[str, float] = {
    "price": 2.0,
    "commute": 2.5,
    "preferences": 1.5,
    "vibe": 1.5,
    "size": 1.0,
    "availability": 0.8,
    "tenancy": 0.6,
    "wg_size": 0.5,
    "upfront_cost": 0.6,
    "quality": 0.0,
}

# Post-blend weight for the absolute quality signal (MATCHER.md §6).
QUALITY_BLEND_WEIGHT = 0.15


# Preference resolver families (MATCHER.md §3). Wizard tile keys are
# routed to exactly one family. Anything not in this map falls back to
# the §3.3 substring/regex scan with synonyms `(key,)` (i.e. likely
# returns `None` and degrades gracefully).
class _Family:
    STRUCTURED = "structured"   # Listing.{furnished, pets_allowed, smoking_ok}
    PLACES = "places"           # places.nearby_places + PLACE_DISTANCE_BANDS
    KEYWORD = "keyword"         # description regex with word boundaries
    LLM = "llm"                 # vibe_fit.soft_signal_scores


# Wizard tile -> resolver family + (for STRUCTURED) the listing field
# and an `invert` flag (we want `smoking_ok = False`, i.e. invert).
STRUCTURED_PREFERENCES: dict[str, tuple[str, bool]] = {
    "furnished":   ("furnished",    False),
    "pet_friendly": ("pets_allowed", False),
    "non_smoking": ("smoking_ok",   True),
}

# §3.3 keyword preferences: regex with word boundaries (no `Bahnhof`
# false-positive on `\bhof\b`, no `unruhig` matching `quiet_area`).
# Each entry is a compiled case-insensitive regex; `quiet_area` carries
# a separate negative regex that flips the score to 0.0.
KEYWORD_PREFERENCES: dict[str, re.Pattern[str]] = {
    "balcony": re.compile(
        r"\b(balkon|balcony|terrasse|terrace|dachterrasse|loggia)\b", re.I
    ),
    "garden": re.compile(r"\b(garten|garden|innenhof)\b", re.I),
    "elevator": re.compile(r"\b(aufzug|elevator|lift|fahrstuhl)\b", re.I),
    "dishwasher": re.compile(r"\b(spülmaschine|geschirrspüler|dishwasher)\b", re.I),
    "washing_machine": re.compile(
        r"\b(waschmaschine|washing\s+machine|washer)\b", re.I
    ),
    "bike_storage": re.compile(
        r"\b(fahrradkeller|fahrradraum|bike\s+storage|radkeller|fahrradabstellplatz)\b",
        re.I,
    ),
    "parking": re.compile(
        r"\b(parkplatz|parking|garage|tiefgarage|stellplatz)\b", re.I
    ),
    "quiet_area": re.compile(r"\b(ruhig|ruhige|quiet|leise)\b", re.I),
}
KEYWORD_NEGATIVES: dict[str, re.Pattern[str]] = {
    "quiet_area": re.compile(r"\b(unruhig|laut|loud)\b", re.I),
}


# §3.4 LLM-resolved soft-signal keys. `vibe_fit` reports per-key scores
# in `VibeJudgement.soft_signal_scores`.
LLM_PREFERENCES: frozenset[str] = frozenset(
    {
        "student_household",
        "couples_ok",
        "lgbt_friendly",
        "english_speaking",
        "international_friendly",
        "wg_gender",
        "wg_age_band",
    }
)


# -----------------------------------------------------------------------------
# Small helpers.
# -----------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _normalize_district(value: Optional[str]) -> Optional[str]:
    """Case + umlaut + `-`-fold so `Schwabing-West` matches `schwabing west`."""
    if not value:
        return None
    lowered = value.strip().lower()
    return (
        lowered.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("-", " ")
    )


# Backward-compat alias used by the `hard_filter` city check (kept for
# legacy tests that reach `_normalize_city`).
_normalize_city = _normalize_district


def _resolver_family(key: str) -> str:
    if key in STRUCTURED_PREFERENCES:
        return _Family.STRUCTURED
    if key in places.PLACE_DISTANCE_BANDS:
        return _Family.PLACES
    if key in KEYWORD_PREFERENCES:
        return _Family.KEYWORD
    if key in LLM_PREFERENCES:
        return _Family.LLM
    return _Family.KEYWORD  # safe fallback: produces None → unknown


def _tag(text: str, source: str) -> str:
    """Append a provenance tag (e.g. `[google]`) per MATCHER.md §10."""
    return f"{text} [{source}]"


# -----------------------------------------------------------------------------
# Result types.
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class VetoResult:
    reason: str


@dataclass(frozen=True)
class CapSource:
    """Records which component / cap value drove a binding hard cap.

    Used by `compose` to render the cap-tail in `score_reason`
    (MATCHER.md §10): `(capped at 0.45 by deal-breaker commute to
    Marienplatz)`.
    """

    component_key: str
    cap: float
    reason: str


@dataclass
class EvaluationResult:
    """Output of `evaluate`.

    `score` is the post-blend `final` (MATCHER.md §6) — this is what
    the matcher persists. `match_score` and `quality_score` are
    surfaced separately so the drawer can show both bars.
    """

    score: float
    components: list[ComponentScore]
    veto_reason: Optional[str]
    summary: str
    match_reasons: list[str]
    mismatch_reasons: list[str]
    match_score: float = 0.0
    quality_score: float = 0.0
    cap_source: Optional[CapSource] = None


@dataclass(frozen=True)
class PreferenceSignal:
    score: Optional[float]
    evidence: str
    family: str


# -----------------------------------------------------------------------------
# Hard filter (§4).
# -----------------------------------------------------------------------------


def hard_filter(
    listing: Listing,
    profile: SearchProfile,
    *,
    travel_times: Optional[dict[tuple[str, str], int]] = None,
) -> Optional[VetoResult]:
    """Return the first matching veto reason or `None`.

    Vetoed listings short-circuit composition — see `compose`.
    """
    # Rule 1 — price hard veto.
    if (
        listing.price_eur is not None
        and listing.price_eur > int(profile.max_rent_eur * PRICE_VETO_MULT)
    ):
        return VetoResult(
            reason=(
                f"far over budget (€{listing.price_eur} > "
                f"€{int(profile.max_rent_eur * PRICE_VETO_MULT)})"
            )
        )

    # Rule 2 — weight-5 structured pref explicitly missing.
    for pref in profile.preferences:
        if pref.weight != 5 or pref.key not in STRUCTURED_PREFERENCES:
            continue
        attr, invert = STRUCTURED_PREFERENCES[pref.key]
        listing_val = getattr(listing, attr, None)
        if listing_val is None:
            continue  # unknown does NOT veto (handled by §5.7 cap).
        actual = (not listing_val) if invert else bool(listing_val)
        if actual is False:
            return VetoResult(reason=f"must-have '{pref.key}' missing")

    # Rule 3 — available too late.
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

    # Rule 4 — district veto with normalised compare.
    if listing.district and profile.avoid_districts:
        norm_d = _normalize_district(listing.district)
        avoid_set = {_normalize_district(d) for d in profile.avoid_districts}
        if norm_d in avoid_set:
            return VetoResult(reason=f"district on avoid list ({listing.district})")

    # Rule 5 — every scored anchor blew COMMUTE_VETO_MULT × budget.
    if travel_times and profile.main_locations:
        scored: list[tuple[float, float]] = []  # (minutes, budget)
        for loc in profile.main_locations:
            fastest = _fastest_minutes_for_location(loc.place_id, travel_times)
            if fastest is None:
                continue
            budget = loc.max_commute_minutes or DEFAULT_COMMUTE_BUDGET_MIN
            scored.append((fastest, budget))
        if scored and all(m > b * COMMUTE_VETO_MULT for (m, b) in scored):
            return VetoResult(
                reason="no anchor reachable within reasonable time"
            )

    return None


def _fastest_minutes_for_location(
    place_id: str,
    travel_times: dict[tuple[str, str], int],
) -> Optional[float]:
    fastest: Optional[int] = None
    for (pid, _mode), seconds in travel_times.items():
        if pid != place_id:
            continue
        if fastest is None or seconds < fastest:
            fastest = seconds
    if fastest is None:
        return None
    return fastest / 60.0


# -----------------------------------------------------------------------------
# §5.1 price_fit
# -----------------------------------------------------------------------------


def price_fit(
    listing: Listing,
    profile: SearchProfile,
    *,
    market_context: Optional[MarketContext] = None,
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["price"]
    if listing.price_eur is None:
        return ComponentScore(
            key="price",
            score=0.0,
            weight=weight,
            evidence=[_tag("price unknown", "listing")],
            missing_data=True,
        )
    cap = profile.max_rent_eur
    if cap <= 0:
        return ComponentScore(
            key="price",
            score=0.0,
            weight=weight,
            evidence=[_tag("no rent budget configured", "engine")],
            missing_data=True,
        )

    p = listing.price_eur
    ratio = p / cap
    if ratio <= 0.5:
        score = 1.0
    elif ratio <= 1.0:
        score = 1.0 - 0.4 * (ratio - 0.5) / 0.5
    elif ratio <= 1.2:
        score = max(0.0, 0.6 - 3.0 * (ratio - 1.0))
    else:
        score = 0.0

    evidence: list[str] = []
    if p <= cap:
        evidence.append(_tag(f"€{p} inside budget €{cap}", "listing"))
    else:
        evidence.append(
            _tag(
                f"€{p} over budget €{cap} — accelerated penalty",
                "listing",
            )
        )

    # Warmmiete contract evidence.
    if listing.price_basis == "kalt_uplift":
        evidence.append(
            _tag(
                "price reported as Kaltmiete; warm-rent estimate +20%",
                "engine",
            )
        )

    # Suspiciously cheap evidence (also surfaced via vibe red flags
    # downstream by the orchestrator). Pure evidence — does not move
    # the score per MATCHER.md §5.1.
    if (
        profile.min_rent_eur > 0
        and p < profile.min_rent_eur * 0.7
    ):
        evidence.append(
            _tag(
                f"suspiciously cheap (below your floor €{profile.min_rent_eur}) — verify it's real",
                "engine",
            )
        )

    # Market percentile evidence (MATCHER.md §5.1).
    if market_context is not None:
        evidence.append(
            _tag(
                f"€{p} at {market_context.percentile}th percentile for "
                f"{listing.kind} in {market_context.district_label or '?'} "
                f"({market_context.peer_count} peers, median €{market_context.median_price_eur})",
                "engine",
            )
        )

    return ComponentScore(
        key="price", score=score, weight=weight, evidence=evidence
    )


# -----------------------------------------------------------------------------
# §5.2 size_fit
# -----------------------------------------------------------------------------


def size_fit(listing: Listing, profile: SearchProfile) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["size"]
    if listing.size_m2 is None:
        return ComponentScore(
            key="size",
            score=0.0,
            weight=weight,
            evidence=[_tag("size unknown", "listing")],
            missing_data=True,
        )
    s = float(listing.size_m2)
    lo = float(profile.min_size_m2)
    hi = float(max(profile.max_size_m2, profile.min_size_m2))
    # Defensive: lo == hi degenerates to a flat band — keep monotone.
    if hi <= lo:
        hi = lo + 1.0
    mid = lo + 0.3 * (hi - lo)

    if s >= hi:
        score = 1.0
        evidence = _tag(f"{s:.0f} m² at or above your preferred {hi:.0f} m²", "listing")
    elif s >= mid:
        score = 0.85 + 0.15 * (s - mid) / max(1e-9, hi - mid)
        evidence = _tag(f"{s:.0f} m² inside your preferred range", "listing")
    elif s >= lo:
        score = 0.6 + 0.25 * (s - lo) / max(1e-9, mid - lo)
        evidence = _tag(f"{s:.0f} m² above your minimum {lo:.0f} m²", "listing")
    elif lo > 0:
        score = 0.6 * (s / lo)
        evidence = _tag(f"{s:.0f} m² below your minimum {lo:.0f} m²", "listing")
    else:
        score = 0.0
        evidence = _tag("size below floor", "listing")

    return ComponentScore(
        key="size", score=score, weight=weight, evidence=[evidence]
    )


# -----------------------------------------------------------------------------
# §5.3 commute_fit
# -----------------------------------------------------------------------------


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
            evidence=[_tag("no main locations configured", "engine")],
            missing_data=True,
        )
    if not travel_times:
        return ComponentScore(
            key="commute",
            score=0.0,
            weight=weight,
            evidence=[_tag("no commute data", "google")],
            missing_data=True,
        )

    sub_scores: list[float] = []
    evidence: list[str] = []
    hard_cap: Optional[float] = None
    cap_reason: Optional[str] = None
    scored_count = 0

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
        scored_count += 1
        minutes = round(fastest_secs / 60)
        budget = loc.max_commute_minutes or DEFAULT_COMMUTE_BUDGET_MIN
        sub = _commute_sub_score(minutes, budget)
        sub_scores.append(sub)
        evidence.append(
            _tag(
                f"{loc.label}: {minutes} min by "
                f"{(fastest_mode or '').lower()} (target {budget} min)",
                "google",
            )
        )
        if minutes > budget * COMMUTE_CAP_MULT:
            hard_cap = (
                COMMUTE_HARD_CAP if hard_cap is None else min(hard_cap, COMMUTE_HARD_CAP)
            )
            cap_reason = f"deal-breaker commute to {loc.label}"

    if scored_count == 0:
        return ComponentScore(
            key="commute",
            score=0.0,
            weight=weight,
            evidence=[_tag("no commute matches for configured locations", "google")],
            missing_data=True,
        )

    if scored_count < len(profile.main_locations):
        evidence.append(
            _tag(
                f"{scored_count} of {len(profile.main_locations)} anchors had routing data",
                "google",
            )
        )

    # Aggregator: 0.7 * min + 0.3 * mean. With one anchor the formula
    # collapses to `sub`.
    m = min(sub_scores)
    mean = sum(sub_scores) / len(sub_scores)
    score = 0.7 * m + 0.3 * mean

    component = ComponentScore(
        key="commute",
        score=score,
        weight=weight,
        evidence=evidence,
        hard_cap=hard_cap,
    )
    if cap_reason is not None:
        # Stash the cap reason on the component via evidence so compose
        # can find it without an extra return value.
        component.evidence.append(_tag(f"cap reason: {cap_reason}", "engine"))
    return component


def _commute_sub_score(minutes: float, budget: float) -> float:
    if budget <= 0:
        return 0.0
    ratio = minutes / budget
    if ratio <= 0.6:
        return 1.0
    if ratio <= 1.0:
        return 1.0 - 0.4 * (ratio - 0.6) / 0.4
    if ratio <= 1.5:
        return max(0.0, 0.6 - 1.2 * (ratio - 1.0))
    return 0.0


# -----------------------------------------------------------------------------
# §5.4 availability_fit
# -----------------------------------------------------------------------------


def availability_fit(
    listing: Listing, profile: SearchProfile
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["availability"]
    if listing.available_from is None:
        return ComponentScore(
            key="availability",
            score=0.0,
            weight=weight,
            evidence=[_tag("no availability date", "listing")],
            missing_data=True,
        )
    if profile.move_in_from is None and profile.move_in_until is None:
        return ComponentScore(
            key="availability",
            score=0.0,
            weight=weight,
            evidence=[_tag("no move-in window configured", "engine")],
            missing_data=True,
        )
    af = listing.available_from
    mf = profile.move_in_from
    mu = profile.move_in_until

    if (mf is None or af >= mf) and (mu is None or af <= mu):
        return ComponentScore(
            key="availability",
            score=1.0,
            weight=weight,
            evidence=[_tag(f"available {af}, within your move-in window", "listing")],
        )

    days_off = 0
    if mf is not None and af < mf:
        days_off = (mf - af).days
    elif mu is not None and af > mu:
        days_off = (af - mu).days

    if days_off <= 7:
        score = 0.8
    elif days_off <= 30:
        score = 0.5
    elif days_off <= 60:
        score = 0.2
    else:
        score = 0.0

    return ComponentScore(
        key="availability",
        score=score,
        weight=weight,
        evidence=[
            _tag(
                f"available {af} — {days_off} days outside your window",
                "listing",
            )
        ],
    )


# -----------------------------------------------------------------------------
# §5.5 wg_size_fit
# -----------------------------------------------------------------------------


def wg_size_fit(
    listing: Listing, profile: SearchProfile
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["wg_size"]
    if profile.mode == "flat" or listing.kind == "flat":
        return ComponentScore(
            key="wg_size",
            score=0.0,
            weight=weight,
            evidence=[_tag("flat search — WG size irrelevant", "engine")],
            missing_data=True,
        )
    if listing.wg_size is None:
        return ComponentScore(
            key="wg_size",
            score=0.0,
            weight=weight,
            evidence=[_tag("WG size unknown", "listing")],
            missing_data=True,
        )

    n = listing.wg_size
    lo = profile.min_wg_size
    hi = profile.max_wg_size

    # Explicit lone-roommate floor: a 1-person "WG" against a real WG
    # preference is a 0.0 regardless of band.
    if n == 1 and lo > 1:
        return ComponentScore(
            key="wg_size",
            score=0.0,
            weight=weight,
            evidence=[
                _tag(
                    f"{n}-person WG below your minimum ({lo}-{hi} people)",
                    "listing",
                )
            ],
        )

    if lo <= n <= hi:
        score, label = 1.0, "within"
    elif n in (lo - 1, hi + 1):
        score, label = 0.6, "just outside"
    elif n in (lo - 2, hi + 2):
        score, label = 0.3, "two off from"
    else:
        score, label = 0.0, "outside"

    return ComponentScore(
        key="wg_size",
        score=score,
        weight=weight,
        evidence=[
            _tag(
                f"{n}-person WG {label} preferred range ({lo}-{hi} people)",
                "listing",
            )
        ],
    )


# -----------------------------------------------------------------------------
# §5.6 tenancy_fit
# -----------------------------------------------------------------------------


def tenancy_fit(
    listing: Listing,
    profile: SearchProfile,
    *,
    tenancy_label: Optional[str] = None,
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["tenancy"]
    if listing.available_from is None:
        return ComponentScore(
            key="tenancy",
            score=0.0,
            weight=weight,
            evidence=[_tag("no availability date — cannot estimate length", "listing")],
            missing_data=True,
        )

    listing_months: Optional[float] = None
    evidence_source = "listing"
    if listing.available_to is not None:
        days = (listing.available_to - listing.available_from).days
        listing_months = max(1.0, days / 30.44)  # average month length
    elif tenancy_label == "open_ended":
        listing_months = 999.0
        evidence_source = "llm"
    elif tenancy_label == "long_term":
        listing_months = 12.0
        evidence_source = "llm"
    elif tenancy_label == "mid_term":
        listing_months = 4.0
        evidence_source = "llm"
    elif tenancy_label == "short_term":
        listing_months = 1.0
        evidence_source = "llm"

    if listing_months is None:
        return ComponentScore(
            key="tenancy",
            score=0.0,
            weight=weight,
            evidence=[_tag("lease length unknown", "listing")],
            missing_data=True,
        )

    if profile.desired_min_months is None:
        # Default heuristic: prefer longer.
        if listing_months >= 12:
            score, label = 1.0, "12+ months"
        elif listing_months >= 6:
            score, label = 0.7, "6–12 months"
        elif listing_months >= 3:
            score, label = 0.5, "3–6 months"
        else:
            score, label = 0.2, "short sublet"
    else:
        d = profile.desired_min_months
        if listing_months >= d:
            score, label = 1.0, f"meets your {d}+ month intent"
        elif listing_months >= 0.7 * d:
            score, label = 0.6, f"slightly under your {d}+ month intent"
        elif listing_months >= 0.5 * d:
            score, label = 0.3, f"well under your {d}+ month intent"
        else:
            score, label = 0.0, f"far under your {d}+ month intent"

    if listing.available_to is not None:
        head = (
            f"runs {listing.available_from}→{listing.available_to} "
            f"(~{int(listing_months)} mo, {label})"
        )
    elif tenancy_label and tenancy_label != "unknown":
        head = (
            f"{tenancy_label.replace('_', '-')} per description "
            f"(~{int(listing_months)} mo, {label})"
        )
    else:
        head = f"~{int(listing_months)} months ({label})"

    return ComponentScore(
        key="tenancy",
        score=score,
        weight=weight,
        evidence=[_tag(head, evidence_source)],
    )


# -----------------------------------------------------------------------------
# §5.7 preference_fit (and §3 family resolvers)
# -----------------------------------------------------------------------------


def preference_fit(
    listing: Listing,
    profile: SearchProfile,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
    *,
    soft_signal_scores: Optional[dict[str, float]] = None,
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["preferences"]
    if not profile.preferences:
        return ComponentScore(
            key="preferences",
            score=0.0,
            weight=weight,
            evidence=[_tag("no preferences configured", "engine")],
            missing_data=True,
        )

    description_lower = (listing.description or "").lower()
    nearby = nearby_places or {}
    soft = soft_signal_scores or {}

    weighted_sum = 0.0
    total_weight = 0.0
    caps: list[tuple[float, str]] = []  # (cap_value, "<key> reason")
    evidence: list[str] = []

    for pref in profile.preferences:
        signal = _resolve_preference(
            pref.key,
            listing,
            description_lower,
            nearby,
            soft,
            profile=profile,
        )
        s = signal.score

        if s is None:
            if pref.weight <= 3:
                continue  # ignore unknown nice-to-haves
            if pref.weight == 4:
                s = 0.4
                evidence.append(
                    _tag(
                        f"{pref.key}: no description evidence (imputed 0.4 for important pref)",
                        signal.family,
                    )
                )
            else:  # weight == 5 unknown
                s = 0.4
                caps.append(
                    (
                        PREF_HARD_CAP_WEIGHT5_UNK,
                        f"unknown must-have '{pref.key}'",
                    )
                )
                evidence.append(
                    _tag(
                        f"{pref.key}: no evidence — capped at "
                        f"{PREF_HARD_CAP_WEIGHT5_UNK:.2f}",
                        signal.family,
                    )
                )
        else:
            evidence.append(_tag(signal.evidence, signal.family))

        weighted_sum += s * pref.weight
        total_weight += pref.weight

        if pref.weight == 5 and s <= 0.2:
            caps.append((PREF_HARD_CAP_WEIGHT5, f"missing must-have '{pref.key}'"))
        if pref.weight == 4 and s <= 0.1:
            caps.append((PREF_HARD_CAP_WEIGHT4, f"missing important '{pref.key}'"))

    if total_weight == 0:
        # Every pref was an unknown nice-to-have — promote to missing
        # data so the component drops out of `live` cleanly (was a
        # misleading 0.0 in v1).
        return ComponentScore(
            key="preferences",
            score=0.0,
            weight=weight,
            evidence=[_tag("all preferences unknown", "engine")],
            missing_data=True,
        )

    score = weighted_sum / total_weight

    hard_cap: Optional[float] = None
    cap_reason: Optional[str] = None
    if caps:
        hard_cap, cap_reason = min(caps, key=lambda x: x[0])

    component = ComponentScore(
        key="preferences",
        score=score,
        weight=weight,
        evidence=evidence[:6],
        hard_cap=hard_cap,
    )
    if cap_reason is not None:
        component.evidence.append(_tag(f"cap reason: {cap_reason}", "engine"))
    return component


def _resolve_preference(
    key: str,
    listing: Listing,
    description_lower: str,
    nearby_places: dict[str, NearbyPlace],
    soft_signal_scores: dict[str, float],
    *,
    profile: SearchProfile,
) -> PreferenceSignal:
    family = _resolver_family(key)

    if family == _Family.STRUCTURED:
        attr, invert = STRUCTURED_PREFERENCES[key]
        value = getattr(listing, attr, None)
        if value is None:
            return PreferenceSignal(None, f"{key} not stated", "listing")
        actual = (not value) if invert else bool(value)
        if actual:
            return PreferenceSignal(1.0, f"{key} present", "listing")
        return PreferenceSignal(0.0, f"{key} missing", "listing")

    if family == _Family.PLACES:
        nearby = nearby_places.get(key)
        if nearby is not None and nearby.searched:
            if nearby.distance_m is None:
                bands = places.PLACE_DISTANCE_BANDS.get(key)
                radius = bands[2] if bands else places.SEARCH_RADIUS_M
                return PreferenceSignal(
                    0.0,
                    f"{nearby.label}: none found within {radius / 1000:.1f} km",
                    "google",
                )
            curve_score = _distance_score(nearby.distance_m, key)
            place_name = nearby.place_name or "nearest match"
            return PreferenceSignal(
                curve_score,
                f"{nearby.label}: {nearby.distance_m} m to {place_name}",
                "google",
            )
        return PreferenceSignal(None, f"{key} lookup unavailable", "google")

    if family == _Family.KEYWORD:
        pat = KEYWORD_PREFERENCES.get(key)
        if pat is None:
            # Wizard tile we don't know — degrade to "unknown".
            if not description_lower:
                return PreferenceSignal(None, f"{key} unclear", "listing")
            return PreferenceSignal(0.0, f"{key} not mentioned", "listing")
        if not description_lower:
            return PreferenceSignal(None, f"{key} unclear", "listing")
        neg = KEYWORD_NEGATIVES.get(key)
        if neg is not None and neg.search(description_lower):
            return PreferenceSignal(0.0, f"{key}: opposite mentioned", "listing")
        if pat.search(description_lower):
            return PreferenceSignal(1.0, f"{key} mentioned in description", "listing")
        return PreferenceSignal(0.0, f"{key} not mentioned", "listing")

    if family == _Family.LLM:
        if key not in soft_signal_scores:
            return PreferenceSignal(None, f"{key} not stated in description", "llm")
        s = soft_signal_scores[key]
        s = _clamp01(float(s))
        # Helpful labels for the §3.4 demographic keys.
        if key == "wg_gender":
            label = (
                "matches your gender preference"
                if s >= 0.5
                else "explicitly excludes your gender"
            )
        elif key == "wg_age_band":
            label = (
                "fits your age band"
                if s >= 0.5
                else "explicitly excludes your age"
            )
        else:
            label = "described as a match" if s >= 0.5 else "weak signal in text"
        return PreferenceSignal(s, f"{key}: {label}", "llm")

    return PreferenceSignal(None, f"{key} unclear", "listing")


def _distance_score(distance_m: float, key: str) -> float:
    """Per-category piecewise linear distance score (MATCHER.md §3.2)."""
    band = places.PLACE_DISTANCE_BANDS.get(key)
    if band is None:
        return 0.0
    comfort, ok, mx = band
    if distance_m <= comfort:
        return 1.0
    if distance_m <= ok:
        return 1.0 - 0.4 * (distance_m - comfort) / max(1e-9, ok - comfort)
    if distance_m <= mx:
        return max(0.0, 0.6 * (1.0 - (distance_m - ok) / max(1e-9, mx - ok)))
    return 0.0


# -----------------------------------------------------------------------------
# §5.8 vibe_fit (LLM)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class VibeOutcome:
    """Container the orchestrator uses to thread vibe side-channel facts.

    `component` is what `compose` consumes; the rest of the fields are
    used by `tenancy_fit`, `quality_fit`, `preference_fit` (soft
    signals), and the orchestrator's `match_reasons` / `mismatch_reasons`
    enrichment.
    """

    component: ComponentScore
    soft_signal_scores: dict[str, float]
    tenancy_label: Optional[str]
    scam_severity: float
    red_flags: list[str]
    green_flags: list[str]


async def vibe_fit(
    listing: Listing,
    profile: SearchProfile,
    *,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
    soft_signal_keys: Optional[list[str]] = None,
) -> VibeOutcome:
    """Run `brain.vibe_score` off the event loop and degrade gracefully.

    Returns a `VibeOutcome` so the orchestrator can route side channels
    into other components without a second LLM call.
    """
    weight = COMPONENT_WEIGHTS["vibe"]
    requested_keys = list(soft_signal_keys or _llm_pref_keys_from_profile(profile))
    try:
        out = await asyncio.to_thread(
            brain.vibe_score,
            listing,
            profile,
            nearby_places=nearby_places or {},
            soft_signal_keys=requested_keys,
        )
    except (ValidationError, ValueError) as exc:
        logger.warning("vibe_fit: bad JSON from LLM: %s", exc)
        return _vibe_missing(weight, "invalid LLM output")
    except Exception as exc:  # noqa: BLE001
        logger.warning("vibe_fit: LLM call failed: %s", exc)
        return _vibe_missing(weight, "LLM error")

    score = _clamp01(float(out.fit_score))
    evidence: list[str] = []
    for e in (out.evidence or [])[:4]:
        evidence.append(_tag(str(e)[:120], "llm"))
    if not evidence:
        evidence.append(_tag("vibe match", "llm"))
    if out.flatmate_vibe:
        evidence.append(_tag(out.flatmate_vibe[:120], "llm"))
    if out.lifestyle_match:
        evidence.append(_tag(out.lifestyle_match[:120], "llm"))

    hard_cap: Optional[float] = None
    cap_reason: Optional[str] = None
    if out.scam_severity >= SCAM_VIBE_CAP_THRESHOLD:
        hard_cap = SCAM_VIBE_HARD_CAP
        cap_reason = (
            f"high scam risk (severity {out.scam_severity:.2f})"
        )
        evidence.append(_tag(f"cap reason: {cap_reason}", "engine"))

    component = ComponentScore(
        key="vibe",
        score=score,
        weight=weight,
        evidence=evidence[:6],
        hard_cap=hard_cap,
    )

    return VibeOutcome(
        component=component,
        soft_signal_scores=dict(out.soft_signal_scores or {}),
        tenancy_label=out.tenancy_label,
        scam_severity=float(out.scam_severity),
        red_flags=[str(f)[:120] for f in (out.red_flags or [])][:3],
        green_flags=[str(f)[:120] for f in (out.green_flags or [])][:3],
    )


def _vibe_missing(weight: float, reason: str) -> VibeOutcome:
    return VibeOutcome(
        component=ComponentScore(
            key="vibe",
            score=0.0,
            weight=weight,
            evidence=[_tag(f"vibe check skipped: {reason}", "llm")],
            missing_data=True,
        ),
        soft_signal_scores={},
        tenancy_label=None,
        scam_severity=0.0,
        red_flags=[],
        green_flags=[],
    )


def _llm_pref_keys_from_profile(profile: SearchProfile) -> list[str]:
    return [p.key for p in profile.preferences if p.key in LLM_PREFERENCES]


# -----------------------------------------------------------------------------
# §5.9 quality_fit (absolute, never missing)
# -----------------------------------------------------------------------------


def quality_fit(
    listing: Listing,
    *,
    scam_severity: float = 0.0,
    tenancy_label: Optional[str] = None,
) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["quality"]  # 0.0 in `live`, only post-blend.

    desc = (listing.description or "").strip()
    desc_len = len(desc)
    markers = sum(
        1
        for token in (
            r"\b\d+\s?€\b",
            r"\b\d+\s?m²\b",
            r"\bkaution\b",
            r"\b(verfügbar|available|frei)\b",
            r"\b(stadtteil|district|bezirk|stadtviertel)\b",
        )
        if re.search(token, desc, re.I)
    )
    if desc_len >= 600 and markers >= 3:
        description_quality = 1.0
    elif desc_len >= 200:
        description_quality = 0.7
    elif desc_len >= 50:
        description_quality = 0.4
    else:
        description_quality = 0.1

    photo_count = len(listing.photo_urls or [])
    if photo_count >= 2:
        media_quality = 1.0
    elif photo_count == 1:
        media_quality = 0.8
    else:
        media_quality = 0.4

    has_from = listing.available_from is not None
    has_end = (
        listing.available_to is not None
        or (tenancy_label is not None and tenancy_label != "unknown")
    )
    if has_from and has_end:
        availability_clarity = 1.0
    elif has_from or has_end:
        availability_clarity = 0.5
    else:
        availability_clarity = 0.0

    quality = (
        0.45 * description_quality
        + 0.25 * media_quality
        + 0.15 * availability_clarity
        + 0.15 * (1.0 - _clamp01(scam_severity))
    )
    quality = _clamp01(quality)

    evidence = [
        _tag(
            f"description {description_quality:.0%}, photos {photo_count}, "
            f"availability clarity {availability_clarity:.0%}",
            "engine",
        )
    ]
    if scam_severity > 0:
        evidence.append(_tag(f"scam-severity penalty: {scam_severity:.2f}", "llm"))

    return ComponentScore(
        key="quality",
        score=quality,
        weight=weight,
        evidence=evidence,
    )


# -----------------------------------------------------------------------------
# §5.10 upfront_cost_fit (new)
# -----------------------------------------------------------------------------


def upfront_cost_fit(listing: Listing) -> ComponentScore:
    weight = COMPONENT_WEIGHTS["upfront_cost"]
    deposit = listing.deposit_months
    buyout = listing.furniture_buyout_eur

    if deposit is None and buyout is None:
        return ComponentScore(
            key="upfront_cost",
            score=0.0,
            weight=weight,
            evidence=[_tag("upfront cost unknown", "listing")],
            missing_data=True,
        )

    if deposit is None:
        deposit_score = 1.0
    elif deposit <= 2:
        deposit_score = 1.0
    elif deposit <= 3:
        deposit_score = 0.7
    elif deposit <= 4:
        deposit_score = 0.4
    else:
        deposit_score = 0.2

    if buyout is None:
        buyout_mult = 1.0
    elif buyout <= 500:
        buyout_mult = 1.0
    elif buyout <= 2000:
        buyout_mult = 0.85
    elif buyout <= 5000:
        buyout_mult = 0.6
    else:
        buyout_mult = 0.3

    score = _clamp01(deposit_score * buyout_mult)

    parts: list[str] = []
    if deposit is not None:
        parts.append(f"deposit {deposit:g} months")
    if buyout is not None:
        parts.append(f"furniture buyout €{buyout}")
    head = " + ".join(parts) if parts else "no upfront cost stated"
    return ComponentScore(
        key="upfront_cost",
        score=score,
        weight=weight,
        evidence=[_tag(head, "listing")],
    )


# -----------------------------------------------------------------------------
# §6 composition.
# -----------------------------------------------------------------------------


def compose(
    components: list[ComponentScore],
    *,
    veto: Optional[VetoResult] = None,
    extra_match_reasons: Optional[list[str]] = None,
    extra_mismatch_reasons: Optional[list[str]] = None,
) -> EvaluationResult:
    """Combine components into one `EvaluationResult`.

    `extra_*_reasons` let the orchestrator fold in the LLM's
    `green_flags` / `red_flags` without re-running vibe.
    """
    if veto is not None:
        return EvaluationResult(
            score=0.0,
            components=components,
            veto_reason=veto.reason,
            summary=f"Rejected: {veto.reason}",
            match_reasons=[],
            mismatch_reasons=[veto.reason],
            match_score=0.0,
            quality_score=0.0,
        )

    quality_component = next(
        (c for c in components if c.key == "quality"), None
    )
    quality_score = (
        _clamp01(quality_component.score)
        if quality_component is not None
        else 0.0
    )

    live = [
        c
        for c in components
        if c.key != "quality" and not c.missing_data and c.weight > 0
    ]
    weight_total = sum(c.weight for c in live)
    if weight_total <= 0:
        return EvaluationResult(
            score=0.0,
            components=components,
            veto_reason=None,
            summary="No data to score",
            match_reasons=[],
            mismatch_reasons=["no evaluable components"],
            match_score=0.0,
            quality_score=quality_score,
        )

    weighted = sum(c.score * c.weight for c in live)
    raw = weighted / weight_total

    # Caps from non-missing components only (defensive — see MATCHER.md §9).
    cap_candidates: list[tuple[float, ComponentScore]] = [
        (c.hard_cap, c)
        for c in components
        if c.hard_cap is not None and not c.missing_data
    ]
    cap_source: Optional[CapSource] = None
    if cap_candidates:
        cap_value, cap_component = min(cap_candidates, key=lambda x: x[0])
        capped = min(raw, cap_value)
        if capped < raw:
            cap_source = CapSource(
                component_key=cap_component.key,
                cap=cap_value,
                reason=_extract_cap_reason(cap_component),
            )
    else:
        capped = raw
    match_score = _clamp01(capped)

    final = _clamp01(
        (1.0 - QUALITY_BLEND_WEIGHT) * match_score
        + QUALITY_BLEND_WEIGHT * quality_score
    )

    match_reasons, mismatch_reasons = _reasons_from_components(components)
    if extra_match_reasons:
        match_reasons.extend(_tag(r, "llm") for r in extra_match_reasons[:3])
    if extra_mismatch_reasons:
        mismatch_reasons.extend(_tag(r, "llm") for r in extra_mismatch_reasons[:3])
    match_reasons = match_reasons[:9]
    mismatch_reasons = mismatch_reasons[:9]

    summary = _summary_from_components(
        final, components, cap_source
    )

    return EvaluationResult(
        score=final,
        components=components,
        veto_reason=None,
        summary=summary,
        match_reasons=match_reasons,
        mismatch_reasons=mismatch_reasons,
        match_score=match_score,
        quality_score=quality_score,
        cap_source=cap_source,
    )


def _extract_cap_reason(component: ComponentScore) -> str:
    """Pull the `cap reason: …` evidence tail emitted by the component, if any."""
    for evidence in reversed(component.evidence):
        # Strip provenance tag for the search.
        text = re.sub(r"\s*\[[^\]]+\]\s*$", "", evidence)
        if text.startswith("cap reason: "):
            return text.removeprefix("cap reason: ")
    return component.key  # fallback


def _reasons_from_components(
    components: list[ComponentScore],
) -> tuple[list[str], list[str]]:
    """High and low headlines per MATCHER.md §10.

    A live component scoring `≥ 0.7` contributes to `match_reasons`;
    `≤ 0.3` to `mismatch_reasons`. Missing-data components are silent.

    Middle-band fallback: when neither bucket is filled, fall back to
    the top-2 (resp. bottom-2) live components' `evidence[0]` so the
    drawer for a 0.55-scoring listing still has something to say.
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

    if not match or not mismatch:
        live = [c for c in components if not c.missing_data and c.evidence]
        sorted_by_score = sorted(live, key=lambda c: c.score, reverse=True)
        if not match:
            match = [c.evidence[0] for c in sorted_by_score[:2]]
        if not mismatch:
            mismatch = [c.evidence[0] for c in reversed(sorted_by_score[-2:])]

    return match[:6], mismatch[:6]


_COMPONENT_LABELS: dict[str, str] = {
    "price": "price",
    "size": "size",
    "wg_size": "WG size",
    "availability": "availability",
    "commute": "commute",
    "preferences": "preferences",
    "vibe": "vibe",
    "tenancy": "tenancy",
    "upfront_cost": "upfront cost",
    "quality": "quality",
}


def _component_label(key: str) -> str:
    return _COMPONENT_LABELS.get(key, key.replace("_", " "))


def _summary_from_components(
    final: float,
    components: list[ComponentScore],
    cap_source: Optional[CapSource],
) -> str:
    live = [c for c in components if not c.missing_data and c.key != "quality"]
    if not live:
        return f"Scored {_pct(final)} (no data)"
    top_positive = max(live, key=lambda c: c.score)
    top_negative = min(live, key=lambda c: c.score)
    bits: list[str] = []
    if top_positive.evidence and top_positive.score >= 0.7:
        bits.append(
            f"strong {_component_label(top_positive.key)} fit: "
            f"{top_positive.evidence[0]}"
        )
    if (
        top_negative.evidence
        and top_negative.score <= 0.4
        and top_negative.key != top_positive.key
    ):
        bits.append(
            f"weak {_component_label(top_negative.key)} fit: "
            f"{top_negative.evidence[0]}"
        )
    if cap_source is not None:
        bits.append(
            f"capped at {cap_source.cap:.2f} by {cap_source.reason}"
        )
    detail = "; ".join(bits) if bits else "mixed fit across components"
    return f"Score {_pct(final)}: {detail}"


def breakdown_detail(components: list[ComponentScore]) -> Optional[str]:
    """Compact one-line `evaluate` action `detail` field used in the SSE log."""
    live = [c for c in components if not c.missing_data]
    if not live:
        return None
    return " · ".join(f"{c.key} {_pct(c.score)}" for c in live)


# -----------------------------------------------------------------------------
# Top-level facade.
# -----------------------------------------------------------------------------


async def evaluate(
    listing: Listing,
    profile: SearchProfile,
    *,
    travel_times: Optional[dict[tuple[str, str], int]] = None,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
    market_context: Optional[MarketContext] = None,
) -> EvaluationResult:
    """End-to-end evaluation per MATCHER.md.

    Calling order is deterministic:
      1. `hard_filter` — short-circuit on veto.
      2. `vibe_fit` (when the user has §3.4 prefs) — populates side
         channels for tenancy / quality / preferences.
      3. All deterministic components.
      4. `compose` — weighted mean + caps + quality blend.
    """
    veto = hard_filter(listing, profile, travel_times=travel_times)
    if veto is not None:
        return compose([], veto=veto)

    tt = travel_times or {}

    # Vibe runs first when we need its side channels (§3.4 LLM prefs).
    llm_keys = _llm_pref_keys_from_profile(profile)
    if llm_keys:
        vibe_outcome = await vibe_fit(
            listing,
            profile,
            nearby_places=nearby_places,
            soft_signal_keys=llm_keys,
        )
    else:
        vibe_outcome = await vibe_fit(
            listing,
            profile,
            nearby_places=nearby_places,
            soft_signal_keys=[],
        )

    components: list[ComponentScore] = [
        price_fit(listing, profile, market_context=market_context),
        size_fit(listing, profile),
        wg_size_fit(listing, profile),
        availability_fit(listing, profile),
        commute_fit(listing, profile, tt),
        preference_fit(
            listing,
            profile,
            nearby_places,
            soft_signal_scores=vibe_outcome.soft_signal_scores,
        ),
        tenancy_fit(listing, profile, tenancy_label=vibe_outcome.tenancy_label),
        upfront_cost_fit(listing),
        vibe_outcome.component,
        quality_fit(
            listing,
            scam_severity=vibe_outcome.scam_severity,
            tenancy_label=vibe_outcome.tenancy_label,
        ),
    ]

    return compose(
        components,
        extra_match_reasons=vibe_outcome.green_flags,
        extra_mismatch_reasons=vibe_outcome.red_flags,
    )


__all__ = [
    "COMPONENT_WEIGHTS",
    "COMMUTE_CAP_MULT",
    "COMMUTE_HARD_CAP",
    "COMMUTE_VETO_MULT",
    "DEFAULT_COMMUTE_BUDGET_MIN",
    "EvaluationResult",
    "CapSource",
    "PRICE_VETO_MULT",
    "PREF_HARD_CAP_WEIGHT4",
    "PREF_HARD_CAP_WEIGHT5",
    "PREF_HARD_CAP_WEIGHT5_UNK",
    "QUALITY_BLEND_WEIGHT",
    "SCAM_VIBE_CAP_THRESHOLD",
    "SCAM_VIBE_HARD_CAP",
    "VetoResult",
    "VibeOutcome",
    "availability_fit",
    "breakdown_detail",
    "commute_fit",
    "compose",
    "evaluate",
    "hard_filter",
    "preference_fit",
    "price_fit",
    "quality_fit",
    "size_fit",
    "tenancy_fit",
    "upfront_cost_fit",
    "vibe_fit",
    "wg_size_fit",
]
