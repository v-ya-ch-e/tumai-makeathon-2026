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


# --- 14-day freshness filter (SCRAPER_MAX_AGE_DAYS, default 14) -------------


class _FakeSource:
    """Minimal `Source` stand-in for freshness tests.

    Pretends to be one of the real sources so the agent's per-source
    deletion sweep / dispatch logic treats namespaced ids the same way it
    does in production. `.search` returns a fixed list of stubs;
    `.scrape_detail` defaults to identity but tests can override it.
    """

    def __init__(
        self,
        *,
        name: str,
        stubs: list[Listing],
        scrape_detail=None,
    ) -> None:
        self.name = name
        self.kind_supported = frozenset({"wg"})
        self.search_page_delay_seconds = 0.0
        self.detail_delay_seconds = 0.0
        self.max_pages = 1
        self.refresh_hours = 24
        self._stubs = stubs
        self._scrape_detail = scrape_detail

    async def search(self, *, kind, profile):  # noqa: ARG002 - unused per protocol
        return list(self._stubs)

    async def scrape_detail(self, stub: Listing) -> Listing:
        if self._scrape_detail is not None:
            return await self._scrape_detail(stub)
        return stub

    def looks_like_block_page(self, text: str, status: int) -> bool:  # noqa: ARG002
        return False


def _stub_with_posted_at(lid: str, *, posted_at) -> Listing:
    bare = lid.split(":", 1)[1] if ":" in lid else lid
    listing = Listing(
        id=lid,
        url=HttpUrl(f"https://example.invalid/{bare}.html"),
        title=f"Stub {bare}",
        kind="wg",
    )
    listing.posted_at = posted_at
    return listing


def test_scraper_drops_stale_tum_living_stub(monkeypatch) -> None:
    """A stub whose `posted_at` is older than the 14-day window must be
    dropped before we even try to deep-scrape it (no upsert)."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    stale = _stub_with_posted_at(
        "tum-living:abc", posted_at=datetime.utcnow() - timedelta(days=30)
    )
    fake_source = _FakeSource(
        name="tum-living",
        stubs=[stale],
        scrape_detail=AsyncMock(),  # must NOT be called
    )

    agent = ScraperAgent(
        city="München",
        max_rent_eur=2000,
        max_pages=1,
        interval_seconds=1,
        refresh_hours=24,
        sources=[fake_source],
    )

    asyncio.run(agent.run_once())

    fake_source._scrape_detail.assert_not_awaited()
    with Session(engine) as session:
        row = session.get(ListingRow, "tum-living:abc")
    assert row is None  # nothing persisted


def test_scraper_keeps_fresh_tum_living_stub(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    fresh = _stub_with_posted_at(
        "tum-living:fresh", posted_at=datetime.utcnow() - timedelta(days=5)
    )

    async def fake_detail(stub: Listing) -> Listing:
        # Mimic a real detail pass: enrich enough to qualify as 'full'.
        stub.description = "tum description"
        stub.lat = 48.1
        stub.lng = 11.5
        return stub

    fake_source = _FakeSource(
        name="tum-living",
        stubs=[fresh],
        scrape_detail=fake_detail,
    )

    agent = ScraperAgent(
        city="München",
        max_rent_eur=2000,
        max_pages=1,
        interval_seconds=1,
        refresh_hours=24,
        sources=[fake_source],
    )

    asyncio.run(agent.run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "tum-living:fresh")
    assert row is not None
    assert row.scrape_status == "full"


def test_scraper_drops_stale_wg_gesucht_stub(monkeypatch) -> None:
    """wg-gesucht stubs carry `posted_at` from the search card; a stale stub
    must be dropped before the detail scrape runs."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    stale_posted_at = datetime.utcnow() - timedelta(days=30)

    async def fake_search(*_a, **_kw):
        stub = Listing(
            id="wg-gesucht:stale",
            url=HttpUrl("https://www.wg-gesucht.de/stale.html"),
            title="stale stub",
            kind="wg",
        )
        stub.posted_at = stale_posted_at
        return [stub]

    scrape_spy = AsyncMock(side_effect=lambda lst, **_kw: _full_listing(lst.id))

    agent = _make_agent()

    with (
        patch("app.wg_agent.browser.anonymous_search", new=AsyncMock(side_effect=fake_search)),
        patch("app.wg_agent.browser.anonymous_scrape_listing", new=scrape_spy),
    ):
        scraped = asyncio.run(agent.run_once())

    assert scraped == 0
    scrape_spy.assert_not_called()
    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:stale")
    assert row is None


def test_scraper_drops_stale_kleinanzeigen_after_detail(monkeypatch) -> None:
    """Kleinanzeigen stubs lack `posted_at` (search cards have no date), so
    the freshness gate fires AFTER the detail fetch sets `posted_at`. A
    stale ad must not be persisted even though we paid for the detail."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    stub_without_date = Listing(
        id="kleinanzeigen:stale",
        url=HttpUrl("https://www.kleinanzeigen.de/s-anzeige/x/stale-199-6411"),
        title="stale KA stub",
        kind="wg",
    )

    async def fake_detail(stub: Listing) -> Listing:
        stub.description = "long ka description"
        stub.lat = 48.1
        stub.lng = 11.5
        stub.posted_at = datetime.utcnow() - timedelta(days=30)
        return stub

    fake_source = _FakeSource(
        name="kleinanzeigen",
        stubs=[stub_without_date],
        scrape_detail=fake_detail,
    )

    agent = ScraperAgent(
        city="München",
        max_rent_eur=2000,
        max_pages=1,
        interval_seconds=1,
        refresh_hours=24,
        sources=[fake_source],
    )

    asyncio.run(agent.run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "kleinanzeigen:stale")
    assert row is None  # detail ran, but the post-detail gate dropped it


def test_parse_wgg_online_value_relative() -> None:
    """Table-driven: the new helper handles relative + absolute + malformed."""
    from app.wg_agent.browser import _parse_wgg_online_value

    now = datetime.utcnow()
    cases = [
        ("3 Minuten", lambda r: r is not None and timedelta(minutes=2) <= now - r <= timedelta(minutes=4)),
        ("1 Stunde", lambda r: r is not None and timedelta(minutes=55) <= now - r <= timedelta(minutes=65)),
        ("25 Minuten", lambda r: r is not None and timedelta(minutes=24) <= now - r <= timedelta(minutes=26)),
        ("2 Tage", lambda r: r is not None and timedelta(days=1, hours=23) <= now - r <= timedelta(days=2, hours=1)),
        ("06.09.2025", lambda r: r == datetime(2025, 9, 6)),
        ("12.03.2026", lambda r: r == datetime(2026, 3, 12)),
        ("", lambda r: r is None),
        ("bogus", lambda r: r is None),
        ("Online-Besichtigung", lambda r: r is None),
        ("32.13.2026", lambda r: r is None),  # invalid date
    ]
    for raw, predicate in cases:
        result = _parse_wgg_online_value(raw)
        assert predicate(result), f"unexpected result for {raw!r}: {result!r}"
