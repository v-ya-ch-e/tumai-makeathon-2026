"""ScraperAgent tests (in-memory DB, fake Source plugin).

`ScraperAgent` consumes `Source` plugins from `app/scraper/sources/`.
Plugins expose `search_pages` as an async iterator (one yield per source
page); the agent walks up to `SCRAPER_MAX_PAGES` per `(source, kind)`
and drops stale stubs without persisting (skip-and-continue, ADR-027).
These tests drive the agent through `_FakeSource` fixtures rather than
the real wg-gesucht network seam, so the per-page control flow can be
exercised without hitting `httpx`.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet
from pydantic import HttpUrl
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("WG_SECRET_KEY", Fernet.generate_key().decode())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.scraper import agent as agent_module  # noqa: E402
from app.scraper.agent import ScraperAgent  # noqa: E402
from app.scraper.enricher import EnrichmentDiff  # noqa: E402
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
    lid: str,
    *,
    lat: float = 48.1,
    lng: float = 11.5,
    description: str = "Bright room",
    posted_at: Optional[datetime] = None,
) -> Listing:
    bare = lid.split(":", 1)[1] if ":" in lid else lid
    listing = Listing(
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
    if posted_at is not None:
        listing.posted_at = posted_at
    return listing


def _stub_listing(lid: str, *, posted_at: Optional[datetime] = None) -> Listing:
    bare = lid.split(":", 1)[1] if ":" in lid else lid
    listing = Listing(
        id=lid,
        url=HttpUrl(f"https://www.wg-gesucht.de/{bare}.html"),
        title=f"Partial {bare}",
        kind="wg",
    )
    if posted_at is not None:
        listing.posted_at = posted_at
    return listing


def _fresh() -> datetime:
    return datetime.utcnow() - timedelta(days=1)


def _stale() -> datetime:
    return datetime.utcnow() - timedelta(days=30)


class _FakeSource:
    """Minimal `Source` stand-in for agent tests.

    `pages` is a list of stub batches. The generator yields each batch
    once, in order, and stops. `scrape_detail` defaults to copying the
    stub into a fully-scraped listing; tests can override.
    """

    def __init__(
        self,
        *,
        name: str = "wg-gesucht",
        pages: Optional[list[list[Listing]]] = None,
        kind_supported: Optional[frozenset[str]] = None,
        scrape_detail=None,
    ) -> None:
        self.name = name
        self.kind_supported = kind_supported or frozenset({"wg"})
        self.search_page_delay_seconds = 0.0
        self.detail_delay_seconds = 0.0
        self.refresh_hours = 24
        self._pages: list[list[Listing]] = pages or []
        self._scrape_detail = scrape_detail
        self.detail_calls: list[str] = []
        self.search_pages_calls = 0
        self.pages_yielded = 0

    async def search_pages(
        self, *, kind, profile  # noqa: ARG002 - signature pinned by Source protocol
    ) -> AsyncIterator[list[Listing]]:
        self.search_pages_calls += 1
        for batch in self._pages:
            self.pages_yielded += 1
            yield batch

    async def scrape_detail(self, stub: Listing) -> Listing:
        self.detail_calls.append(stub.id)
        if self._scrape_detail is not None:
            return await self._scrape_detail(stub)
        return _full_listing(
            stub.id,
            description=stub.description or "Bright room",
            posted_at=stub.posted_at,
        )

    def looks_like_block_page(self, text: str, status: int) -> bool:  # noqa: ARG002
        return False


def _agent(
    *,
    sources: list[_FakeSource],
    max_age_days: Optional[int] = None,
    max_pages: Optional[int] = None,
    enrich_enabled: bool = False,
    enrich_min_desc_chars: int = 200,
    kind_filter: str = "both",
) -> ScraperAgent:
    if max_age_days is not None:
        os.environ["SCRAPER_MAX_AGE_DAYS"] = str(max_age_days)
    if max_pages is not None:
        os.environ["SCRAPER_MAX_PAGES"] = str(max_pages)
    return ScraperAgent(
        city="München",
        max_rent_eur=2000,
        interval_seconds=1,
        refresh_hours=24,
        sources=sources,
        enrich_enabled=enrich_enabled,
        enrich_min_desc_chars=enrich_min_desc_chars,
        kind_filter=kind_filter,
    )


# --- Existing scraper-loop coverage (now driven via _FakeSource) -------------


def test_scraper_writes_full_listing_and_photos(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    fake = _FakeSource(pages=[[_stub_listing("wg-gesucht:lx", posted_at=_fresh())]])
    asyncio.run(_agent(sources=[fake]).run_once())

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

    async def partial(stub: Listing) -> Listing:
        return stub

    fake = _FakeSource(
        pages=[[_stub_listing("wg-gesucht:ly", posted_at=_fresh())]],
        scrape_detail=partial,
    )
    asyncio.run(_agent(sources=[fake]).run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:ly")
    assert row is not None
    assert row.scrape_status == "stub"


def test_scraper_records_scrape_errors(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    async def boom(_stub: Listing) -> Listing:
        raise RuntimeError("boom")

    fake = _FakeSource(
        pages=[[_stub_listing("wg-gesucht:lz", posted_at=_fresh())]],
        scrape_detail=boom,
    )
    asyncio.run(_agent(sources=[fake]).run_once())

    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:lz")
    assert row is not None
    assert row.scrape_status == "failed"
    assert row.scrape_error == "boom"


def test_scraper_skips_recently_scraped(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.upsert_global_listing(
            session, listing=_full_listing("wg-gesucht:fresh"), status="full"
        )

    fake = _FakeSource(
        pages=[[_stub_listing("wg-gesucht:fresh", posted_at=_fresh())]]
    )
    scraped = asyncio.run(_agent(sources=[fake]).run_once())

    assert scraped == 0
    # The stub was fresh per `posted_at` (so the per-stub stop didn't fire)
    # and already fully scraped in the DB, so `_needs_scrape` short-circuited
    # and no detail fetch was made.
    assert fake.detail_calls == []


def test_scraper_refreshes_stale_listings(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        repo.upsert_global_listing(
            session, listing=_full_listing("wg-gesucht:stale"), status="full"
        )
        row = session.get(ListingRow, "wg-gesucht:stale")
        assert row is not None
        row.scraped_at = datetime.utcnow() - timedelta(hours=48)
        session.add(row)
        session.commit()

    fake = _FakeSource(
        pages=[[_stub_listing("wg-gesucht:stale", posted_at=_fresh())]]
    )
    scraped = asyncio.run(_agent(sources=[fake]).run_once())

    assert scraped == 1
    assert fake.detail_calls == ["wg-gesucht:stale"]


# --- Per-stub freshness drop (skip-and-continue, ADR-027) ------------------


def test_skips_stale_stubs_and_continues(monkeypatch) -> None:
    """Stale stubs are dropped without persisting; the loop keeps
    walking the rest of the page and remaining pages (up to the
    `SCRAPER_MAX_PAGES` cap). No detail fetch is made for stale stubs."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    p0 = [
        _stub_listing("wg-gesucht:p0a", posted_at=_fresh()),
        _stub_listing("wg-gesucht:p0b", posted_at=_stale()),  # dropped
        _stub_listing("wg-gesucht:p0c", posted_at=_fresh()),  # still scraped
        _stub_listing("wg-gesucht:p0d", posted_at=_stale()),  # dropped
    ]
    p1 = [_stub_listing("wg-gesucht:p1a", posted_at=_fresh())]
    fake = _FakeSource(pages=[p0, p1])

    scraped = asyncio.run(_agent(sources=[fake]).run_once())

    assert scraped == 3
    assert set(fake.detail_calls) == {
        "wg-gesucht:p0a",
        "wg-gesucht:p0c",
        "wg-gesucht:p1a",
    }
    with Session(engine) as session:
        for lid in ("wg-gesucht:p0a", "wg-gesucht:p0c", "wg-gesucht:p1a"):
            assert session.get(ListingRow, lid) is not None, lid
        for lid in ("wg-gesucht:p0b", "wg-gesucht:p0d"):
            assert session.get(ListingRow, lid) is None, lid


