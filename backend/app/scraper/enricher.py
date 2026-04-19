"""LLM-driven enrichment of scraped listings.

After a source's `scrape_detail` returns, the agent optionally calls
`enrich_listing` to fill missing structured fields on a `Listing`
**only when the description provides them clearly and explicitly**.
Two layers of defense make this safe:

1. The system prompt enumerates the strict "do not infer / do not guess"
   rules and lists every in-scope field with its expected type/format.
2. `ScraperAgent._apply_enrichment` re-checks each returned value: it
   refuses to overwrite a non-null deterministic field, drops values
   whose types do not match the `Listing` schema, and round-trips the
   merged listing through Pydantic validation before mutating the
   in-memory listing.

Coordinates (`lat`, `lng`) are deliberately out of scope: a description
cannot reliably encode coordinates and we already fall back to Google
Geocoding when only a textual address is available.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

from ..wg_agent import brain
from ..wg_agent.models import Listing

logger = logging.getLogger(__name__)


ENRICHABLE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "kind",
        "city",
        "district",
        "address",
        "price_eur",
        "size_m2",
        "wg_size",
        "available_from",
        "available_to",
        "furnished",
        "pets_allowed",
        "smoking_ok",
        "languages",
    }
)


class EnrichmentDiff(BaseModel):
    """Subset of `Listing` fields that the LLM is allowed to fill in.

    Every field is optional — the model omits anything it cannot justify
    from the description. Field types mirror the matching `Listing`
    field exactly; Pydantic rejects mismatches at parse time.
    """

    title: Optional[str] = None
    kind: Optional[Literal["wg", "flat"]] = None
    city: Optional[str] = None
    district: Optional[str] = None
    address: Optional[str] = None
    price_eur: Optional[int] = Field(default=None, ge=0)
    size_m2: Optional[float] = Field(default=None, ge=0)
    wg_size: Optional[int] = Field(default=None, ge=1)
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    furnished: Optional[bool] = None
    pets_allowed: Optional[bool] = None
    smoking_ok: Optional[bool] = None
    languages: Optional[list[str]] = None


ENRICH_SYSTEM_PROMPT = """\
You enrich rental listing metadata. You receive (a) the structured fields
already known about a listing and (b) the free-text description. Return
ONLY a JSON object containing fields that are CLEARLY and EXPLICITLY
stated in the description. Hard rules:

- Do NOT include any field that is already non-null in the structured data.
- Do NOT infer, guess, or interpret. If the description merely hints at a
  value, omit the field. Examples of NOT clearly provided:
    * "Couch is included" -> furnished = unknown (omit)
    * "near a park" -> not a field, omit
    * "ideal for one person" -> wg_size is unknown, omit
- Use the original units / formats: price_eur as integer euros, size_m2
  as float, dates as ISO yyyy-mm-dd, booleans as true/false, languages
  as a list of strings (e.g. ["Deutsch", "English"]).
- Never output coordinates, urls, ids, scores, or any field not in the
  schema below.
- Output an empty object {} if nothing is clearly provided.

Allowed fields (omit any whose value you cannot pin down from the
description with high confidence):
- title (string)
- kind ("wg" or "flat")
- city, district, address (strings)
- price_eur (integer euros, total monthly rent)
- size_m2 (float, square meters)
- wg_size (integer, total residents including the new one; >= 1)
- available_from, available_to (ISO yyyy-mm-dd)
- furnished, pets_allowed, smoking_ok (boolean)
- languages (list of strings, e.g. ["Deutsch", "English"])
"""


def _known_fields_summary(listing: Listing) -> dict[str, object]:
    """Return the structured fields the LLM should treat as 'already known'.

    Only enrichable fields whose value is non-null are included; the LLM is
    explicitly forbidden from overwriting them, and the prompt repeats the
    list so the rule is visible in two places.
    """
    out: dict[str, object] = {}
    for field in ENRICHABLE_FIELDS:
        value = getattr(listing, field, None)
        if value is None:
            continue
        if isinstance(value, date):
            out[field] = value.isoformat()
        else:
            out[field] = value
    return out


def _build_user_prompt(listing: Listing) -> str:
    known = _known_fields_summary(listing)
    description = (listing.description or "").strip()
    return (
        "Already-known structured fields (do NOT overwrite or repeat any of these):\n"
        f"{json.dumps(known, ensure_ascii=False, sort_keys=True)}\n\n"
        "Listing description:\n"
        f"{description}\n\n"
        "Return ONLY a JSON object with the in-scope fields you can pin "
        "down from the description with high confidence. Omit everything else."
    )


def enrich_listing(
    listing: Listing,
    *,
    model: str,
    client: Optional[OpenAI] = None,
) -> EnrichmentDiff:
    """Call the LLM and return its parsed diff.

    Raises on HTTP / JSON / validation errors so the caller can decide
    whether to log and continue or back off. The function never mutates
    `listing`.
    """
    api_client = client if client is not None else brain._client()
    response = api_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ENRICH_SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(listing)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    content = response.choices[0].message.content or "{}"
    return EnrichmentDiff.model_validate_json(content)
