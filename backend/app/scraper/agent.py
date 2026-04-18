"""Background scraper agent.

Periodically hits wg-gesucht search pages, deep-scrapes every new listing it
has not yet saved (or whose row is older than `SCRAPER_REFRESH_HOURS`), and
writes the full listing + photos to the shared MySQL pool. Never scores,
never touches hunts.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session

from ..wg_agent import browser, repo
from ..wg_agent import db as db_module
from ..wg_agent.db_models import ListingRow
from ..wg_agent.models import Listing, SearchProfile

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("Invalid %s=%r, falling back to %d", name, raw, default)
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default) or default


class ScraperAgent:
    """One-process loop that keeps the global `ListingRow` pool fresh."""

    def __init__(
        self,
        *,
        city: Optional[str] = None,
        max_rent_eur: Optional[int] = None,
        max_pages: Optional[int] = None,
        interval_seconds: Optional[int] = None,
        refresh_hours: Optional[int] = None,
    ) -> None:
        self._city = city if city is not None else _env_str("SCRAPER_CITY", "München")
        self._max_rent = (
            max_rent_eur if max_rent_eur is not None else _env_int("SCRAPER_MAX_RENT", 2000)
        )
        self._max_pages = (
            max_pages if max_pages is not None else _env_int("SCRAPER_MAX_PAGES", 2)
        )
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else _env_int("SCRAPER_INTERVAL_SECONDS", 300)
        )
        self._refresh_hours = (
            refresh_hours
            if refresh_hours is not None
            else _env_int("SCRAPER_REFRESH_HOURS", 24)
        )
        self._deletion_passes = _env_int("SCRAPER_DELETION_PASSES", 2)
        self._missing_passes: dict[str, int] = {}

    def _search_profile(self) -> SearchProfile:
        return SearchProfile(city=self._city, max_rent_eur=self._max_rent)

    def _needs_scrape(self, existing: Optional[ListingRow]) -> bool:
        if existing is None:
            return True
        if existing.scrape_status != "full":
            return True
        if existing.scraped_at is None:
            return True
        cutoff = datetime.utcnow() - timedelta(hours=self._refresh_hours)
        return existing.scraped_at < cutoff

    def _status_for(self, listing: Listing) -> str:
        if not listing.description:
            return "stub"
        if listing.lat is None or listing.lng is None:
            return "stub"
        return "full"

    async def _scrape_and_save(self, stub: Listing) -> None:
        try:
            enriched = await browser.anonymous_scrape_listing(stub, req_city=self._city)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scrape failed for %s: %s", stub.id, exc)
            with Session(db_module.engine) as session:
                repo.upsert_global_listing(
                    session,
                    listing=stub,
                    status="failed",
                    scrape_error=str(exc),
                )
            return

        status = self._status_for(enriched)
        with Session(db_module.engine) as session:
            repo.upsert_global_listing(session, listing=enriched, status=status)
            repo.save_photos(
                session,
                listing_id=enriched.id,
                urls=list(enriched.photo_urls),
            )

    async def run_once(self) -> int:
        """One search + deep-scrape pass. Returns the number of listings written."""
        sp = self._search_profile()
        try:
            found = await browser.anonymous_search(sp, max_pages=self._max_pages)
        except Exception as exc:  # noqa: BLE001
            logger.error("Search failed: %s", exc)
            return 0

        logger.info(
            "Scraper: %d listings from search (city=%s, max_rent=%d, pages=%d)",
            len(found),
            self._city,
            self._max_rent,
            self._max_pages,
        )

        scraped = 0
        for stub in found:
            with Session(db_module.engine) as session:
                existing = session.get(ListingRow, stub.id)
            if not self._needs_scrape(existing):
                continue
            await self._scrape_and_save(stub)
            scraped += 1
        logger.info("Scraper: scraped %d listings this pass", scraped)

        self._sweep_deletions({stub.id for stub in found})
        return scraped

    def _sweep_deletions(self, seen_ids: set[str]) -> None:
        """Diff current search ids against the active pool and tombstone listings
        that have been missing for `self._deletion_passes` consecutive passes."""
        with Session(db_module.engine) as session:
            active_ids = repo.list_active_listing_ids(session)

        # Reset counters for anything that reappeared in the search.
        for lid in list(self._missing_passes.keys()):
            if lid in seen_ids:
                del self._missing_passes[lid]

        missing = active_ids - seen_ids
        tombstoned: list[str] = []
        for lid in missing:
            count = self._missing_passes.get(lid, 0) + 1
            if count >= self._deletion_passes:
                with Session(db_module.engine) as session:
                    repo.mark_listing_deleted(session, listing_id=lid)
                tombstoned.append(lid)
                self._missing_passes.pop(lid, None)
            else:
                self._missing_passes[lid] = count

        logger.info(
            "Scraper: deletion sweep found %d missing, tombstoned %d",
            len(missing),
            len(tombstoned),
        )

    async def run_forever(self) -> None:
        logger.info(
            "Starting scraper agent: interval=%ds, refresh_after=%dh",
            self._interval,
            self._refresh_hours,
        )
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Scraper pass raised; sleeping before retry")
            await asyncio.sleep(self._interval)
