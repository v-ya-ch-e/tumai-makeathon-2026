"""ScraperAgent tests (in-memory DB, mocked browser)."""

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
from app.wg_agent import db as db_module, repo  # noqa: E402
from app.wg_agent.db_models import ListingRow, PhotoRow  # noqa: E402
from app.wg_agent.models import Listing  # noqa: E402


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _full_listing(
    lid: str, *, lat: float = 48.1, lng: float = 11.5, description: str = "Bright room"
) -> Listing:
    return Listing(
        id=lid,
        url=HttpUrl(f"https://www.wg-gesucht.de/{lid}.html"),
        title=f"Listing {lid}",
        price_eur=800,
        lat=lat,
        lng=lng,
        description=description,
        photo_urls=[f"https://img.wg-gesucht.de/{lid}-a.jpg"],
    )


def _stub_listing(lid: str) -> Listing:
    """A partial listing (no description / coords) — should persist as status='stub'."""
    return Listing(
        id=lid,
        url=HttpUrl(f"https://www.wg-gesucht.de/{lid}.html"),
        title=f"Partial {lid}",
    )


def test_scraper_writes_full_listing_and_photos(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def fake_search(*_a, **_kw):
        return [Listing(id="lx", url=HttpUrl("https://www.wg-gesucht.de/lx.html"), title="stub Lx")]

    async def fake_scrape(lst: Listing, **_kw):
        return _full_listing(lst.id)

    agent = ScraperAgent(
        city="München",
        max_rent_eur=2000,
        max_pages=1,
        interval_seconds=1,
        refresh_hours=24,
    )

    with (
        patch("app.scraper.agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch("app.scraper.agent.browser.anonymous_scrape_listing", new=AsyncMock(side_effect=fake_scrape)),
    ):
        scraped = asyncio.run(agent.run_once())

    assert scraped == 1
    with Session(engine) as session:
        row = session.get(ListingRow, "lx")
        photos = session.exec(
            __import__("sqlmodel").select(PhotoRow).where(PhotoRow.listing_id == "lx")
        ).all()
    assert row is not None
    assert row.scrape_status == "full"
    assert row.scraped_at is not None
    assert row.description == "Bright room"
    assert len(photos) == 1


def test_scraper_marks_partial_listing_as_stub(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def fake_search(*_a, **_kw):
        return [_stub_listing("ly")]

    async def fake_scrape_partial(lst: Listing, **_kw):
        # No description / no coords → status should be 'stub'.
        return lst

    agent = ScraperAgent(max_pages=1, interval_seconds=1, refresh_hours=24)

    with (
        patch("app.scraper.agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch(
            "app.scraper.agent.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=fake_scrape_partial),
        ),
    ):
        asyncio.run(agent.run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "ly")
    assert row is not None
    assert row.scrape_status == "stub"


def test_scraper_records_scrape_errors(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def fake_search(*_a, **_kw):
        return [_stub_listing("lz")]

    async def fake_scrape_raises(lst: Listing, **_kw):
        raise RuntimeError("boom")

    agent = ScraperAgent(max_pages=1, interval_seconds=1, refresh_hours=24)

    with (
        patch("app.scraper.agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch(
            "app.scraper.agent.browser.anonymous_scrape_listing",
            new=AsyncMock(side_effect=fake_scrape_raises),
        ),
    ):
        asyncio.run(agent.run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "lz")
    assert row is not None
    assert row.scrape_status == "failed"
    assert row.scrape_error == "boom"


def test_scraper_skips_recently_scraped(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    # Pre-seed a fully scraped listing from 1 hour ago.
    with Session(engine) as session:
        repo.upsert_global_listing(session, listing=_full_listing("fresh"), status="full")

    async def fake_search(*_a, **_kw):
        return [
            Listing(
                id="fresh",
                url=HttpUrl("https://www.wg-gesucht.de/fresh.html"),
                title="fresh stub",
            )
        ]

    scrape_spy = AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id))

    agent = ScraperAgent(max_pages=1, interval_seconds=1, refresh_hours=24)

    with (
        patch("app.scraper.agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch("app.scraper.agent.browser.anonymous_scrape_listing", new=scrape_spy),
    ):
        scraped = asyncio.run(agent.run_once())

    assert scraped == 0
    scrape_spy.assert_not_called()


def test_scraper_refreshes_stale_listings(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.upsert_global_listing(session, listing=_full_listing("stale"), status="full")
        # Push scraped_at back in time beyond the refresh TTL.
        row = session.get(ListingRow, "stale")
        assert row is not None
        row.scraped_at = datetime.utcnow() - timedelta(hours=48)
        session.add(row)
        session.commit()

    async def fake_search(*_a, **_kw):
        return [
            Listing(
                id="stale",
                url=HttpUrl("https://www.wg-gesucht.de/stale.html"),
                title="stale stub",
            )
        ]

    scrape_spy = AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id))

    agent = ScraperAgent(max_pages=1, interval_seconds=1, refresh_hours=24)

    with (
        patch("app.scraper.agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch("app.scraper.agent.browser.anonymous_scrape_listing", new=scrape_spy),
    ):
        scraped = asyncio.run(agent.run_once())

    assert scraped == 1
    scrape_spy.assert_awaited_once()
