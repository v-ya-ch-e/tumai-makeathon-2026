"""OpenAI-powered reasoning for the WG-Gesucht agent.

Four responsibilities:
  1. `score_listing`   -> legacy: one-shot LLM score across all axes (orchestrator).
  1b. `vibe_score`     -> narrow prose-only score used by `evaluator.py`.
  2. `draft_message`   -> the first message we send to the landlord.
  3. `classify_reply`  -> what does the landlord's answer mean? What should we do next?

All use the OpenAI Chat Completions API with JSON output. We keep prompts
short (hackathon budget) but specific.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional
from urllib.parse import urlparse

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from .models import (
    ContactInfo,
    Listing,
    NearbyPlace,
    ReplyAnalysis,
    ReplyIntent,
    SearchProfile,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Matcher v2 (MATCHER.md §5.8): the vibe LLM model is env-driven so CI /
# prod can pin different snapshots without code changes. Defaults to
# `gpt-5.4-nano` per the design's model recommendation; override with
# WG_VIBE_MODEL=gpt-5-nano (or any other id) when needed.
VIBE_MODEL = os.getenv("WG_VIBE_MODEL", "gpt-5.4-nano")


def _base_url() -> Optional[str]:
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not base_url:
        return None
    host = (urlparse(base_url).hostname or "").lower()
    if host in {"127.0.0.1", "0.0.0.0", "localhost"}:
        logger.warning(
            "Ignoring OPENAI_BASE_URL=%s because it points to a local endpoint.",
            base_url,
        )
        return None
    return base_url


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Put it in .env or export it in the shell."
        )
    base_url = _base_url()
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


_MODE_LABELS = {"DRIVE": "car", "BICYCLE": "bike", "TRANSIT": "transit"}


def _commute_block(
    travel_times: dict[tuple[str, str], int],
    main_locations: list,
) -> str:
    """Format travel_times into the 'Commute times' block. Empty-string on
    empty input so the caller can drop the line entirely.

    When a location carries `max_commute_minutes`, the budget is rendered
    inline as `(max N min)` so the LLM can compare it to the fastest mode.
    """
    if not travel_times or not main_locations:
        return ""
    lines: list[str] = []
    for loc in main_locations:
        per_mode = [
            (mode, secs)
            for (pid, mode), secs in travel_times.items()
            if pid == loc.place_id
        ]
        if not per_mode:
            continue
        per_mode.sort(key=lambda item: item[1])
        rendered = ", ".join(
            f"{_MODE_LABELS.get(mode, mode.lower())} {round(secs / 60)} min"
            for mode, secs in per_mode
        )
        budget = getattr(loc, "max_commute_minutes", None)
        header = f"- {loc.label} (place_id {loc.place_id}"
        if isinstance(budget, int):
            header += f", max {budget} min"
        header += ")"
        lines.append(f"{header}: {rendered}")
    if not lines:
        return ""
    return "\n".join(["Commute times (one-way):", *lines])


def _nearby_places_block(
    nearby_places: dict[str, NearbyPlace],
    preferences: list,
) -> str:
    """Render nearby place facts in the same order as the user's preferences."""
    if not nearby_places or not preferences:
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    for pref in preferences:
        if pref.key in seen:
            continue
        seen.add(pref.key)
        item = nearby_places.get(pref.key)
        if item is None:
            continue
        if not item.searched:
            lines.append(f"- {item.label}: lookup unavailable")
            continue
        if item.distance_m is None:
            lines.append(f"- {item.label}: none found within 2 km")
            continue
        place_name = item.place_name or "nearest match"
        lines.append(f"- {item.label}: {place_name}, {item.distance_m} m away")
    if not lines:
        return ""
    return "\n".join(["Nearby preference places:", *lines])


