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
    _ACTIVE_MATCHERS,
    _NOTIFY_STATE,
    _SUBSCRIBERS,
    request_backfill,
    resume_user_agents,
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
    repo.upsert_search_profile(session, username=username, sp=sp)  # type: ignore[func-returns-value]


def _set_user_created_at(session: Session, username: str, when) -> None:
    """Backdate/forward the user's `created_at` and its `backfill_baseline_at`
    so tests can pretend the user was created before (or after) the seeded
    listings' `first_seen_at`. Both fields move in lock-step because every
    freshness gate now reads `backfill_baseline_at` (falling back to
    `created_at`) — leaving the baseline at `utcnow()` would defeat the
    `first_seen_at > baseline` check in tests that stamp listings at a
    past date.
    """
    row = session.get(UserRow, username)
    assert row is not None
    row.created_at = when
    row.backfill_baseline_at = when
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
    # Per-location map now carries every computed mode side-by-side
    # ({mode_lower: minutes}) so the drawer can show transit/bike/drive next
    # to each other; the old single-mode shape is only kept around for
    # backward-compat reads in api._travel_minutes_by_label.
    assert match_row.travel_minutes == {"ChIJ_TUM": {"transit": 18}}
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
    # Disable the freshness window so the fixed 2024-06-01 `first_seen_at`
    # below is not rejected as stale. The freshness-window gate has its own
    # dedicated tests.
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "0")
    _reset_notify_state("u-new")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "0")
    _reset_notify_state("u-cool")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
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


def test_stale_listing_outside_fresh_window_is_not_emailed(monkeypatch) -> None:
    """A listing first-seen before `WG_NOTIFY_FRESH_WINDOW_MINUTES` ago must
    not produce an email, even if every other gate passes.

    Guards "pay more attention to how new the listing is": backlog scored
    long after it was posted should stay out of the inbox.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "60")
    _reset_notify_state("u-stale")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    now = datetime.utcnow()
    with Session(engine) as session:
        _seed_user(
            session,
            "u-stale",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-stale@example.com",
        )
        _set_user_created_at(session, "u-stale", now - timedelta(days=7))
        _seed_listings(session, ("old",))
        # First seen AFTER signup but outside the 60-minute window.
        _set_listing_first_seen_at(session, "old", now - timedelta(hours=3))

    agent = UserAgent("u-stale")

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

    mocked_send.assert_not_called()
    assert _NOTIFY_STATE.get("u-stale") is None or not _NOTIFY_STATE["u-stale"].pending


def test_fresh_listing_inside_window_is_emailed(monkeypatch) -> None:
    """A listing first-seen inside `WG_NOTIFY_FRESH_WINDOW_MINUTES` flushes
    on the first pass that scores it (companion to the stale-window test)."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "60")
    _reset_notify_state("u-fresh")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    now = datetime.utcnow()
    with Session(engine) as session:
        _seed_user(
            session,
            "u-fresh",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-fresh@example.com",
        )
        _set_user_created_at(session, "u-fresh", now - timedelta(days=7))
        _seed_listings(session, ("fresh",))
        _set_listing_first_seen_at(session, "fresh", now - timedelta(minutes=5))

    agent = UserAgent("u-fresh")

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
    items = list(mocked_send.call_args.kwargs["items"])
    assert [i.listing_id for i in items] == ["fresh"]


