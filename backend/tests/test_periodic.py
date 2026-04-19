"""UserAgent / PeriodicUserMatcher behavior (in-memory DB, mocked network + LLM).

Post-refactor the matcher is keyed by username and reads the shared
`ListingRow` pool; it never calls `browser.anonymous_search`. Tests pre-seed
the pool via `upsert_global_listing` before the matcher runs.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from pydantic import HttpUrl
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("WG_SECRET_KEY", Fernet.generate_key().decode())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import db as db_module, repo  # noqa: E402
from app.wg_agent.db_models import ListingRow, UserListingRow, UserRow  # noqa: E402
from app.wg_agent.evaluator import EvaluationResult  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    ActionKind,
    ComponentScore,
    Gender,
    Listing,
    NearbyPlace,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
    UserProfile,
)
from app.wg_agent.periodic import (  # noqa: E402
    PeriodicUserMatcher,
    UserAgent,
    _ACTIVE_AGENTS,
    _NOTIFY_STATE,
    _SUBSCRIBERS,
    subscribe,
    unsubscribe,
)


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


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_user(
    session: Session,
    username: str,
    sp: SearchProfile,
    *,
    email: str | None = None,
) -> None:
    repo.create_user(
        session,
        profile=UserProfile(
            username=username, email=email, age=22, gender=Gender.female
        ),
    )
    repo.upsert_search_profile(session, username=username, sp=sp)


def _set_user_created_at(session: Session, username: str, when) -> None:
    """Backdate/forward the user's `created_at` so tests can pretend the user
    was created before (or after) the seeded listings' `first_seen_at`."""
    row = session.get(UserRow, username)
    assert row is not None
    row.created_at = when
    session.add(row)
    session.commit()


def _set_listing_first_seen_at(session: Session, listing_id: str, when) -> None:
    row = session.get(ListingRow, listing_id)
    assert row is not None
    row.first_seen_at = when
    session.add(row)
    session.commit()


def test_user_agent_scores_every_pool_listing_once(monkeypatch) -> None:
    """Matcher scores each pool listing once; second pass adds no score rows."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        return _stub_result(0.9)

    search_spy = AsyncMock()

    with Session(engine) as session:
        _seed_user(
            session,
            "u1",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("a", "b", "c"))

    agent = UserAgent("u1")

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
            await agent.run_match_pass()
            # Second pass: no new listings since the last scrape, so no new scores.
            await agent.run_match_pass()

        asyncio.run(run())

    search_spy.assert_not_called()

    with Session(engine) as session:
        actions = repo.list_actions_for_user(session, username="u1")
        listings = repo.list_user_listings(session, username="u1")

    new_listing_actions = [a for a in actions if a.kind == ActionKind.new_listing]
    assert len([a for a in actions if a.kind == ActionKind.search]) >= 2
    assert len(new_listing_actions) == 3
    assert len({a.listing_id for a in new_listing_actions if a.listing_id}) == 3
    assert len(listings) == 3


def test_user_agent_sees_listings_added_between_passes(monkeypatch) -> None:
    """The rescan path picks up new global listings added between passes."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        return _stub_result(0.6)

    with Session(engine) as session:
        _seed_user(
            session,
            "u1b",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("a", "b"))

    agent = UserAgent("u1b")

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_stub),
    ):

        async def run() -> None:
            await agent.run_match_pass()
            with Session(engine) as s:
                _seed_listings(s, ("c", "d"))
            await agent.run_match_pass()

        asyncio.run(run())

    with Session(engine) as session:
        listings = repo.list_user_listings(session, username="u1b")
    assert {l.id for l in listings} == {"a", "b", "c", "d"}