def _listing_summary(
    listing: Listing,
    *,
    travel_times: Optional[dict[tuple[str, str], int]] = None,
    main_locations: Optional[list] = None,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
    preferences: Optional[list] = None,
) -> str:
    parts = [
        f"ID: {listing.id}",
        f"Title: {listing.title}",
        f"City/district: {listing.city or '?'} / {listing.district or '?'}",
        f"Rent: {listing.price_eur} €" if listing.price_eur else "Rent: ?",
        f"Size: {listing.size_m2} m²" if listing.size_m2 else "Size: ?",
        f"WG size: {listing.wg_size}er" if listing.wg_size else "WG size: ?",
        f"Available from: {listing.available_from}" if listing.available_from else "",
        f"Available until: {listing.available_to}" if listing.available_to else "",
        f"Languages: {', '.join(listing.languages)}" if listing.languages else "",
        f"Furnished: {listing.furnished}" if listing.furnished is not None else "",
    ]
    if travel_times:
        block = _commute_block(travel_times, list(main_locations or []))
        if block:
            parts.append(block)
    if nearby_places:
        block = _nearby_places_block(nearby_places, list(preferences or []))
        if block:
            parts.append(block)
    if listing.description:
        parts.append("Description (truncated):")
        parts.append(listing.description[:1800])
    return "\n".join(p for p in parts if p)


def _preferences_block(req: SearchProfile) -> str:
    """Render weighted preferences as a single line for the prompt.

    Returns empty string when the user has not picked any preferences so
    the caller can omit the line entirely.
    """
    if not req.preferences:
        return ""
    items = ", ".join(f"{p.key} ({p.weight})" for p in req.preferences)
    return f"Preferences (1=nice, 5=must-have): {items}"


def _requirements_summary(req: SearchProfile) -> str:
    parts = [
        f"City: {req.city}",
        f"Rent: {req.min_rent_eur}–{req.max_rent_eur} €",
        f"Size: {req.min_size_m2}–{req.max_size_m2} m²",
        f"WG size: {req.min_wg_size}–{req.max_wg_size}",
        f"Rent type: {req.rent_type.name}",
        f"Move in from: {req.move_in_from or 'ASAP'}",
        f"Move in until: {req.move_in_until or 'flexible'}",
        f"Preferred districts: {', '.join(req.preferred_districts) or '—'}",
        f"Avoid districts: {', '.join(req.avoid_districts) or '—'}",
        f"Languages: {', '.join(req.languages)}",
        f"Furnished preference: {req.furnished if req.furnished is not None else 'no preference'}",
        f"Notes: {req.notes or '—'}",
    ]
    pref_line = _preferences_block(req)
    if pref_line:
        parts.append(pref_line)
    return "\n".join(parts)


def _profile_summary(p: ContactInfo) -> str:
    return "\n".join(
        [
            f"Name: {p.first_name} {p.last_name}".strip(),
            f"Age: {p.age}",
            f"Gender: {p.gender.value}",
            f"Email: {p.email}",
            f"Phone: {p.phone or '—'}",
            f"Occupation: {p.occupation}",
            f"Languages: {', '.join(p.languages)}",
            f"Bio: {p.bio}",
        ]
    )


# -----------------------------------------------------------------------------
# 1. Score a listing against the student's requirements
# -----------------------------------------------------------------------------

SCORE_SYSTEM = (
    "You help a university student filter WG-Gesucht listings against their "
    "requirements. You are strict: reject anything clearly off-spec. Output JSON."
)

