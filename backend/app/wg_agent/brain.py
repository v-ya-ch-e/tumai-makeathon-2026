"""OpenAI-powered reasoning for the WG-Gesucht agent.

Three responsibilities:
  1. `score_listing`   -> is this listing a match for the student's requirements?
  2. `draft_message`   -> the first message we send to the landlord.
  3. `classify_reply`  -> what does the landlord's answer mean? What should we do next?

All three use the OpenAI Chat Completions API with JSON output. We keep prompts
short (hackathon budget) but specific.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from openai import OpenAI
from pydantic import ValidationError

from .models import (
    ContactInfo,
    Listing,
    ReplyAnalysis,
    ReplyIntent,
    SearchProfile,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Put it in .env or export it in the shell."
        )
    return OpenAI(api_key=api_key)


_MODE_LABELS = {"DRIVE": "car", "BICYCLE": "bike", "TRANSIT": "transit"}


def _commute_block(
    travel_times: dict[tuple[str, str], int],
    main_locations: list,
) -> str:
    """Format travel_times into the 'Commute times' block. Empty-string on
    empty input so the caller can drop the line entirely."""
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
        lines.append(f"- {loc.label} (place_id {loc.place_id}): {rendered}")
    if not lines:
        return ""
    return "\n".join(["Commute times (one-way):", *lines])


def _listing_summary(
    listing: Listing,
    *,
    travel_times: Optional[dict[tuple[str, str], int]] = None,
    main_locations: Optional[list] = None,
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
    if listing.description:
        parts.append("Description (truncated):")
        parts.append(listing.description[:1800])
    return "\n".join(p for p in parts if p)


def _requirements_summary(req: SearchProfile) -> str:
    return "\n".join(
        [
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
    )


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

If the "Commute times" section is present, treat commutes over 40 minutes as
strong negatives and under 20 minutes as positives. Do not invent commute
times that aren't listed.

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
) -> Listing:
    """Ask the LLM to rate `listing` against `requirements`. Mutates + returns listing.

    When `travel_times` is provided, the prompt includes a per-main-location
    commute block keyed by `(place_id, mode) -> seconds`. The LLM is told to
    treat long commutes as soft negatives.
    """
    client = _client()
    user_msg = SCORE_USER_TEMPLATE.format(
        requirements=_requirements_summary(requirements),
        listing=_listing_summary(
            listing,
            travel_times=travel_times,
            main_locations=requirements.main_locations,
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