def test_periodic_user_matcher_cancels_cleanly(monkeypatch) -> None:
    """Starting a `PeriodicUserMatcher` + cancelling must not bubble an
    exception, and the registry entry must be cleared."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-cancel",
            SearchProfile(
                city="München",
                max_rent_eur=800,
                rescan_interval_minutes=5,
                schedule="periodic",
            ),
        )
        _seed_listings(session, ("solo",))

    started_event = asyncio.Event()

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        started_event.set()
        return _stub_result(0.5)

    async def scenario() -> None:
        matcher = PeriodicUserMatcher(
            username="u-cancel",
            interval_minutes=1,
        )
        task = asyncio.create_task(matcher.start())
        _ACTIVE_AGENTS["u-cancel"] = task
        try:
            await asyncio.wait_for(started_event.wait(), timeout=2.0)
            # Give the task a moment to finish the first pass and enter sleep.
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            _ACTIVE_AGENTS.pop("u-cancel", None)

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_stub),
    ):
        asyncio.run(scenario())

    assert "u-cancel" not in _ACTIVE_AGENTS
    assert "u-cancel" not in _SUBSCRIBERS


def test_publish_fans_out_to_every_subscriber(monkeypatch) -> None:
    """Two SSE subscribers for the same user must each receive every event.

    Regression for the "matches show on only one device when the same user
    is open in two browsers" bug: a single shared `asyncio.Queue` would
    deliver each item to only one waiter.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        return _stub_result(0.7)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-fanout",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("fan1", "fan2"))

    async def run() -> None:
        device_a = subscribe("u-fanout")
        device_b = subscribe("u-fanout")
        try:
            agent = UserAgent("u-fanout")
            await agent.run_match_pass()

            def drain(q: asyncio.Queue) -> list[ActionKind]:
                kinds: list[ActionKind] = []
                while not q.empty():
                    kinds.append(q.get_nowait().kind)
                return kinds

            return drain(device_a), drain(device_b)
        finally:
            unsubscribe("u-fanout", device_a)
            unsubscribe("u-fanout", device_b)

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_stub),
    ):
        events_a, events_b = asyncio.run(run())

    # 1 search + 2 (new_listing + evaluate) per candidate = 5 events each.
    assert events_a == events_b
    assert events_a.count(ActionKind.new_listing) == 2
    assert events_a.count(ActionKind.evaluate) == 2
    assert events_a.count(ActionKind.search) == 1
    assert "u-fanout" not in _SUBSCRIBERS


def test_commute_times_reach_evaluator_and_persist(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    tum = PlaceLocation(
        label="TUM", place_id="ChIJ_TUM", lat=48.149, lng=11.568
    )
    fake_matrix = {("ChIJ_TUM", "TRANSIT"): 1080}

    captured: dict = {}

    async def evaluate_capture(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        captured["travel_times"] = travel_times
        captured["nearby_places"] = nearby_places
        return _stub_result(0.8)

    with Session(engine) as session:
        _seed_user(
            session,
            "u3",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                main_locations=[tum],
                has_bike=False,
                has_car=False,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("lst1",), lat=48.13, lng=11.50)

    agent = UserAgent("u3")

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
        asyncio.run(agent.run_match_pass())

    assert captured["travel_times"] == fake_matrix

    with Session(engine) as session:
        match_row = session.get(UserListingRow, ("u3", "lst1"))
    assert match_row is not None
    assert match_row.travel_minutes == {
        "ChIJ_TUM": {"mode": "TRANSIT", "minutes": 18}
    }
    # The matcher stamps scored_against_scraped_at with the listing's scraped_at.
    assert match_row.scored_against_scraped_at is not None


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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        captured["nearby_places"] = nearby_places
        return _stub_result(0.7)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-nearby",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                preferences=[PreferenceWeight(key="gym", weight=5)],
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("lst-nearby",), lat=48.13, lng=11.50)

    agent = UserAgent("u-nearby")

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
        asyncio.run(agent.run_match_pass())

    assert captured["nearby_places"] == fake_nearby

    with Session(engine) as session:
        match_row = session.get(UserListingRow, ("u-nearby", "lst-nearby"))
    assert match_row is not None
    assert match_row.nearby_places == [fake_nearby["gym"].model_dump(mode="json")]


def test_commute_skipped_when_listing_lacks_coords(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    tum = PlaceLocation(
        label="TUM", place_id="ChIJ_TUM", lat=48.149, lng=11.568
    )

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        return _stub_result(0.5, summary="no commute")

    with Session(engine) as session:
        _seed_user(
            session,
            "u4",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                main_locations=[tum],
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("lst2",))  # lat/lng default None

    agent = UserAgent("u4")
    travel_mock = AsyncMock(return_value={})

    with (
        patch("app.wg_agent.periodic.commute.travel_times", new=travel_mock),
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_stub),
        ),
    ):
        asyncio.run(agent.run_match_pass())

    travel_mock.assert_not_called()

    with Session(engine) as session:
        match_row = session.get(UserListingRow, ("u4", "lst2"))
    assert match_row is not None
    assert match_row.travel_minutes is None


# --- Email digest notification behavior ---------------------------------------


