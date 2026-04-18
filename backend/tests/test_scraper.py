"""ScraperAgent tests (in-memory DB, mocked source plugin).

Post-multi-source-refactor: `ScraperAgent` consumes `Source` plugins from
`app/scraper/sources/`, and the wg-gesucht plugin in turn delegates to
`browser.anonymous_search` / `browser.anonymous_scrape_listing`. These
tests patch the `browser.*` functions (the deepest stable seam) and use
namespaced ids (`wg-gesucht:lx`, …) so the per-source deletion sweep
sees them.
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

from app.scraper.agent import ScraperAgent  # noqa: E402
from app.scraper.sources.wg_gesucht import WgGesuchtSource  # noqa: E402
from app.wg_agent import db as db_module, repo  # noqa: E402
from app.wg_agent.db_models import ListingRow, PhotoRow  # noqa: E402
from app.wg_agent.models import Listing  # noqa: E402


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _make_agent(**kwargs) -> ScraperAgent:
    """Build a wg-gesucht-only ScraperAgent (mirrors the existing tests)."""
    kwargs.setdefault("city", "München")
    kwargs.setdefault("max_rent_eur", 2000)
    kwargs.setdefault("max_pages", 1)
    kwargs.setdefault("interval_seconds", 1)
    kwargs.setdefault("refresh_hours", 24)
    kwargs.setdefault("sources", [WgGesuchtSource()])
    return ScraperAgent(**kwargs)


def _full_listing(
    lid: str, *, lat: float = 48.1, lng: float = 11.5, description: str = "Bright room"
) -> Listing:
    bare = lid.split(":", 1)[1] if ":" in lid else lid
    return Listing(
        id=lid,
        url=HttpUrl(f"https://www.wg-gesucht.de/{bare}.html"),
        title=f"Listing {bare}",
        kind="wg",
        price_eur=800,
        lat=lat,
        lng=lng,
        description=description,
        photo_urls=[f"https://img.wg-gesucht.de/{bare}-a.jpg"],
    )


def _stub_listing(lid: str) -> Listing:
    """A partial listing (no description / coords) — should persist as status='stub'."""
    bare = lid.split(":", 1)[1] if ":" in lid else lid
    return Listing(
        id=lid,
        url=HttpUrl(f"https://www.wg-gesucht.de/{bare}.html"),
        title=f"Partial {bare}",
        kind="wg",
    )


def test_scraper_writes_full_listing_and_photos(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def fake_search(*_a, **_kw):
        return [
            Listing(
                id="wg-gesucht:lx",
                url=HttpUrl("https://www.wg-gesucht.de/lx.html"),
                title="stub Lx",
                kind="wg",
            )
        ]

    async def fake_scrape(lst: Listing, **_kw):
        return _full_listing(lst.id)

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch("app.wg_agent.browser.anonymous_scrape_listing", new=AsyncMock(side_effect=fake_scrape)),
    ):
        scraped = asyncio.run(agent.run_once())

    assert scraped == 1
    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:lx")
        photos = session.exec(
            __import__("sqlmodel").select(PhotoRow).where(PhotoRow.listing_id == "wg-gesucht:lx")
        ).all()
    assert row is not None
    assert row.scrape_status == "full"
    assert row.scraped_at is not None
    assert row.description == "Bright room"
    assert row.kind == "wg"
    assert len(photos) == 1


def test_scraper_marks_partial_listing_as_stub(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def fake_search(*_a, **_kw):
        return [_stub_listing("wg-gesucht:ly")]

    async def fake_scrape_partial(lst: Listing, **_kw):
        # No description / no coords → status should be 'stub'.
        return lst

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch(
            "app.wg_agent.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=fake_scrape_partial),
        ),
    ):
        asyncio.run(agent.run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:ly")
    assert row is not None
    assert row.scrape_status == "stub"


def test_scraper_records_scrape_errors(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def fake_search(*_a, **_kw):
        return [_stub_listing("wg-gesucht:lz")]

    async def fake_scrape_raises(lst: Listing, **_kw):
        raise RuntimeError("boom")

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch(
            "app.wg_agent.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=fake_scrape_raises),
        ),
    ):
        asyncio.run(agent.run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:lz")
    assert row is not None
    assert row.scrape_status == "failed"
    assert row.scrape_error == "boom"


def test_scraper_skips_recently_scraped(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    # Pre-seed a fully scraped listing from now (within the 24h refresh window).
    with Session(engine) as session:
        repo.upsert_global_listing(session, listing=_full_listing("wg-gesucht:fresh"), status="full")

    async def fake_search(*_a, **_kw):
        return [
            Listing(
                id="wg-gesucht:fresh",
                url=HttpUrl("https://www.wg-gesucht.de/fresh.html"),
                title="fresh stub",
                kind="wg",
            )
        ]

    scrape_spy = AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id))

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch("app.wg_agent.browser.anonymous_scrape_listing", new=scrape_spy),
    ):
        scraped = asyncio.run(agent.run_once())

    assert scraped == 0
    scrape_spy.assert_not_called()


def test_scraper_refreshes_stale_listings(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.upsert_global_listing(session, listing=_full_listing("wg-gesucht:stale"), status="full")
        # Push scraped_at back in time beyond the refresh TTL.
        row = session.get(ListingRow, "wg-gesucht:stale")
        assert row is not None
        row.scraped_at = datetime.utcnow() - timedelta(hours=48)
        session.add(row)
        session.commit()

    async def fake_search(*_a, **_kw):
        return [
            Listing(
                id="wg-gesucht:stale",
                url=HttpUrl("https://www.wg-gesucht.de/stale.html"),
                title="stale stub",
                kind="wg",
            )
        ]

    scrape_spy = AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id))

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch("app.wg_agent.browser.anonymous_scrape_listing", new=scrape_spy),
    ):
        scraped = asyncio.run(agent.run_once())

    assert scraped == 1
    scrape_spy.assert_awaited_once()


def test_scraper_deletion_sweep_tombstones_missing_listings(monkeypatch) -> None:
    """After N consecutive passes without seeing a listing, it gets tombstoned."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.upsert_global_listing(session, listing=_full_listing("wg-gesucht:keep"), status="full")
        repo.upsert_global_listing(session, listing=_full_listing("wg-gesucht:gone"), status="full")

    async def fake_search(*_a, **_kw):
        # Only "keep" continues to appear; "gone" is missing from both passes.
        return [
            Listing(
                id="wg-gesucht:keep",
                url=HttpUrl("https://www.wg-gesucht.de/keep.html"),
                title="keep stub",
                kind="wg",
            )
        ]

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch(
            "app.wg_agent.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id)),
        ),
    ):
        asyncio.run(agent.run_once())
        asyncio.run(agent.run_once())

    with Session(engine) as session:
        keep = session.get(ListingRow, "wg-gesucht:keep")
        gone = session.get(ListingRow, "wg-gesucht:gone")
    assert keep is not None
    assert keep.deleted_at is None
    assert keep.scrape_status != "deleted"
    assert gone is not None
    assert gone.scrape_status == "deleted"
    assert gone.deleted_at is not None