SCORE_USER_TEMPLATE = """
Rate this listing against the student's requirements on a 0..1 scale.

REQUIREMENTS:
{requirements}

LISTING:
{listing}

Use this scoring shape for numeric fit: cheaper, bigger, and closer are
better. Scores should stay fairly forgiving up to the user's cutoff, but once
a listing crosses that cutoff the penalty should accelerate sharply instead of
dropping linearly. For size, use the mirrored rule: being below the preferred
size should hurt quickly, while anything at or above it is good.

If the "Commute times" section is present, treat commutes over 40 minutes as
strong negatives and under 20 minutes as positives. Do not invent commute
times that aren't listed. When a location shows "(max N min)", treat any
fastest-mode time above that budget as a strong negative; comfortably under
it is a positive.

If a "Preferences" line is present, each item is tagged with a 1..5 weight:
  * weight 5 = must-have: if the listing clearly lacks it, cap the score at 0.4.
  * weight 4 = important: missing it is a notable negative.
  * weight 3 = neutral / nice-to-have.
  * weight 1-2 = mild bonus when present, minor if missing.
Do not invent features the listing does not mention.

Return JSON with these keys:
  score (0..1, higher = better match),
  reason (one sentence),
  match_reasons (list of short strings),
  mismatch_reasons (list of short strings).

Only return valid JSON, no prose.
"""


def score_listing(
    listing: Listing,
    requirements: SearchProfile,
    *,
    travel_times: Optional[dict[tuple[str, str], int]] = None,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
) -> Listing:
    """Ask the LLM to rate `listing` against `requirements`. Mutates + returns listing.

    When `travel_times` is provided, the prompt includes a per-main-location
    commute block keyed by `(place_id, mode) -> seconds`. The LLM is told to
    treat long commutes as soft negatives.

    NOTE: The v1 find loop goes through `evaluator.evaluate` instead; this
    function is kept for the older `orchestrator.py` code path (non-v1) and
    for ad-hoc scripts that want a single LLM-composed score.
    """
    client = _client()
    user_msg = SCORE_USER_TEMPLATE.format(
        requirements=_requirements_summary(requirements),
        listing=_listing_summary(
            listing,
            travel_times=travel_times,
            main_locations=requirements.main_locations,
            nearby_places=nearby_places,
            preferences=requirements.preferences,
        ),
    )
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": SCORE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = response.choices[0].message.content or "{}"
    try:
        data: dict[str, Any] = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON, falling back to heuristic: %s", content)
        data = {}

    score = float(data.get("score", 0.0) or 0.0)
    listing.score = max(0.0, min(1.0, score))
    listing.score_reason = str(data.get("reason", ""))[:400] or None
    listing.match_reasons = [str(x) for x in (data.get("match_reasons") or [])][:6]
    listing.mismatch_reasons = [str(x) for x in (data.get("mismatch_reasons") or [])][:6]
    return listing


# -----------------------------------------------------------------------------
# 1b. Vibe-only judgement (one component of the v2 scorecard evaluator)
# -----------------------------------------------------------------------------


class VibeJudgement(BaseModel):
    """Strict JSON output of the vibe LLM (MATCHER.md §5.8).

    Single shot per listing. The LLM is told to judge prose-only signals
    (vibe, flatmate fit, lifestyle) and to NEVER score price / size /
    WG-size / commute (those are deterministic in `evaluator.py`).

    Side channels feed back into other components:
      * `soft_signal_scores` -> `preferences_fit` per-key resolution for
        the §3.4 LLM-resolved keys (lgbt_friendly, english_speaking, ...).
      * `tenancy_label`     -> `tenancy_fit` when the listing has no
        explicit `available_to`.
      * `scam_severity`     -> drives the `vibe_fit` hard-cap at 0.30
        (§9 row 6) and feeds `quality_fit`.

    Backward-compat alias: the legacy `score` / `evidence` fields from the
    old `VibeScore` are mirrored as properties so any caller that still
    references them by the old names keeps working.
    """

    fit_score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    flatmate_vibe: str = ""
    lifestyle_match: str = ""
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    soft_signal_scores: dict[str, float] = Field(default_factory=dict)
    tenancy_label: str = "unknown"
    scam_severity: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_score(cls, data):  # type: ignore[override]
        """Accept the v1 `{score, evidence}` shape for backward compat.

        Older test fixtures and the legacy `VibeScore` callers still pass
        `score=...`. We mirror it onto `fit_score` so the new schema
        validates without forcing every caller to rename the field.
        """
        if isinstance(data, dict) and "fit_score" not in data and "score" in data:
            data = {**data, "fit_score": data["score"]}
        return data

    @property
    def score(self) -> float:
        """Legacy alias used by older `vibe_score` callers."""
        return self.fit_score


