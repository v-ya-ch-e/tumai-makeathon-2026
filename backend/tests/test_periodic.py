"""HuntEngine / PeriodicHunter behavior (in-memory DB, mocked network + LLM)."""

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
from app.wg_agent.models import (  # noqa: E402
    ActionKind,
    Gender,
    HuntStatus,
    Listing,
    SearchProfile,
    UserProfile,
)
from app.wg_agent.periodic import HuntEngine, PeriodicHunter  # noqa: E402


def _fake_listings(ids: tuple[str, ...]) -> list[Listing]:
    out: list[Listing] = []
    for lid in ids:
        out.append(
            Listing(
                id=lid,
                url=HttpUrl(f"https://www.wg-gesucht.de/{lid}.html"),
                title=f"Room {lid}",
                price_eur=500,
            )
        )
    return out


def test_periodic_hunter_dedupes_new_listings(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)

    async def search_side_effect(*_a, **_kw):
        search_side_effect.calls += 1  # type: ignore[attr-defined]
        if search_side_effect.calls == 1:
            return _fake_listings(("a", "b", "c"))
        return _fake_listings(("a", "b", "d"))

    search_side_effect.calls = 0  # type: ignore[attr-defined]

    async def scrape_identity(lst: Listing) -> Listing:
        return lst

    def score_stub(lst: Listing, _sp: SearchProfile) -> Listing:
        lst.score = 0.9
        lst.score_reason = "ok"
        lst.match_reasons = []
        lst.mismatch_reasons = []
        return lst

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

    q: asyncio.Queue = asyncio.Queue()
    he = HuntEngine(hunt.id, "u1", q)

    with (
        patch(
            "app.wg_agent.periodic.browser.anonymous_search",
            new=AsyncMock(side_effect=search_side_effect),
        ),
        patch(
            "app.wg_agent.periodic.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=scrape_identity),
        ),
        patch("app.wg_agent.periodic.brain.score_listing", side_effect=score_stub),
    ):

        async def run() -> None:
            await he.run_find_only()
            await he.run_find_only()

        asyncio.run(run())

    with Session(engine) as session:
        actions = repo.list_actions_for_hunt(session, hunt_id=hunt.id)
        listings = repo.list_listings_for_hunt(session, hunt_id=hunt.id)

    new_listing_actions = [a for a in actions if a.kind == ActionKind.new_listing]
    assert len([a for a in actions if a.kind == ActionKind.search]) >= 2
    assert len(new_listing_actions) == 4
    assert len({a.listing_id for a in new_listing_actions if a.listing_id}) == 4
    assert len(listings) == 4


def test_periodic_hunter_runs_stop_correctly(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)

    async def search_empty(*_a, **_kw):
        return []

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

    with patch(
        "app.wg_agent.periodic.browser.anonymous_search",
        new=AsyncMock(side_effect=search_empty),
    ):

        async def run() -> None:
            await hunter.start()

        asyncio.run(run())

    with Session(engine) as session:
        hrow = session.get(HuntRow, hunt.id)
        actions = repo.list_actions_for_hunt(session, hunt_id=hunt.id)

    assert hrow is not None
    assert hrow.status == HuntStatus.done.value
    assert any(a.kind == ActionKind.done for a in actions)