def test_digest_never_queues_the_same_listing_twice(monkeypatch) -> None:
    """Once a listing is in the pending buffer or has already been emailed,
    a second call to `_maybe_queue_digest_item` for the same listing is a
    no-op. Defends against the matcher re-entering the queue path for a
    listing that is mid-flight in a held (cooldown) digest.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "0")
    _reset_notify_state("u-dedup")

    now = datetime.utcnow()
    with Session(engine) as session:
        _seed_user(
            session,
            "u-dedup",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-dedup@example.com",
        )
        _set_user_created_at(session, "u-dedup", now - timedelta(days=7))
        _seed_listings(session, ("dup",))
        _set_listing_first_seen_at(session, "dup", now - timedelta(minutes=1))
        row = session.get(ListingRow, "dup")
        assert row is not None

    agent = UserAgent("u-dedup")
    listing = Listing(
        id="dup",
        url=HttpUrl("https://www.wg-gesucht.de/dup.html"),
        title="Dup",
        price_eur=500,
        description="d",
        score=0.95,
        match_reasons=[],
    )

    def queue_once() -> None:
        with Session(engine) as s:
            r = s.get(ListingRow, "dup")
            assert r is not None
            agent._maybe_queue_digest_item(
                row=r,
                listing=listing,
                user_email="u-dedup@example.com",
                baseline_at=now - timedelta(days=7),
            )

    # Two back-to-back queue calls with pending not yet flushed.
    queue_once()
    queue_once()
    assert [i.listing_id for i in _NOTIFY_STATE["u-dedup"].pending] == ["dup"]

    # Flush succeeds, emailed_ids now tracks "dup".
    with patch(
        "app.wg_agent.periodic.notifier.send_digest_email",
        return_value=True,
    ) as mocked_send:
        from app.wg_agent.periodic import _try_flush_digest

        assert _try_flush_digest("u-dedup", "u-dedup@example.com") == 1
        mocked_send.assert_called_once()

    assert _NOTIFY_STATE["u-dedup"].emailed_ids == {"dup"}
    assert _NOTIFY_STATE["u-dedup"].pending == []

    # A third queue attempt after the flush must not re-queue.
    queue_once()
    assert _NOTIFY_STATE["u-dedup"].pending == []


# --- Persisted agent pause state ---------------------------------------------


def test_resume_user_agents_skips_paused_users(monkeypatch) -> None:
    """A user with `UserAgentStateRow.paused=True` must NOT get an auto-spawned
    matcher on backend boot. Regression for the "Stop should survive a restart
    until the user presses Resume" requirement.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-paused",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_user(
            session,
            "u-running",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        repo.set_user_agent_paused(session, username="u-paused", paused=True)

    spawned: list[str] = []
    monkeypatch.setattr(
        "app.wg_agent.periodic.spawn_user_agent",
        lambda username, **kwargs: spawned.append(username),
    )

    asyncio.run(resume_user_agents())

    assert "u-running" in spawned
    assert "u-paused" not in spawned


def test_set_user_agent_paused_roundtrip(monkeypatch) -> None:
    """`set_user_agent_paused` + `is_user_agent_paused` round-trip both
    true/false, including upserting over an existing row.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-flip",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )

    with Session(engine) as session:
        assert repo.is_user_agent_paused(session, username="u-flip") is False

    with Session(engine) as session:
        repo.set_user_agent_paused(session, username="u-flip", paused=True)
    with Session(engine) as session:
        assert repo.is_user_agent_paused(session, username="u-flip") is True

    with Session(engine) as session:
        repo.set_user_agent_paused(session, username="u-flip", paused=False)
    with Session(engine) as session:
        assert repo.is_user_agent_paused(session, username="u-flip") is False


def test_run_match_pass_bails_on_paused_flag(monkeypatch) -> None:
    """A `paused=True` flag observed at the top of `run_match_pass` must
    short-circuit before any candidate is even considered, so no
    `UserListingRow` rows are written for a user who pressed "Stop".
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-stop-top",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("x1", "x2", "x3"))
        repo.set_user_agent_paused(session, username="u-stop-top", paused=True)

    evaluate_spy = AsyncMock(side_effect=lambda *a, **kw: _stub_result(0.9))

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=evaluate_spy,
    ):
        returned = asyncio.run(UserAgent("u-stop-top").run_match_pass())

    assert returned == 0
    evaluate_spy.assert_not_called()
    with Session(engine) as session:
        assert repo.list_user_listings(session, username="u-stop-top") == []