def test_scraper_deletion_sweep_resets_counter_when_listing_returns(monkeypatch) -> None:
    """A listing that reappears after being missing for one pass must not be
    tombstoned, and its miss-counter must be cleared."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.upsert_global_listing(session, listing=_full_listing("wg-gesucht:flaky"), status="full")

    calls = {"n": 0}

    async def fake_search(*_a, **_kw):
        calls["n"] += 1
        # Pass 1: missing. Pass 2: reappears.
        if calls["n"] == 1:
            return []
        return [
            Listing(
                id="wg-gesucht:flaky",
                url=HttpUrl("https://www.wg-gesucht.de/flaky.html"),
                title="flaky stub",
                kind="wg",
            )
        ]

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch(
            "app.wg_agent.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id)),
        ),
    ):
        asyncio.run(agent.run_once())
        asyncio.run(agent.run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:flaky")
    assert row is not None
    assert row.deleted_at is None
    assert row.scrape_status != "deleted"
    assert "wg-gesucht:flaky" not in agent._missing_passes["wg-gesucht"]


def test_scraper_per_source_sweep_does_not_tombstone_other_sources(monkeypatch) -> None:
    """G4 verification: a wg-gesucht-only pass does not tombstone Kleinanzeigen
    rows that the agent never tried to see this pass."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.upsert_global_listing(
            session,
            listing=_full_listing("wg-gesucht:keep"),
            status="full",
        )
        # A pre-existing Kleinanzeigen row that this wg-gesucht-only run
        # must not tombstone, even after several passes.
        repo.upsert_global_listing(
            session,
            listing=Listing(
                id="kleinanzeigen:other",
                url=HttpUrl("https://www.kleinanzeigen.de/s-anzeige/x/other-199-6411"),
                title="other source",
                kind="wg",
                description="ok",
                lat=48.1,
                lng=11.5,
            ),
            status="full",
        )

    async def fake_search(*_a, **_kw):
        return [
            Listing(
                id="wg-gesucht:keep",
                url=HttpUrl("https://www.wg-gesucht.de/keep.html"),
                title="keep stub",
                kind="wg",
            )
        ]

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch(
            "app.wg_agent.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id)),
        ),
    ):
        for _ in range(3):  # well beyond SCRAPER_DELETION_PASSES (default 2)
            asyncio.run(agent.run_once())

    with Session(engine) as session:
        other = session.get(ListingRow, "kleinanzeigen:other")
    assert other is not None
    assert other.deleted_at is None
    assert other.scrape_status != "deleted"
