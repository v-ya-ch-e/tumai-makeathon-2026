"""HuntEngine / PeriodicHunter behavior (in-memory DB, mocked network + LLM).

Post-ADR-018 the matcher reads the global ListingRow pool and never calls
`browser.anonymous_search`. Tests pre-seed the pool via `upsert_global_listing`
before the matcher runs.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from pydantic import HttpUrl
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("WG_SECRET_KEY", Fernet.generate_key().decode())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import db as db_module, repo  # noqa: E402
from app.wg_agent.db_models import HuntRow  # noqa: E402
from app.wg_agent.db_models import ListingScoreRow  # noqa: E402
from app.wg_agent.evaluator import EvaluationResult  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    ActionKind,
    ComponentScore,
    Gender,
    HuntStatus,
    Listing,
    NearbyPlace,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
    UserProfile,
)
from app.wg_agent.periodic import HuntEngine, PeriodicHunter  # noqa: E402


def _stub_result(score: float, summary: str = "ok") -> EvaluationResult:
    return EvaluationResult(
        score=score,
        components=[
            ComponentScore(
                key="price", score=score, weight=1.0, evidence=["stub"]
            )
        ],
        veto_reason=None,
        summary=summary,
        match_reasons=[],
        mismatch_reasons=[],
    )


def _seed_listings(
    session: Session,
    ids: tuple[str, ...],
    *,
    lat: float | None = None,
    lng: float | None = None,
) -> None:
    """Write the given listings into the global pool as fully-scraped rows."""
    for lid in ids:
        repo.upsert_global_listing(
            session,
            listing=Listing(
                id=lid,
                url=HttpUrl(f"https://www.wg-gesucht.de/{lid}.html"),
                title=f"Room {lid}",
                price_eur=500,
                lat=lat,
                lng=lng,
                description=f"Nice room {lid}",
            ),
            status="full",
        )


def _make_engine() -> tuple:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_hunt_engine_scores_every_pool_listing_once(monkeypatch) -> None:
    """Matcher scores each pool listing once; second pass adds no score rows."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.9)

    search_spy = AsyncMock()

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="u1", age=22, gender=Gender.female),
        )
        sp = SearchProfile(
            city="München",
            max_rent_eur=900,
            rescan_interval_minutes=30,
            schedule="one_shot",
        )
        repo.upsert_search_profile(session, username="u1", sp=sp)
        hunt = repo.create_hunt(session, username="u1", schedule="one_shot")
        _seed_listings(session, ("a", "b", "c"))

    q: asyncio.Queue = asyncio.Queue()
    he = HuntEngine(hunt.id, "u1", q)

    with (
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_stub),
        ),
        patch(
            # Regression: matcher must never call the scraper's search path.
            "app.wg_agent.browser.anonymous_search",
            new=search_spy,
        ),
    ):

        async def run() -> None:
            await he.run_find_only()
            # Second pass: no new listings since the last scrape, so no new scores.
            await he.run_find_only()

        asyncio.run(run())

    search_spy.assert_not_called()

    with Session(engine) as session:
        actions = repo.list_actions_for_hunt(session, hunt_id=hunt.id)
        listings = repo.list_listings_for_hunt(session, hunt_id=hunt.id)

    new_listing_actions = [a for a in actions if a.kind == ActionKind.new_listing]
    assert len([a for a in actions if a.kind == ActionKind.search]) >= 2
    assert len(new_listing_actions) == 3
    assert len({a.listing_id for a in new_listing_actions if a.listing_id}) == 3
    assert len(listings) == 3


def test_hunt_engine_sees_listings_added_between_passes(monkeypatch) -> None:
    """The rescan path picks up new global listings added between passes."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.6)

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="u1b", age=22, gender=Gender.female),
        )
        sp = SearchProfile(
            city="München",
            max_rent_eur=900,
            rescan_interval_minutes=30,
            schedule="one_shot",
        )
        repo.upsert_search_profile(session, username="u1b", sp=sp)
        hunt = repo.create_hunt(session, username="u1b", schedule="one_shot")
        _seed_listings(session, ("a", "b"))

    q: asyncio.Queue = asyncio.Queue()
    he = HuntEngine(hunt.id, "u1b", q)

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_stub),
    ):

        async def run() -> None:
            await he.run_find_only()
            with Session(engine) as s:
                _seed_listings(s, ("c", "d"))
            await he.run_find_only()

        asyncio.run(run())

    with Session(engine) as session:
        listings = repo.list_listings_for_hunt(session, hunt_id=hunt.id)
    assert {l.id for l in listings} == {"a", "b", "c", "d"}


def test_periodic_hunter_runs_stop_correctly(monkeypatch) -> None:
    """Empty pool → matcher runs, hunt finishes as done (one_shot)."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="u2", age=24, gender=Gender.male),
        )
        sp = SearchProfile(
            city="München",
            max_rent_eur=800,
            rescan_interval_minutes=30,
            schedule="one_shot",
        )
        repo.upsert_search_profile(session, username="u2", sp=sp)
        hunt = repo.create_hunt(session, username="u2", schedule="one_shot")
        repo.update_hunt_status(session, hunt_id=hunt.id, status=HuntStatus.running)

    q: asyncio.Queue = asyncio.Queue()
    hunter = PeriodicHunter(
        hunt.id,
        "u2",
        interval_minutes=0,
        event_queue=q,
        schedule="one_shot",
    )

    async def run() -> None:
        await hunter.start()

    asyncio.run(run())

    with Session(engine) as session:
        hrow = session.get(HuntRow, hunt.id)
        actions = repo.list_actions_for_hunt(session, hunt_id=hunt.id)

    assert hrow is not None
    assert hrow.status == HuntStatus.done.value
    assert any(a.kind == ActionKind.done for a in actions)