def test_run_match_pass_bails_when_paused_mid_pass(monkeypatch) -> None:
    """If `paused` flips to True during a pass, the matcher must stop
    writing `UserListingRow` rows for the remaining candidates instead of
    burning through the whole 15-item batch. Regression for "I pressed
    Stop but listings grew by 9 in 40 seconds".
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-stop-mid",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )
        _seed_listings(session, ("m1", "m2", "m3", "m4"))

    # Flip the paused flag mid-pass on the first evaluate call — that
    # mirrors the user pressing "Stop" while one candidate was in flight.
    scored: list[str] = []

    async def evaluate_once_then_pause(
        listing: Listing, *args, **kwargs
    ) -> EvaluationResult:
        scored.append(listing.id)
        if len(scored) == 1:
            with Session(engine) as s:
                repo.set_user_agent_paused(s, username="u-stop-mid", paused=True)
        return _stub_result(0.8)

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_once_then_pause),
    ):
        asyncio.run(UserAgent("u-stop-mid").run_match_pass())

    # Exactly one listing may have been fully scored (the one in flight
    # when the flag flipped). The remaining candidates must not be scored.
    with Session(engine) as session:
        persisted = [l.id for l in repo.list_user_listings(session, username="u-stop-mid")]
    assert len(persisted) == 1, f"expected 1 persisted row, got {persisted}"
    assert persisted[0] == scored[0]
    assert len(scored) == 1


# --- Silent backfill + profile-edit re-backfill ------------------------------


def test_backfill_pass_scores_every_listing_without_emailing(monkeypatch) -> None:
    """`run_backfill_pass` scores every pool listing in one shot with no cap,
    emits `backfill_progress` events, and never queues a digest email — even
    when the score passes the notify threshold and the fresh-window would
    otherwise accept the listing.

    Together these guard the "silent backfill" contract: after signup every
    existing listing lands on the dashboard without a "new" badge and without
    triggering an email.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "0")
    _reset_notify_state("u-backfill")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    now = datetime.utcnow()
    ids = tuple(f"bf{i}" for i in range(20))

    with Session(engine) as session:
        _seed_user(
            session,
            "u-backfill",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-backfill@example.com",
        )
        # Baseline must be AFTER every listing's first_seen_at so the
        # silence gate would NOT trip on freshness alone — the only reason
        # these skip email is the `silent=True` branch in `_score_one`.
        _set_user_created_at(session, "u-backfill", now)
        _seed_listings(session, ids)
        for lid in ids:
            _set_listing_first_seen_at(session, lid, now - timedelta(days=1))

    agent = UserAgent("u-backfill")
    progress_events: list[tuple[int, int]] = []

    async def run() -> None:
        queue = subscribe("u-backfill")
        try:
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
                total = await agent.run_backfill_pass(
                    on_progress=lambda d, t: progress_events.append((d, t))
                )
            assert total == len(ids)
            assert mocked_send.call_count == 0
        finally:
            # Drain and classify the SSE fan-out before tearing down so we
            # can assert the wire protocol matches what the frontend expects.
            kinds: list[ActionKind] = []
            while not queue.empty():
                kinds.append(queue.get_nowait().kind)
            unsubscribe("u-backfill", queue)
            assert ActionKind.backfill_progress in kinds
            # Every listing fires a `new_listing` + `evaluate` pair.
            assert kinds.count(ActionKind.new_listing) == len(ids)
            assert kinds.count(ActionKind.evaluate) == len(ids)
            # And exactly `len(ids) + 1` progress events (initial + each tick).
            assert (
                kinds.count(ActionKind.backfill_progress) == len(ids) + 1
            )

    asyncio.run(run())

    with Session(engine) as session:
        scored = repo.list_user_listings(session, username="u-backfill")
    assert {l.id for l in scored} == set(ids)
    # Callback fires start + one per listing.
    assert progress_events[0] == (0, len(ids))
    assert progress_events[-1] == (len(ids), len(ids))
    assert _NOTIFY_STATE.get("u-backfill") is None or not _NOTIFY_STATE[
        "u-backfill"
    ].pending


def test_post_backfill_match_pass_emails_new_listings(monkeypatch) -> None:
    """After a silent backfill, a subsequent `run_match_pass` emails a
    digest for a listing whose `first_seen_at > baseline_at` — proving the
    normal notification path picks up where the backfill left off.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "0")
    _reset_notify_state("u-after-bf")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    baseline = datetime(2024, 6, 1)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-after-bf",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-after-bf@example.com",
        )
        _set_user_created_at(session, "u-after-bf", baseline)
        # Two backlog listings in the pool at backfill time.
        _seed_listings(session, ("old1", "old2"))
        _set_listing_first_seen_at(session, "old1", baseline - timedelta(days=3))
        _set_listing_first_seen_at(session, "old2", baseline - timedelta(days=2))

    agent = UserAgent("u-after-bf")

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
        asyncio.run(agent.run_backfill_pass())
        assert mocked_send.call_count == 0

        # A new listing appears AFTER the backfill finishes — the scraper
        # would have added it mid-loop in real life. The match pass must
        # pick it up and email a digest for it.
        with Session(engine) as s:
            _seed_listings(s, ("fresh1",))
            _set_listing_first_seen_at(
                s, "fresh1", baseline + timedelta(minutes=1)
            )

        asyncio.run(agent.run_match_pass())

    assert mocked_send.call_count == 1
    items = list(mocked_send.call_args.kwargs["items"])
    assert [i.listing_id for i in items] == ["fresh1"]


def test_profile_edit_wipes_matches_and_bumps_baseline(monkeypatch) -> None:
    """A material `upsert_search_profile` call deletes every `UserListingRow`
    for the user and bumps `backfill_baseline_at` to `utcnow()`. The next
    `run_backfill_pass` re-scores the whole pool silently.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setenv("WG_NOTIFY_FRESH_WINDOW_MINUTES", "0")
    _reset_notify_state("u-edit")

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.95)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-edit",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
            email="u-edit@example.com",
        )
        _seed_listings(session, ("e1", "e2", "e3"))

    agent = UserAgent("u-edit")
    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_stub),
    ):
        asyncio.run(agent.run_backfill_pass())

    with Session(engine) as session:
        seeded = repo.list_user_listings(session, username="u-edit")
    assert {l.id for l in seeded} == {"e1", "e2", "e3"}

    with Session(engine) as session:
        user_row_before = session.get(UserRow, "u-edit")
        assert user_row_before is not None
        baseline_before = user_row_before.backfill_baseline_at

    # Tiny sleep so the bumped baseline is strictly later than the initial
    # signup baseline — otherwise `utcnow()` can tie on fast machines and
    # the > assertion below becomes flaky.
    import time as _time

    _time.sleep(0.01)

    with Session(engine) as session:
        # Materially edit the profile: `price_max_eur` is one of the fields
        # `_search_profile_material_snapshot` hashes, so flipping it must
        # produce a diff. `max_rent_eur` alone would NOT — it's a domain
        # field that never reaches the persisted snapshot.
        new_sp = SearchProfile(
            city="München",
            max_rent_eur=900,
            price_max_eur=1200,
            rescan_interval_minutes=30,
            schedule="one_shot",
        )
        _, bumped = repo.upsert_search_profile(
            session, username="u-edit", sp=new_sp
        )
    assert bumped is True

    with Session(engine) as session:
        # Every `UserListingRow` for this user must be gone so the next
        # `list_scorable_listings_for_user` sees the full pool again.
        after_wipe = repo.list_user_listings(session, username="u-edit")
        user_row_after = session.get(UserRow, "u-edit")
    assert after_wipe == []
    assert user_row_after is not None
    assert user_row_after.backfill_baseline_at is not None
    assert baseline_before is not None
    assert user_row_after.backfill_baseline_at > baseline_before

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_stub),
    ):
        asyncio.run(agent.run_backfill_pass())

    with Session(engine) as session:
        rescored = repo.list_user_listings(session, username="u-edit")
    assert {l.id for l in rescored} == {"e1", "e2", "e3"}