def _reset_notify_state(username: str) -> None:
    _NOTIFY_STATE.pop(username, None)


def test_initial_evaluation_does_not_send_email(monkeypatch) -> None:
    """First pass over listings that predate the user must not send any email.

    This guards the "don't email during initial evaluation" requirement: even
    though every listing scores above the threshold, they were all first seen
    by the scraper before the user signed up.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    _reset_notify_state("u-initial")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-initial",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-initial@example.com",
        )
        _seed_listings(session, ("a", "b", "c"))
        # Pretend every listing was first seen long before the user existed.
        for lid in ("a", "b", "c"):
            _set_listing_first_seen_at(
                session, lid, datetime(2020, 1, 1)
            )
        _set_user_created_at(session, "u-initial", datetime(2024, 1, 1))

    send_spy = patch(
        "app.wg_agent.periodic.notifier.send_digest_email",
        return_value=True,
    )

    agent = UserAgent("u-initial")

    with (
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_stub),
        ),
        send_spy as mocked_send,
    ):
        asyncio.run(agent.run_match_pass())

    mocked_send.assert_not_called()
    assert _NOTIFY_STATE.get("u-initial") is None or not _NOTIFY_STATE["u-initial"].pending


def test_new_listings_trigger_single_batched_email(monkeypatch) -> None:
    """A pass that scores new (post-signup) high-scoring listings sends exactly
    one digest email containing every queued listing."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    _reset_notify_state("u-new")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-new",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-new@example.com",
        )
        _set_user_created_at(session, "u-new", datetime(2024, 1, 1))
        _seed_listings(session, ("n1", "n2"))
        # Scraper first-saw these listings AFTER the user was created.
        for lid in ("n1", "n2"):
            _set_listing_first_seen_at(
                session, lid, datetime(2024, 6, 1)
            )

    agent = UserAgent("u-new")

    with (
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_stub),
        ),
        patch(
            "app.wg_agent.periodic.notifier.send_digest_email",
            return_value=True,
        ) as mocked_send,
    ):
        asyncio.run(agent.run_match_pass())

    assert mocked_send.call_count == 1
    kwargs = mocked_send.call_args.kwargs
    assert kwargs["to_email"] == "u-new@example.com"
    assert kwargs["username"] == "u-new"
    items = list(kwargs["items"])
    assert {i.listing_url.rstrip("/").rsplit("/", 1)[-1] for i in items} == {
        "n1.html",
        "n2.html",
    }


def test_cooldown_suppresses_second_email_and_releases_after(monkeypatch) -> None:
    """Two passes within the 5-minute cooldown emit one email; once the
    cooldown elapses, the next pass drains the queued listings."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setenv("WG_NOTIFY_COOLDOWN_MINUTES", "5")
    _reset_notify_state("u-cool")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None, market_context=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-cool",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-cool@example.com",
        )
        _set_user_created_at(session, "u-cool", datetime(2024, 1, 1))
        _seed_listings(session, ("c1",))
        _set_listing_first_seen_at(session, "c1", datetime(2024, 6, 1))

    agent = UserAgent("u-cool")

    with (
        patch(
            "app.wg_agent.periodic.evaluator.evaluate",
            new=AsyncMock(side_effect=evaluate_stub),
        ),
        patch(
            "app.wg_agent.periodic.notifier.send_digest_email",
            return_value=True,
        ) as mocked_send,
    ):
        asyncio.run(agent.run_match_pass())
        assert mocked_send.call_count == 1

        # Add another listing and rerun immediately — cooldown must suppress.
        with Session(engine) as s:
            _seed_listings(s, ("c2",))
            _set_listing_first_seen_at(s, "c2", datetime(2024, 6, 2))
        asyncio.run(agent.run_match_pass())
        assert mocked_send.call_count == 1
        assert [i.listing_url for i in _NOTIFY_STATE["u-cool"].pending][0].endswith(
            "c2.html"
        )

        # Pretend the cooldown elapsed and rerun — the queued c2 should now flush.
        state = _NOTIFY_STATE["u-cool"]
        state.last_sent_at = datetime.utcnow() - timedelta(minutes=10)
        asyncio.run(agent.run_match_pass())
        assert mocked_send.call_count == 2
        last_kwargs = mocked_send.call_args.kwargs
        items = list(last_kwargs["items"])
        assert len(items) == 1
        assert items[0].listing_url.endswith("c2.html")
        assert _NOTIFY_STATE["u-cool"].pending == []
