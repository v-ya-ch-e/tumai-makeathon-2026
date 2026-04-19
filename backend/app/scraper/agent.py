"""Background scraper agent: multi-source loop.

Drives a list of `Source` plugins (`backend/app/scraper/sources/*.py`)
sequentially per pass. For each `(source, kind)` it walks the source's
`search_pages` async iterator one page at a time, deciding after each
page whether to keep going based on the freshness of the first stub on
that page. Any page whose first stub is older than the
`SCRAPER_MAX_AGE_DAYS` cutoff terminates pagination for that
`(source, kind)`. There is no per-source `max_pages` ceiling; pagination
also stops on an empty page or after the first HTTP error.

If `SCRAPER_ENRICH_ENABLED` is set, the agent calls a narrow LLM
enrichment step between `scrape_detail` and `repo.upsert_global_listing`
to fill missing structured fields that the description states clearly
(see `enricher.py`). Coordinates remain on the deterministic geocoder
path; the LLM never produces lat/lng.

Never scores, never touches per-user matchers.

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
from ..wg_agent import brain
from ..wg_agent.db_models import ListingRow
from ..wg_agent.models import Listing, SearchProfile
from .enricher import ENRICHABLE_FIELDS, EnrichmentDiff, enrich_listing
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ScraperAgent:
    """Multi-source loop that keeps the global `ListingRow` pool fresh."""

    def __init__(
        self,
        *,
        city: Optional[str] = None,
        max_rent_eur: Optional[int] = None,
        interval_seconds: Optional[int] = None,
        refresh_hours: Optional[int] = None,
        sources: Optional[list[Source]] = None,
        enrich_enabled: Optional[bool] = None,
        enrich_model: Optional[str] = None,
        enrich_min_desc_chars: Optional[int] = None,
    ) -> None:
        self._city = city if city is not None else _env_str("SCRAPER_CITY", "München")
        self._max_rent = (
            max_rent_eur if max_rent_eur is not None else _env_int("SCRAPER_MAX_RENT", 2000)
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
        self._max_age_days = _env_int("SCRAPER_MAX_AGE_DAYS", 7)
        self._enrich_enabled = (
            enrich_enabled
            if enrich_enabled is not None
            else _env_bool("SCRAPER_ENRICH_ENABLED", False)
        )
        self._enrich_model = (
            enrich_model
            if enrich_model is not None
            else _env_str("SCRAPER_ENRICH_MODEL", brain.DEFAULT_MODEL)
        )
        self._enrich_min_desc_chars = (
            enrich_min_desc_chars
            if enrich_min_desc_chars is not None
            else _env_int("SCRAPER_ENRICH_MIN_DESC_CHARS", 200)
        )
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

    def _stale_cutoff(self) -> datetime:
        return datetime.utcnow() - timedelta(days=self._max_age_days)

    async def _first_stub_posted_at(
        self, source: Source, stub: Listing
    ) -> tuple[Optional[datetime], Optional[Listing]]:
        """Return `(posted_at, prefetched_listing)` for the page-leading stub.

        For sources whose stub already carries `posted_at` (wg-gesucht,
        tum-living) we never touch the network. For kleinanzeigen the
        date only appears on the detail page, so we run `scrape_detail`
        once and pass the enriched listing back to `_run_source` so the
        per-page scrape loop can skip refetching the same URL.

        Returns `(None, None)` on failure: the agent treats freshness
        unknown as "fresh enough" so a parser regression cannot silently
        halt the scraper.
        """
        if stub.posted_at is not None:
            return stub.posted_at, None
        try:
            enriched = await source.scrape_detail(stub)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] freshness probe failed for %s: %s",
                source.name,
                stub.id,
                exc,
            )
            return None, None
        return enriched.posted_at, enriched

    def _has_missing_enrichable_fields(self, listing: Listing) -> bool:
        return any(getattr(listing, f, None) is None for f in ENRICHABLE_FIELDS)

    def _apply_enrichment(self, listing: Listing, diff: EnrichmentDiff) -> list[str]:
        """Merge `diff` into `listing` for every in-scope, currently-null field.

        Refuses to overwrite non-null deterministic fields. Validates the
        merged result through `Listing.model_validate`; if validation
        fails, the entire diff is dropped and the listing is left
        untouched. Returns the list of fields actually written, for
        logging.
        """
        applied: list[str] = []
        candidate = listing.model_dump()
        for field in ENRICHABLE_FIELDS:
            new_value = getattr(diff, field, None)
            if new_value is None:
                continue
            if candidate.get(field) is not None:
                continue
            candidate[field] = new_value
            applied.append(field)
        if not applied:
            return []
        try:
            validated = Listing.model_validate(candidate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Enrichment diff failed validation, dropping: %s", exc)
            return []
        for field in applied:
            setattr(listing, field, getattr(validated, field))
        return applied

    async def _maybe_enrich(self, source: Source, listing: Listing) -> None:
        if not self._enrich_enabled:
            return
        if not self._has_missing_enrichable_fields(listing):
            return
        if len((listing.description or "")) < self._enrich_min_desc_chars:
            return
        try:
            diff = await asyncio.to_thread(
                enrich_listing, listing, model=self._enrich_model
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] enrichment failed for %s: %s",
                source.name,
                listing.id,
                exc,
            )
            return
        applied = self._apply_enrichment(listing, diff)
        if applied:
            logger.info(
                "[%s] enrichment filled %s on %s",
                source.name,
                applied,
                listing.id,
            )

    async def _scrape_and_save_via(
        self,
        source: Source,
        stub: Listing,
        *,
        prefetched: Optional[Listing] = None,
    ) -> None:
        if prefetched is not None:
            enriched = prefetched
        else:
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

        # Defensive backstop: the per-page first-stub freshness probe
        # already fires for stub-time-dated sources (wg-gesucht,
        # tum-living). For kleinanzeigen the same probe runs against
        # the page leader; this re-check catches non-leader stubs whose
        # detail page reveals an older posting date than the leader.
        if not self._is_fresh_enough(enriched):
            logger.info(
                "[%s] dropping stale listing %s (posted_at=%s, max_age_days=%d)",
                source.name,
                enriched.id,
                enriched.posted_at,
                self._max_age_days,
            )
            return

        await self._maybe_enrich(source, enriched)

        status = self._status_for(enriched)
        with Session(db_module.engine) as session:
            repo.upsert_global_listing(session, listing=enriched, status=status)
            repo.save_photos(
                session,
                listing_id=enriched.id,
                urls=list(enriched.photo_urls),
            )

    def _is_fresh_enough(self, listing: Listing) -> bool:
        """True iff `listing.posted_at` is within the freshness window.

        Listings without a `posted_at` are treated as fresh. The agent
        treats unknown freshness as "fresh enough" so a parser
        regression never silently halts scraping; the existing refresh
        / deletion sweep eventually cleans up genuinely stale rows.
        """
        posted_at = getattr(listing, "posted_at", None)
        if posted_at is None:
            return True
        return posted_at >= self._stale_cutoff()

    async def _run_source(self, source: Source) -> int:
        sp = self._search_profile()
        seen_for_source: set[str] = set()
        scraped = 0

        for kind in sorted(source.kind_supported):
            cutoff = self._stale_cutoff()
            try:
                page_iter = source.search_pages(kind=kind, profile=sp).__aiter__()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[%s] search_pages(kind=%s) init failed: %s",
                    source.name,
                    kind,
                    exc,
                )
                continue

            page_index = 0
            while True:
                try:
                    batch = await page_iter.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "[%s] search_pages(kind=%s) page=%d failed: %s",
                        source.name,
                        kind,
                        page_index,
                        exc,
                    )
                    break

                if not batch:
                    break

                first_posted, prefetched = await self._first_stub_posted_at(source, batch[0])
                if first_posted is not None and first_posted < cutoff:
                    logger.info(
                        "[%s] kind=%s page=%d: first listing posted_at=%s < cutoff %s, stopping",
                        source.name,
                        kind,
                        page_index,
                        first_posted,
                        cutoff,
                    )
                    break

                logger.info(
                    "[%s] kind=%s page=%d: %d stubs (city=%s)",
                    source.name,
                    kind,
                    page_index,
                    len(batch),
                    self._city,
                )

                for index, stub in enumerate(batch):
                    seen_for_source.add(stub.id)
                    with Session(db_module.engine) as session:
                        existing = session.get(ListingRow, stub.id)
                    if not self._needs_scrape(existing, source):
                        continue
                    use_prefetched = prefetched if index == 0 else None
                    await self._scrape_and_save_via(
                        source, stub, prefetched=use_prefetched
                    )
                    scraped += 1

                page_index += 1

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