def test_kleinanzeigen_drops_stale_detail_and_continues(monkeypatch) -> None:
    """Kleinanzeigen stubs lack `posted_at`. The agent only learns the
    date after `scrape_detail`; a stale detail is dropped without
    persisting and the walk continues (the cost of one detail fetch
    per stale ad is the price of the date being detail-only)."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    page = [
        _stub_listing("kleinanzeigen:a"),
        _stub_listing("kleinanzeigen:b"),  # detail reveals stale → dropped
        _stub_listing("kleinanzeigen:c"),
    ]

    async def detail(stub: Listing) -> Listing:
        if stub.id == "kleinanzeigen:b":
            return _full_listing(stub.id, posted_at=_stale())
        return _full_listing(stub.id, posted_at=_fresh())

    fake = _FakeSource(name="kleinanzeigen", pages=[page], scrape_detail=detail)

    scraped = asyncio.run(_agent(sources=[fake]).run_once())

    assert scraped == 2
    assert fake.detail_calls == [
        "kleinanzeigen:a",
        "kleinanzeigen:b",
        "kleinanzeigen:c",
    ]
    with Session(engine) as session:
        for lid in ("kleinanzeigen:a", "kleinanzeigen:c"):
            assert session.get(ListingRow, lid) is not None, lid
        assert session.get(ListingRow, "kleinanzeigen:b") is None


def test_unknown_freshness_keeps_paginating(monkeypatch) -> None:
    """If neither the stub nor the detail set `posted_at`, the agent
    must keep going — better to over-scrape than to silently drop
    everything on a parser regression."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    p0 = [_stub_listing("kleinanzeigen:a")]
    p1 = [_stub_listing("kleinanzeigen:b")]

    async def detail_no_date(stub: Listing) -> Listing:
        return _full_listing(stub.id, description="ok")

    fake = _FakeSource(
        name="kleinanzeigen", pages=[p0, p1], scrape_detail=detail_no_date,
    )

    scraped = asyncio.run(_agent(sources=[fake]).run_once())

    assert scraped == 2
    assert fake.pages_yielded == 2


