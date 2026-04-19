"""Smoke-test for gpt-5.4-nano via the OpenAI Python client.

Goal: confirm the model name, JSON-mode response_format, and our preferred
schema-validation pattern (Pydantic) all work end-to-end with the live
API key in `../.env`.

Usage (from backend/):
    venv/bin/python scripts/check_openai.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

env_file = pathlib.Path(__file__).resolve().parent.parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from openai import OpenAI  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402


MODEL = os.environ.get("SCRAPER_ENRICH_MODEL", "gpt-5.4-nano")
SAMPLE_DESCRIPTION = """
Helles, ruhiges Zimmer (16 m²) in einer entspannten 3er WG in Schwabing.
Wir sind zwei Studierende (Bio & Informatik) Anfang/Mitte 20, gerne auf
Englisch. Nähe Englischer Garten, Klimastraße ein paar Minuten zu Fuß.
Möbliert, Spülmaschine, Waschmaschine, Balkon. Haustiere leider nicht
möglich, Nichtraucher-Wohnung. Verfügbar ab 1.6.2026 für mindestens
ein Jahr. Miete €620 warm.
"""

SAMPLE_NOTES = """
I'm a master's student at TUM (Informatics), I value a quiet but social
flat where I can study at home. I cook a lot and cycle everywhere. I'd
love to be close to a park or green space and on tram/U-Bahn routes that
reach Garching in under 40 minutes.
"""


class VibeJudgement(BaseModel):
    fit_score: float = Field(ge=0, le=1, description="0=bad fit, 1=perfect fit")
    flatmate_vibe: str = Field(description="One short sentence about the flatmates")
    lifestyle_match: str = Field(description="One sentence linking listing to user's lifestyle")
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)


SYSTEM = (
    "You judge how well a Munich flat-share listing matches a student. "
    "You are evaluating only the lifestyle/vibe fit -- assume price, size, "
    "and commute have already been judged separately. Output strict JSON "
    "matching the schema."
)

USER_TEMPLATE = (
    "Student notes:\n{notes}\n\n"
    "Listing description:\n{description}\n\n"
    "Return JSON with: fit_score (0..1), flatmate_vibe, lifestyle_match, "
    "red_flags (array of short strings), green_flags (array of short strings)."
)


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    print(f"\nModel: {MODEL}\n")

    t0 = time.time()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    notes=SAMPLE_NOTES.strip(),
                    description=SAMPLE_DESCRIPTION.strip(),
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    dt = time.time() - t0
    raw = response.choices[0].message.content or "{}"

    print(f"=== Raw response ({dt:.2f}s) ===")
    print(raw)
    print()

    try:
        parsed = VibeJudgement.model_validate_json(raw)
    except ValidationError as exc:
        print("VALIDATION FAILED:")
        print(exc)
        sys.exit(2)

    print("=== Parsed ===")
    print(json.dumps(parsed.model_dump(), indent=2, ensure_ascii=False))
    usage = response.usage
    if usage is not None:
        print(
            f"\nUsage: prompt={usage.prompt_tokens} "
            f"completion={usage.completion_tokens} "
            f"total={usage.total_tokens}"
        )


if __name__ == "__main__":
    main()