def test_upsert_search_profile_noop_does_not_bump_baseline(monkeypatch) -> None:
    """Re-submitting the exact same search profile (no material diff) must
    NOT wipe matches or bump the baseline — otherwise every dashboard reload
    that re-PUTs the profile would silently wipe the shortlist.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    sp = SearchProfile(
        city="München",
        max_rent_eur=900,
        rescan_interval_minutes=30,
        schedule="one_shot",
    )
    with Session(engine) as session:
        _seed_user(session, "u-noop", sp)
        _seed_listings(session, ("n1",))

    async def evaluate_stub(
        _lst: Listing, _sp: SearchProfile, *, travel_times=None, nearby_places=None
    ) -> EvaluationResult:
        return _stub_result(0.8)

    with patch(
        "app.wg_agent.periodic.evaluator.evaluate",
        new=AsyncMock(side_effect=evaluate_stub),
    ):
        asyncio.run(UserAgent("u-noop").run_match_pass())

    with Session(engine) as session:
        baseline_before = (
            session.get(UserRow, "u-noop").backfill_baseline_at  # type: ignore[union-attr]
        )
        # Re-PUT the identical profile.
        _, bumped = repo.upsert_search_profile(
            session, username="u-noop", sp=sp
        )
        baseline_after = (
            session.get(UserRow, "u-noop").backfill_baseline_at  # type: ignore[union-attr]
        )
        persisted = repo.list_user_listings(session, username="u-noop")

    assert bumped is False
    assert baseline_before == baseline_after
    assert {l.id for l in persisted} == {"n1"}


def test_request_backfill_resets_matcher_flag(monkeypatch) -> None:
    """`request_backfill` flips `_backfill_complete` to False and wakes the
    matcher so the profile-edit flow doesn't have to wait out the rescan
    interval before the silent re-backfill kicks off.
    """
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        _seed_user(
            session,
            "u-req",
            SearchProfile(
                city="München",
                max_rent_eur=900,
                rescan_interval_minutes=30,
                schedule="one_shot",
            ),
        )

    async def scenario() -> None:
        matcher = PeriodicUserMatcher(
            username="u-req", interval_minutes=1
        )
        _ACTIVE_MATCHERS["u-req"] = matcher
        try:
            matcher._backfill_complete = True
            matcher.backfill_state = {"done": 5, "total": 5}
            assert request_backfill("u-req") is True
            assert matcher._backfill_complete is False
            assert matcher.backfill_state is None
            # The wake event must be set so the matcher cuts its sleep short.
            assert matcher._wake.is_set()
        finally:
            _ACTIVE_MATCHERS.pop("u-req", None)

    asyncio.run(scenario())
    # No matcher registered → no-op return.
    assert request_backfill("u-ghost") is False
