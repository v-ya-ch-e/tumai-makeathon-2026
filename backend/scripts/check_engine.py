"""End-to-end smoke run for the v2 evaluator against live data.

Pulls 10 real Munich `ListingRow`s from the shared AWS RDS DB, builds
a synthetic-but-realistic `SearchProfile` (TUM student, 900€ cap,
35-min commute, a few preferences), runs the engine end-to-end with
real Google Maps + OpenAI calls, and prints a side-by-side ranking
against the legacy `brain.score_listing` baseline so a human can
eyeball whether v2 is regressing.

Usage (from backend/):
    venv/bin/python scripts/check_engine.py

Reads `OPENAI_API_KEY`, `GOOGLE_MAPS_SERVER_KEY`, and `DB_*` from
`../.env`. Costs are tiny: ~10 OpenAI vibe calls (~$0.01) plus ~10
Distance Matrix + ~30 Places calls (Google Maps free tier).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from datetime import date

env_file = pathlib.Path(__file__).resolve().parent.parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.wg_agent import (  # noqa: E402
    brain,
    commute,
    db as db_module,
    evaluator,
    market,
    places,
    repo,
)
from app.wg_agent.db_models import ListingRow  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
)


PROFILE = SearchProfile(
    city="München",
    max_rent_eur=900,
    min_rent_eur=300,
    min_size_m2=12,
    max_size_m2=30,
    min_wg_size=2,
    max_wg_size=5,
    main_locations=[
        PlaceLocation(
            label="TUM Hauptcampus",
            place_id="ChIJi4WMoxenthQRMBbHFWKBGMY",
            lat=48.14948,
            lng=11.56797,
            max_commute_minutes=35,
        )
    ],
    has_car=False,
    has_bike=True,
    mode="wg",
    preferences=[
        PreferenceWeight(key="public_transport", weight=5),
        PreferenceWeight(key="supermarket", weight=4),
        PreferenceWeight(key="park", weight=3),
        PreferenceWeight(key="quiet_area", weight=3),
        PreferenceWeight(key="english_speaking", weight=4),
    ],
    move_in_from=date(2026, 9, 1),
    move_in_until=date(2026, 10, 31),
    notes=(
        "Master's student at TUM (Informatics). I cook a lot, value a quiet "
        "but social flat, cycle everywhere. Would love access to a park or "
        "Grünfläche and English-speaking flatmates."
    ),
    desired_min_months=12,
    flatmate_self_age=24,
)


async def run_listing(listing) -> tuple[evaluator.EvaluationResult, float]:
    """Score one listing through the v2 engine. Returns (v2_result, baseline_score)."""
    travel_times: dict[tuple[str, str], int] = {}
    nearby_places: dict = {}
    market_context = None

    if listing.lat is not None and listing.lng is not None:
        travel_times = await commute.travel_times(
            origin=(listing.lat, listing.lng),
            destinations=PROFILE.main_locations,
            modes=commute.modes_for(PROFILE),
        )
        nearby_places = await places.nearby_places(
            origin=(listing.lat, listing.lng),
            preferences=PROFILE.preferences,
        )

    with Session(db_module.engine) as session:
        market_context = market.market_context(
            session,
            listing_id=listing.id,
            district=listing.district,
            kind=listing.kind,
            size_m2=listing.size_m2,
            price_eur=listing.price_eur,
        )

    v2 = await evaluator.evaluate(
        listing,
        PROFILE,
        travel_times=travel_times,
        nearby_places=nearby_places,
        market_context=market_context,
    )

    # Baseline: the v0 single-LLM-call scorer.
    try:
        baseline_listing = brain.score_listing(
            listing,
            PROFILE,
            travel_times=travel_times,
            nearby_places=nearby_places,
        )
        baseline = float(baseline_listing.score or 0.0)
    except Exception as exc:  # noqa: BLE001
        print(f"  baseline failed: {exc}")
        baseline = float("nan")

    return v2, baseline


async def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)
    if not os.environ.get("GOOGLE_MAPS_SERVER_KEY"):
        print("ERROR: GOOGLE_MAPS_SERVER_KEY not set")
        sys.exit(1)

    db_module.init_db()
    print(f"Database: {db_module.describe_database()}\n")

    with Session(db_module.engine) as session:
        rows = list(
            session.exec(
                select(ListingRow)
                .where(ListingRow.scrape_status == "full")
                .where(ListingRow.lat.is_not(None))   # type: ignore[union-attr]
                .where(ListingRow.lng.is_not(None))   # type: ignore[union-attr]
                .where(ListingRow.kind == "wg")
                .order_by(ListingRow.last_seen_at.desc())
                .limit(10)
            )
        )

    if not rows:
        print("No fully-scraped Munich WG listings in the DB. Nothing to do.")
        return

    print(f"Scoring {len(rows)} listings end-to-end. v2 vs v0 baseline:\n")

    rankings: list[tuple[str, float, float, evaluator.EvaluationResult]] = []
    for row in rows:
        listing = repo.row_to_domain_listing(row)
        title = (listing.title or listing.id)[:60]
        print(f"… {title}")
        v2, baseline = await run_listing(listing)
        rankings.append((title, v2.score, baseline, v2))

    rankings.sort(key=lambda x: x[1], reverse=True)

    print("\n=== v2 ranking (with baseline shown for comparison) ===\n")
    print(f"{'rank':>4} {'v2':>6} {'v0':>6}  title")
    for rank, (title, v2_score, baseline, _res) in enumerate(rankings, 1):
        print(f"{rank:>4d} {v2_score:>6.2%} {baseline:>6.2%}  {title}")

    print("\n=== top-3 component breakdown ===\n")
    for rank, (title, _v2_score, _baseline, res) in enumerate(rankings[:3], 1):
        print(f"#{rank} {title}")
        print(f"    summary: {res.summary}")
        print(f"    match  : {res.match_score:.2%}")
        print(f"    quality: {res.quality_score:.2%}")
        if res.cap_source is not None:
            print(
                f"    cap    : {res.cap_source.cap:.2f} "
                f"({res.cap_source.component_key}: {res.cap_source.reason})"
            )
        for c in res.components:
            tag = "MISS" if c.missing_data else f"{c.score:.2f}"
            print(f"    - {c.key:<14} {tag:<6} weight={c.weight:.1f}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