# Kept as an import alias so any in-tree code that still imports
# `VibeScore` continues to type-check. New code should use
# `VibeJudgement` directly.
VibeScore = VibeJudgement


_TENANCY_LABELS = ("open_ended", "long_term", "mid_term", "short_term", "unknown")


VIBE_SYSTEM = (
    "You judge the vibe of a WG-Gesucht listing against a student's free-form "
    "notes, district preferences, and lifestyle preferences. Nearby place "
    "facts can inform lifestyle fit when they clearly matter to the student. "
    "Do NOT judge price, size, WG size, or commute times — those are handled "
    "by other deterministic components. You also extract three side-channel "
    "facts (tenancy_label, scam_severity, per-key soft-signal scores) so the "
    "rest of the engine can reuse your read of the description without a "
    "second call. Output strict JSON, no prose."
)

VIBE_USER_TEMPLATE = """
Rate how well the listing's description and district match the student's vibe
notes on a 0..1 scale, and extract the side-channel facts described below.

STUDENT NOTES:
{notes}

STUDENT DEMOGRAPHICS (for wg_gender / wg_age_band soft signals):
  gender: {self_gender}
  age: {self_age}

PREFERRED DISTRICTS: {preferred_districts}
AVOID DISTRICTS: {avoid_districts}
WEIGHTED PREFERENCES: {preferences}
SOFT-SIGNAL KEYS YOU MUST SCORE (0..1, omit a key when no evidence):
{soft_signal_keys}
NEARBY PREFERENCE PLACES:
{nearby_places}

LISTING DISTRICT: {district}
LISTING DESCRIPTION:
\"\"\"
{description}
\"\"\"

Return JSON with EXACTLY these fields:
  fit_score:           number in [0, 1] (overall prose-vibe match).
  evidence:            list of 1..4 short strings, concrete phrases you used.
  flatmate_vibe:       one short sentence describing the flatmates / household.
  lifestyle_match:     one short sentence linking the listing to the user's lifestyle.
  red_flags:           0..3 short strings naming concrete concerns (e.g. "asks for deposit by transfer to private account").
  green_flags:         0..3 short strings naming concrete positives.
  soft_signal_scores:  object with per-key scores in [0, 1] for the keys listed
                       under SOFT-SIGNAL KEYS above. OMIT a key when the
                       description gives no evidence either way (do not guess).
  tenancy_label:       one of "open_ended" | "long_term" | "mid_term" | "short_term" | "unknown".
                       "open_ended" = "unbefristet" / "permanent" stated.
                       "long_term"  = year-or-more lease implied (e.g. "min. 12 Monate").
                       "mid_term"   = 3-9 months ("Zwischenmiete" without a year).
                       "short_term" = under 3 months / weeks ("4 Wochen Zwischenmiete").
                       "unknown"    = description does not say.
  scam_severity:       number in [0, 1]. 0 = no concern; 1 = obvious scam pattern
                       (off-platform contact required, payment by Western Union,
                       implausibly cheap rent for the district, photos clearly stolen
                       or copy-pasted, urgent pressure to transfer money). Be
                       conservative — only flag with concrete evidence in the text.

Rules:
  * If the student has no notes AND no district preferences AND no nearby-place
    context, return fit_score 0.5 with evidence ["not enough vibe information"]
    and still emit the side-channel facts as best you can from the description.
  * If the listing is in an avoid-district, fit_score <= 0.3 and mention the
    district in evidence.
  * For wg_gender / wg_age_band: only score from EXPLICIT exclusions or matches
    in the description ("nur Frauen-WG", "30+ WG", "Studi-WG"); omit when
    unstated. The student's gender/age above are provided so you can decide
    "matches student" (1.0) vs "explicitly excludes student" (0.0).
  * Do NOT mention rent, size, or commute in the evidence.

Only return valid JSON, no prose.
"""