# --- Page cap (`SCRAPER_MAX_PAGES`) ----------------------------------------


def test_max_pages_caps_per_source_kind(monkeypatch) -> None:
    """The agent walks at most `max_pages` pages per (source, kind).
    Pages past the cap are not requested, regardless of freshness."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    pages = [
        [_stub_listing(f"wg-gesucht:p{i}", posted_at=_fresh())]
        for i in range(8)
    ]
    fake = _FakeSource(pages=pages)

    scraped = asyncio.run(_agent(sources=[fake], max_pages=3).run_once())

    assert scraped == 3
    assert fake.pages_yielded == 3
    assert fake.detail_calls == [
        "wg-gesucht:p0",
        "wg-gesucht:p1",
        "wg-gesucht:p2",
    ]


def test_max_pages_applies_independently_per_kind(monkeypatch) -> None:
    """Cap is per (source, kind), not summed across kinds — a 3-page
    cap with two kinds yields up to 6 pages of work for the source."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    yielded: dict[str, int] = {}

    class _PerKindCounter(_FakeSource):
        async def search_pages(self, *, kind, profile):  # noqa: ARG002
            self.search_pages_calls += 1
            for i in range(8):
                yielded[kind] = yielded.get(kind, 0) + 1
                yield [
                    _stub_listing(
                        f"kleinanzeigen:{kind}-p{i}",
                        posted_at=_fresh(),
                    )
                ]

    fake = _PerKindCounter(
        name="kleinanzeigen",
        kind_supported=frozenset({"wg", "flat"}),
    )
    scraped = asyncio.run(_agent(sources=[fake], max_pages=3).run_once())

    # Two kinds × 3 pages × 1 stub per page = 6.
    assert scraped == 6
    # Each generator yields page #N just before the agent loop checks
    # `page_index < max_pages`. So with max_pages=3 the count is 3 per
    # kind (page 0, 1, 2 are all yielded; page 3 never gets yielded
    # because the agent never calls __anext__ a fourth time).
    assert yielded == {"wg": 3, "flat": 3}


# --- SCRAPER_KIND filter ----------------------------------------------------