def test_commute_times_reach_evaluator_and_persist(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    tum = PlaceLocation(
        label="TUM", place_id="ChIJ_TUM", lat=48.149, lng=11.568
    )
    fake_matrix = {("ChIJ_TUM", "TRANSIT"): 1080}

    captured: dict = {}

    async def evaluate_capture(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        captured["travel_times"] = travel_times
        captured["nearby_places"] = nearby_places
        return _stub_result(0.8)

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="u3", age=22, gender=Gender.female),
        )
        sp = SearchProfile(
            city="München",
            max_rent_eur=900,
            main_locations=[tum],
            has_bike=False,
            has_car=False,
            rescan_interval_minutes=30,
            schedule="one_shot",
        )
        repo.upsert_search_profile(session, username="u3", sp=sp)
        hunt = repo.create_hunt(session, username="u3", schedule="one_shot")
        _seed_listings(session, ("lst1",), lat=48.13, lng=11.50)

    q: asyncio.Queue = asyncio.Queue()
    he = HuntEngine(hunt.id, "u3", q)

    with (
        patch(
            "app.wg_agent.periodic.commute.travel_times",
            new=AsyncMock(return_value=fake_matrix),
        ),
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_capture),
        ),
    ):
        asyncio.run(he.run_find_only())

    assert captured["travel_times"] == fake_matrix

    with Session(engine) as session:
        score_row = session.get(ListingScoreRow, ("lst1", hunt.id))
    assert score_row is not None
    assert score_row.travel_minutes == {
        "ChIJ_TUM": {"mode": "TRANSIT", "minutes": 18}
    }
    # The matcher stamps scored_against_scraped_at with the listing's scraped_at.
    assert score_row.scored_against_scraped_at is not None


def test_nearby_places_reach_evaluator_and_persist(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    fake_nearby = {
        "gym": NearbyPlace(
            key="gym",
            label="Gym",
            searched=True,
            distance_m=240,
            place_name="Fit Star",
            category="sport.fitness.fitness_centre",
        )
    }

    captured: dict = {}

    async def evaluate_capture(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        captured["nearby_places"] = nearby_places
        return _stub_result(0.7)

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="u-nearby", age=22, gender=Gender.female),
        )
        sp = SearchProfile(
            city="München",
            max_rent_eur=900,
            preferences=[PreferenceWeight(key="gym", weight=5)],
            rescan_interval_minutes=30,
            schedule="one_shot",
        )
        repo.upsert_search_profile(session, username="u-nearby", sp=sp)
        hunt = repo.create_hunt(session, username="u-nearby", schedule="one_shot")
        _seed_listings(session, ("lst-nearby",), lat=48.13, lng=11.50)

    q: asyncio.Queue = asyncio.Queue()
    he = HuntEngine(hunt.id, "u-nearby", q)

    with (
        patch(
            "app.wg_agent.periodic.places.nearby_places",
            new=AsyncMock(return_value=fake_nearby),
        ),
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_capture),
        ),
    ):
        asyncio.run(he.run_find_only())

    assert captured["nearby_places"] == fake_nearby

    with Session(engine) as session:
        score_row = session.get(ListingScoreRow, ("lst-nearby", hunt.id))
    assert score_row is not None
    assert score_row.nearby_places == [fake_nearby["gym"].model_dump(mode="json")]


def test_commute_skipped_when_listing_lacks_coords(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    tum = PlaceLocation(
        label="TUM", place_id="ChIJ_TUM", lat=48.149, lng=11.568
    )

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.5, summary="no commute")

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="u4", age=22, gender=Gender.female),
        )
        sp = SearchProfile(
            city="München",
            max_rent_eur=900,
            main_locations=[tum],
            rescan_interval_minutes=30,
            schedule="one_shot",
        )
        repo.upsert_search_profile(session, username="u4", sp=sp)
        hunt = repo.create_hunt(session, username="u4", schedule="one_shot")
        _seed_listings(session, ("lst2",))  # lat/lng default None

    q: asyncio.Queue = asyncio.Queue()
    he = HuntEngine(hunt.id, "u4", q)
    travel_mock = AsyncMock(return_value={})

    with (
        patch("app.wg_agent.periodic.commute.travel_times", new=travel_mock),
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_stub),
        ),
    ):
        asyncio.run(he.run_find_only())

    travel_mock.assert_not_called()

    with Session(engine) as session:
        score_row = session.get(ListingScoreRow, ("lst2", hunt.id))
    assert score_row is not None
    assert score_row.travel_minutes is None
