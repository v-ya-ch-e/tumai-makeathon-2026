"""Background scraper agent: multi-source loop.

Drives a list of `Source` plugins (`backend/app/scraper/sources/*.py`)
sequentially per pass, deep-scrapes every new listing it has not yet
saved (or whose row is older than the source's `refresh_hours`), and
writes the full listing + photos to the shared MySQL pool. Never
scores, never touches per-user matchers.

`SCRAPER_ENABLED_SOURCES` (env, comma-separated; default `wg-gesucht`)
selects which sources run.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session

from ..wg_agent import repo
from ..wg_agent import db as db_module
from ..wg_agent.db_models import ListingRow
from ..wg_agent.models import Listing, SearchProfile
from .sources import Source, build_sources

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
    """Multi-source loop that keeps the global `ListingRow` pool fresh."""

    def __init__(
        self,
        *,
        city: Optional[str] = None,
        max_rent_eur: Optional[int] = None,
        max_pages: Optional[int] = None,
        interval_seconds: Optional[int] = None,
        refresh_hours: Optional[int] = None,
        sources: Optional[list[Source]] = None,
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
        # Kept for back-compat: per-source `refresh_hours` overrides this for
        # newer sources, but `_needs_scrape` falls back to it for any source
        # whose plugin doesn't declare its own threshold.
        self._refresh_hours = (
            refresh_hours
            if refresh_hours is not None
            else _env_int("SCRAPER_REFRESH_HOURS", 24)
        )
        self._deletion_passes = _env_int("SCRAPER_DELETION_PASSES", 2)
        self._sources: list[Source] = sources if sources is not None else build_sources()
        # Per-source miss counters: { source_name: { listing_id: missed_passes } }.
        self._missing_passes: dict[str, dict[str, int]] = {
            s.name: {} for s in self._sources
        }

    def _search_profile(self) -> SearchProfile:
        return SearchProfile(city=self._city, max_rent_eur=self._max_rent)

    def _refresh_hours_for(self, source: Source) -> int:
        return getattr(source, "refresh_hours", self._refresh_hours)

    def _needs_scrape(self, existing: Optional[ListingRow], source: Source) -> bool:
        if existing is None:
            return True
        if existing.scrape_status != "full":
            return True
        if existing.scraped_at is None:
            return True
        cutoff = datetime.utcnow() - timedelta(hours=self._refresh_hours_for(source))
        return existing.scraped_at < cutoff

    def _status_for(self, listing: Listing) -> str:
        if not listing.description:
            return "stub"
        if listing.lat is None or listing.lng is None:
            return "stub"
        return "full"

    async def _scrape_and_save_via(self, source: Source, stub: Listing) -> None:
        try:
            enriched = await source.scrape_detail(stub)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] scrape failed for %s: %s", source.name, stub.id, exc)
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

    async def _run_source(self, source: Source) -> int:
        sp = self._search_profile()
        seen_for_source: set[str] = set()
        scraped = 0

        for kind in sorted(source.kind_supported):
            try:
                stubs = await source.search(kind=kind, profile=sp)
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] search(kind=%s) failed: %s", source.name, kind, exc)
                continue

            logger.info(
                "[%s] kind=%s: %d stubs returned (city=%s)",
                source.name,
                kind,
                len(stubs),
                self._city,
            )

            for stub in stubs:
                seen_for_source.add(stub.id)
                with Session(db_module.engine) as session:
                    existing = session.get(ListingRow, stub.id)
                if not self._needs_scrape(existing, source):
                    continue
                await self._scrape_and_save_via(source, stub)
                scraped += 1

        self._sweep_deletions_for(source, seen_for_source)
        return scraped

    async def run_once(self) -> int:
        """One cross-source pass. Returns the number of listings scraped."""
        total = 0
        for source in self._sources:
            try:
                total += await self._run_source(source)
            except Exception:  # noqa: BLE001
                logger.exception("Scraper source %s pass failed", source.name)
        logger.info("Scraper: scraped %d listings this pass across %d sources", total, len(self._sources))
        return total

    def _sweep_deletions_for(self, source: Source, seen_ids: set[str]) -> None:
        """Per-source deletion sweep.

        Diffs `seen_ids` (collected across every kind this source iterated)
        against `repo.list_active_listing_ids(source=source.name)` and
        tombstones any listing missing for `self._deletion_passes`
        consecutive passes. Per-source scoping means a wg-gesucht-only
        pass cannot tombstone Kleinanzeigen / TUM Living rows.
        """
        with Session(db_module.engine) as session:
            active_ids = repo.list_active_listing_ids(session, source=source.name)

        misses = self._missing_passes.setdefault(source.name, {})

        # Reset counters for anything that reappeared this pass.
        for lid in list(misses.keys()):
            if lid in seen_ids:
                del misses[lid]

        missing = active_ids - seen_ids
        tombstoned: list[str] = []
        for lid in missing:
            count = misses.get(lid, 0) + 1
            if count >= self._deletion_passes:
                with Session(db_module.engine) as session:
                    repo.mark_listing_deleted(session, listing_id=lid)
                tombstoned.append(lid)
                misses.pop(lid, None)
            else:
                misses[lid] = count

        logger.info(
            "[%s] deletion sweep: %d missing, %d tombstoned",
            source.name,
            len(missing),
            len(tombstoned),
        )

    async def run_forever(self) -> None:
        logger.info(
            "Starting scraper agent: interval=%ds, sources=%s",
            self._interval,
            ",".join(s.name for s in self._sources),
        )
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Scraper pass raised; sleeping before retry")
            await asyncio.sleep(self._interval)