def test_kind_filter_wg_skips_flat_only_pass(monkeypatch) -> None:
    """`kind_filter='wg'` must skip the flat vertical entirely."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    captured_kinds: list[str] = []

    class _RecordingSource(_FakeSource):
        async def search_pages(self, *, kind, profile):  # noqa: ARG002
            captured_kinds.append(kind)
            yield [_stub_listing(f"kleinanzeigen:{kind}", posted_at=_fresh())]

    fake = _RecordingSource(
        name="kleinanzeigen",
        kind_supported=frozenset({"wg", "flat"}),
    )
    asyncio.run(_agent(sources=[fake], kind_filter="wg").run_once())

    assert captured_kinds == ["wg"]


def test_kind_filter_flat_skips_wg_only_source(monkeypatch) -> None:
    """`kind_filter='flat'` against a wg-only source produces zero work."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    fake = _FakeSource(
        pages=[[_stub_listing("wg-gesucht:x", posted_at=_fresh())]],
        kind_supported=frozenset({"wg"}),
    )
    scraped = asyncio.run(_agent(sources=[fake], kind_filter="flat").run_once())

    assert scraped == 0
    assert fake.search_pages_calls == 0
    assert fake.detail_calls == []


def test_kind_filter_both_runs_every_supported_kind(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    captured_kinds: list[str] = []

    class _RecordingSource(_FakeSource):
        async def search_pages(self, *, kind, profile):  # noqa: ARG002
            captured_kinds.append(kind)
            yield [_stub_listing(f"kleinanzeigen:{kind}", posted_at=_fresh())]

    fake = _RecordingSource(
        name="kleinanzeigen",
        kind_supported=frozenset({"wg", "flat"}),
    )
    asyncio.run(_agent(sources=[fake], kind_filter="both").run_once())

    assert sorted(captured_kinds) == ["flat", "wg"]


# --- Enrichment ---------------------------------------------------------------


def test_apply_enrichment_fills_only_when_missing() -> None:
    listing = _full_listing("wg-gesucht:e1")
    listing.furnished = None
    listing.languages = []  # not None — must NOT be overwritten

    diff = EnrichmentDiff(furnished=True, languages=["Deutsch"])
    agent = _agent(sources=[_FakeSource()])
    applied = agent._apply_enrichment(listing, diff)

    assert applied == ["furnished"]
    assert listing.furnished is True
    assert listing.languages == []


def test_apply_enrichment_refuses_to_overwrite() -> None:
    listing = _full_listing("wg-gesucht:e2")
    listing.furnished = False  # explicit no
    listing.smoking_ok = None

    diff = EnrichmentDiff(furnished=True, smoking_ok=False)
    agent = _agent(sources=[_FakeSource()])
    applied = agent._apply_enrichment(listing, diff)

    assert applied == ["smoking_ok"]
    assert listing.furnished is False
    assert listing.smoking_ok is False


def test_apply_enrichment_rejects_invalid_diff(monkeypatch) -> None:
    """If the merged candidate fails `Listing.model_validate`, the
    entire diff is dropped and the listing is unchanged. Most schema
    violations are caught at the diff layer (`EnrichmentDiff` rejects
    them before they reach `_apply_enrichment`); this test simulates a
    future schema drift by patching `Listing.model_validate` to raise."""
    listing = _full_listing("wg-gesucht:e3")
    listing.furnished = None
    listing.smoking_ok = None

    diff = EnrichmentDiff(furnished=True, smoking_ok=False)

    from app.wg_agent import models as models_module

    def _raise(_data):
        raise ValueError("simulated future schema drift")

    monkeypatch.setattr(models_module.Listing, "model_validate", _raise)

    agent = _agent(sources=[_FakeSource()])
    applied = agent._apply_enrichment(listing, diff)

    assert applied == []
    assert listing.furnished is None
    assert listing.smoking_ok is None


def test_enrichment_disabled_skips_call(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    enrich_spy = MagicMock(return_value=EnrichmentDiff())
    monkeypatch.setattr(agent_module, "enrich_listing", enrich_spy)

    page = [_stub_listing("wg-gesucht:no-enrich", posted_at=_fresh())]
    fake = _FakeSource(pages=[page])

    asyncio.run(_agent(sources=[fake], enrich_enabled=False).run_once())

    enrich_spy.assert_not_called()


def test_enrichment_skipped_when_no_missing_fields(monkeypatch) -> None:
    """A fully-populated listing has nothing to enrich; we must not call
    OpenAI for it even with the feature flag on."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    enrich_spy = MagicMock(return_value=EnrichmentDiff())
    monkeypatch.setattr(agent_module, "enrich_listing", enrich_spy)

    long_desc = "x" * 1000

    async def detail(stub: Listing) -> Listing:
        return Listing(
            id=stub.id,
            url=HttpUrl("https://www.wg-gesucht.de/done.html"),
            title="Done",
            kind="wg",
            city="München",
            district="Neuhausen",
            address="Hauptstr 1",
            price_eur=1000,
            size_m2=18.0,
            wg_size=3,
            available_from=datetime.utcnow().date(),
            available_to=datetime.utcnow().date(),
            furnished=True,
            pets_allowed=False,
            smoking_ok=False,
            languages=["Deutsch"],
            lat=48.1,
            lng=11.5,
            description=long_desc,
            posted_at=_fresh(),
            # Matcher v2 added price_basis / deposit_months /
            # furniture_buyout_eur to ENRICHABLE_FIELDS; populate them so
            # this "fully populated" fixture actually has nothing left to
            # enrich.
            price_basis="warm",
            deposit_months=2.0,
            furniture_buyout_eur=0,
        )

    page = [_stub_listing("wg-gesucht:done", posted_at=_fresh())]
    fake = _FakeSource(pages=[page], scrape_detail=detail)

    asyncio.run(
        _agent(sources=[fake], enrich_enabled=True, enrich_min_desc_chars=200).run_once()
    )

    enrich_spy.assert_not_called()


def test_enrichment_skipped_when_description_too_short(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    enrich_spy = MagicMock(return_value=EnrichmentDiff())
    monkeypatch.setattr(agent_module, "enrich_listing", enrich_spy)

    async def detail(stub: Listing) -> Listing:
        return _full_listing(stub.id, description="too short", posted_at=_fresh())

    page = [_stub_listing("wg-gesucht:short", posted_at=_fresh())]
    fake = _FakeSource(pages=[page], scrape_detail=detail)

    asyncio.run(
        _agent(sources=[fake], enrich_enabled=True, enrich_min_desc_chars=200).run_once()
    )

    enrich_spy.assert_not_called()


def test_enrichment_runs_and_persists_filled_fields(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)

    enrich_spy = MagicMock(
        return_value=EnrichmentDiff(furnished=True, wg_size=3)
    )
    monkeypatch.setattr(agent_module, "enrich_listing", enrich_spy)

    long_desc = "Die WG hat drei Bewohner. Das Zimmer ist möbliert. " + ("x" * 200)

    async def detail(stub: Listing) -> Listing:
        # Returns a listing with all enrichable fields set EXCEPT
        # furnished and wg_size, plus a long-enough description.
        return Listing(
            id=stub.id,
            url=HttpUrl("https://www.wg-gesucht.de/sparse.html"),
            title="Sparse",
            kind="wg",
            city="München",
            district="Neuhausen",
            address="Hauptstr 1",
            price_eur=1000,
            size_m2=18.0,
            wg_size=None,
            available_from=datetime.utcnow().date(),
            available_to=datetime.utcnow().date(),
            furnished=None,
            pets_allowed=False,
            smoking_ok=False,
            languages=["Deutsch"],
            lat=48.1,
            lng=11.5,
            description=long_desc,
            posted_at=_fresh(),
        )

    page = [_stub_listing("wg-gesucht:sparse", posted_at=_fresh())]
    fake = _FakeSource(pages=[page], scrape_detail=detail)

    asyncio.run(
        _agent(sources=[fake], enrich_enabled=True, enrich_min_desc_chars=50).run_once()
    )

    enrich_spy.assert_called_once()
    with Session(engine) as session:
        row = session.get(ListingRow, "wg-gesucht:sparse")
    assert row is not None
    assert row.furnished is True
    assert row.wg_size == 3


# --- Existing browser-helper unit test (kept) -------------------------------


def test_parse_wgg_online_value_relative() -> None:
    """Table-driven: the helper handles relative + absolute + malformed."""
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
        ("32.13.2026", lambda r: r is None),
    ]
    for raw, predicate in cases:
        result = _parse_wgg_online_value(raw)
        assert predicate(result), f"unexpected result for {raw!r}: {result!r}"
