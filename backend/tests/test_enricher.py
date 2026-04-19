"""Unit tests for the LLM enrichment helper.

`enrich_listing` is mocked at the OpenAI-client boundary: we never make
a real network call. The tests verify (1) the prompt sent contains the
description and the already-known fields, (2) `enrich_listing` returns
the parsed `EnrichmentDiff`, and (3) the function never mutates its
input listing.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime
from unittest.mock import MagicMock

from pydantic import HttpUrl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.scraper.enricher import (  # noqa: E402
    ENRICHABLE_FIELDS,
    EnrichmentDiff,
    enrich_listing,
)
from app.wg_agent.models import Listing  # noqa: E402


def _make_listing() -> Listing:
    return Listing(
        id="kleinanzeigen:42",
        url=HttpUrl("https://www.kleinanzeigen.de/x"),
        title="Sonniges Zimmer in Schwabing",
        kind="wg",
        city="München",
        district="Schwabing",
        price_eur=900,
        size_m2=18.0,
        wg_size=None,
        furnished=None,
        pets_allowed=None,
        smoking_ok=None,
        languages=[],
        description=(
            "Wir sind eine 3er-WG in Schwabing. Das Zimmer ist möbliert und "
            "verfügbar ab 01.05.2026."
        ),
    )


def _fake_client(payload: dict) -> MagicMock:
    """Return a MagicMock shaped like an `openai.OpenAI` instance whose
    `chat.completions.create` returns a response carrying `payload` as
    its JSON content."""
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    client.chat.completions.create.return_value = response
    return client


def test_enrich_listing_returns_parsed_diff() -> None:
    listing = _make_listing()
    client = _fake_client({"furnished": True, "wg_size": 3})

    diff = enrich_listing(listing, model="gpt-4o-mini", client=client)

    assert isinstance(diff, EnrichmentDiff)
    assert diff.furnished is True
    assert diff.wg_size == 3
    # Other fields are absent → None.
    assert diff.price_eur is None


def test_enrich_listing_does_not_mutate_input() -> None:
    listing = _make_listing()
    snapshot = listing.model_dump()
    client = _fake_client({"furnished": True, "wg_size": 3})

    enrich_listing(listing, model="gpt-4o-mini", client=client)

    assert listing.model_dump() == snapshot


def test_enrich_listing_prompt_includes_description_and_known_fields() -> None:
    listing = _make_listing()
    client = _fake_client({})

    enrich_listing(listing, model="gpt-4o-mini", client=client)

    call_kwargs = client.chat.completions.create.call_args.kwargs
    messages = call_kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert listing.description in user_msg
    # Already-known structured fields appear in the prompt's "do not
    # overwrite" block. price_eur and city are non-null, so both must
    # appear.
    assert '"price_eur": 900' in user_msg
    assert '"city": "M\\u00fcnchen"' in user_msg or "München" in user_msg
    # Null fields must NOT appear in the known-fields block.
    assert '"furnished"' not in user_msg.split("Listing description")[0]


def test_enrich_listing_uses_json_response_format_and_zero_temperature() -> None:
    listing = _make_listing()
    client = _fake_client({})

    enrich_listing(listing, model="gpt-4o-mini", client=client)

    call_kwargs = client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["temperature"] == 0.0
    assert call_kwargs["model"] == "gpt-4o-mini"


def test_enrichable_fields_match_diff_schema() -> None:
    """Sanity: every key the agent treats as enrichable must exist on
    the `EnrichmentDiff` schema."""
    diff_keys = set(EnrichmentDiff.model_fields.keys())
    assert ENRICHABLE_FIELDS == diff_keys


def test_enrichment_diff_rejects_negative_wg_size() -> None:
    """Schema-level guardrail: bad numeric values are rejected at parse
    time so they never reach `_apply_enrichment`."""
    import pytest

    with pytest.raises(Exception):
        EnrichmentDiff.model_validate({"wg_size": -1})


def test_enrichment_diff_accepts_iso_dates() -> None:
    diff = EnrichmentDiff.model_validate({"available_from": "2026-05-01"})
    assert diff.available_from is not None
    assert diff.available_from.isoformat() == "2026-05-01"