def vibe_score(
    listing: Listing,
    requirements: SearchProfile,
    *,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
    soft_signal_keys: Optional[list[str]] = None,
) -> VibeJudgement:
    """Run the v2 vibe LLM and return the parsed `VibeJudgement`.

    Raises on HTTP / JSON / ValidationError failure. The evaluator
    catches these and sets `missing_data=True` on the `vibe_fit`
    component (see MATCHER.md §5.8).

    `soft_signal_keys` is the list of preference keys for which the LLM
    should report a per-key score in `soft_signal_scores` (the §3.4
    keys: `student_household`, `couples_ok`, `lgbt_friendly`,
    `english_speaking`, `international_friendly`, `wg_gender`,
    `wg_age_band`). Pass `None` to ask for no soft signals.
    """
    client = _client()
    nearby_block = _nearby_places_block(
        nearby_places or {},
        list(requirements.preferences),
    )
    self_gender = (
        requirements.flatmate_self_gender.value
        if requirements.flatmate_self_gender is not None
        else "—"
    )
    self_age = (
        str(requirements.flatmate_self_age)
        if requirements.flatmate_self_age is not None
        else "—"
    )
    soft_signal_keys = list(soft_signal_keys or [])
    keys_block = (
        "  " + "\n  ".join(soft_signal_keys) if soft_signal_keys else "  (none)"
    )
    user_msg = VIBE_USER_TEMPLATE.format(
        notes=(requirements.notes or "(none)").strip()[:1500],
        self_gender=self_gender,
        self_age=self_age,
        preferred_districts=", ".join(requirements.preferred_districts) or "—",
        avoid_districts=", ".join(requirements.avoid_districts) or "—",
        preferences=", ".join(
            f"{p.key} ({p.weight})" for p in requirements.preferences
        )
        or "—",
        soft_signal_keys=keys_block,
        nearby_places=nearby_block.replace("Nearby preference places:\n", "")
        or "—",
        district=listing.district or "?",
        description=(listing.description or "").strip()[:2000],
    )
    response = client.chat.completions.create(
        model=VIBE_MODEL,
        messages=[
            {"role": "system", "content": VIBE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    judgement = VibeJudgement.model_validate_json(content)
    # Defensive: clamp scam_severity and soft_signal_scores into [0, 1]
    # so a misbehaving LLM cannot poison downstream caps.
    judgement.scam_severity = max(0.0, min(1.0, float(judgement.scam_severity)))
    judgement.soft_signal_scores = {
        k: max(0.0, min(1.0, float(v)))
        for k, v in (judgement.soft_signal_scores or {}).items()
        if k in soft_signal_keys
    }
    if judgement.tenancy_label not in _TENANCY_LABELS:
        judgement.tenancy_label = "unknown"
    return judgement


# -----------------------------------------------------------------------------
# 2. Draft the first message to the landlord
# -----------------------------------------------------------------------------

DRAFT_SYSTEM = (
    "You are a friendly, concise German/English assistant that writes WG-Gesucht "
    "intro messages for a university student. Match the language of the listing. "
    "Keep the message under 180 words. Answer the questions the landlord asks in "
    "their description (age, origin, occupation, duration, hobbies). Do not invent "
    "information. Sign off with the student's first name."
)

DRAFT_USER_TEMPLATE = """
Write a first message to the landlord of the following WG-Gesucht listing.

STUDENT PROFILE:
{profile}

LISTING:
{listing}

Guidelines:
  * Be warm but concise.
  * Detect the primary language of the listing description and reply in it. If the
    listing explicitly welcomes English speakers, you may write in English.
  * Briefly mention two concrete reasons why this WG is a good match.
  * Propose that you'd be happy to come for a viewing, but do NOT suggest a date.
  * No emojis. No markdown. Plain text only.
"""


def draft_message(listing: Listing, profile: ContactInfo) -> str:
    """Return plain-text message body, ready to paste into the wg-gesucht form."""
    client = _client()
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": DRAFT_SYSTEM},
            {
                "role": "user",
                "content": DRAFT_USER_TEMPLATE.format(
                    profile=_profile_summary(profile),
                    listing=_listing_summary(listing),
                ),
            },
        ],
        temperature=0.5,
    )
    return (response.choices[0].message.content or "").strip()


# -----------------------------------------------------------------------------
# 3. Classify a landlord reply
# -----------------------------------------------------------------------------

CLASSIFY_SYSTEM = (
    "You classify landlord replies on WG-Gesucht and decide what the student's "
    "autonomous agent should do next. Output strict JSON."
)

CLASSIFY_USER_TEMPLATE = """
The student is looking for a WG room. They sent a message; below is the landlord's
reply. Classify it.

REPLY TEXT (may be in German or English):
\"\"\"
{reply}
\"\"\"

Return JSON:
  intent: one of {intents}
  summary: one sentence summarising the reply
  proposed_times: list of human-readable time strings the landlord proposed (may be empty)
  questions: list of short questions the landlord asked the student
  next_action: one of "accept_viewing", "answer_questions", "drop", "wait"

Rules:
  * If the landlord proposes one or more concrete viewing times → intent=viewing_offer, next_action=accept_viewing.
  * If the landlord asks the student for more info (Alter, Beruf, Einzugsdatum, hobbies) → intent=asks_for_info, next_action=answer_questions.
  * If the listing is "schon vergeben", "already taken", "vermietet" etc. → intent=already_taken, next_action=drop.
  * Polite rejections ("doesn't fit our WG") → intent=polite_decline, next_action=drop.
  * Anything else → intent=unclear or smalltalk, next_action=wait.

Only return valid JSON.
"""


def classify_reply(reply_text: str) -> ReplyAnalysis:
    client = _client()
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {
                "role": "user",
                "content": CLASSIFY_USER_TEMPLATE.format(
                    reply=reply_text.strip()[:4000],
                    intents=", ".join(i.value for i in ReplyIntent),
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    content = response.choices[0].message.content or "{}"
    try:
        return ReplyAnalysis.model_validate_json(content)
    except (ValidationError, ValueError):
        logger.warning("classify_reply: bad JSON, falling back. Raw: %s", content)
        return ReplyAnalysis(
            intent=ReplyIntent.unclear,
            summary=reply_text.strip()[:160],
            next_action="wait",
        )


# -----------------------------------------------------------------------------
# 4. Reply to the landlord (given their questions / proposed times)
# -----------------------------------------------------------------------------

REPLY_SYSTEM = (
    "You are a friendly assistant writing the student's reply on WG-Gesucht. "
    "Keep it short and polite. Match the landlord's language. No emojis, plain text."
)

REPLY_USER_TEMPLATE = """
Write a reply to this landlord message. Mode = {mode}.

STUDENT PROFILE:
{profile}

LISTING:
{listing}

LANDLORD'S MESSAGE:
\"\"\"
{reply}
\"\"\"

Guidelines:
  * If mode == "accept_viewing": thank them, enthusiastically confirm ONE of the
    proposed times (prefer the earliest weekday slot), and share the student's phone
    number so they can coordinate.
  * If mode == "answer_questions": answer their questions using ONLY info from the
    profile and listing. Don't invent.
  * Keep under 120 words.
"""


def reply_to_landlord(
    reply_text: str,
    listing: Listing,
    profile: ContactInfo,
    mode: str,
) -> str:
    client = _client()
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": REPLY_SYSTEM},
            {
                "role": "user",
                "content": REPLY_USER_TEMPLATE.format(
                    mode=mode,
                    profile=_profile_summary(profile),
                    listing=_listing_summary(listing),
                    reply=reply_text.strip()[:4000],
                ),
            },
        ],
        temperature=0.4,
    )
    return (response.choices[0].message.content or "").strip()
