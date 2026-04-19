"""Background scraper agent: multi-source loop.

Drives a list of `Source` plugins (`backend/app/scraper/sources/*.py`)
sequentially per pass. For each `(source, kind)` it walks the source's
`search_pages` async iterator up to `SCRAPER_MAX_PAGES` pages
(default 6). Inside each page it processes stubs in order; if a stub's
`posted_at` is older than `SCRAPER_MAX_AGE_DAYS` the stub is dropped
without persisting and the loop continues with the next stub. Pagination
terminates only on the page cap, an empty page, an HTTP error, or a
block-like response — never on a single stale stub (see ADR-027).

For wg-gesucht and tum-living the stub already carries `posted_at`, so
the staleness check is free. For kleinanzeigen the date only appears on
the detail page, so a stale ad costs us one detail fetch but never a
write.

`SCRAPER_KIND` (env, one of `wg` | `flat` | `both`; default `both`)
restricts which verticals each source iterates. `SCRAPER_ENABLED_SOURCES`
(env, comma-separated; default `wg-gesucht`) selects which sources run.

If `SCRAPER_ENRICH_ENABLED` is set, the agent calls a narrow LLM
enrichment step between `scrape_detail` and `repo.upsert_global_listing`
to fill missing structured fields that the description states clearly
(see `enricher.py`). Coordinates remain on the deterministic geocoder
path; the LLM never produces lat/lng.

Never scores, never touches per-user matchers.
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


_VALID_KIND_FILTERS = ("wg", "flat", "both")


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


def _env_kind(name: str, default: str) -> str:
    raw = (os.environ.get(name) or default).strip().lower()
    if raw not in _VALID_KIND_FILTERS:
        logger.warning(
            "Invalid %s=%r (expected one of %s), falling back to %r",
            name, raw, _VALID_KIND_FILTERS, default,
        )
        return default
    return raw


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
        kind_filter: Optional[str] = None,
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
        self._max_age_days = _env_int("SCRAPER_MAX_AGE_DAYS", 4)
        self._max_pages = _env_int("SCRAPER_MAX_PAGES", 6)
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
        self._kind_filter = (
            kind_filter
            if kind_filter is not None
            else _env_kind("SCRAPER_KIND", "both")
        )
        self._sources: list[Source] = sources if sources is not None else build_sources()

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

    def _kinds_for(self, source: Source) -> list[str]:
        """Intersect the agent-wide kind filter with the source's supported kinds."""
        if self._kind_filter == "both":
            wanted = {"wg", "flat"}
        else:
            wanted = {self._kind_filter}
        return sorted(k for k in source.kind_supported if k in wanted)

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

    async def _scrape_and_persist(
        self,
        source: Source,
        stub: Listing,
    ) -> Optional[Listing]:
        """Deep-scrape one stub and persist the result. Returns the enriched
        listing on success, `None` on scrape failure (recorded as
        `scrape_status='failed'`) OR on detail-revealed staleness
        (sources like kleinanzeigen whose `posted_at` only appears on
        the detail page — the listing is dropped without persisting)."""
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
            return None

        # Detail-revealed staleness: sources whose stub lacks `posted_at`
        # (kleinanzeigen) only learn the date here. Drop without
        # persisting so the global pool stays free of stale ads.
        if self._is_stale(enriched.posted_at):
            logger.info(
                "[%s] dropping stale detail %s (posted_at=%s, max_age_days=%d)",
                source.name,
                enriched.id,
                enriched.posted_at,
                self._max_age_days,
            )
            return None

        await self._maybe_enrich(source, enriched)

        status = self._status_for(enriched)
        with Session(db_module.engine) as session:
            repo.upsert_global_listing(session, listing=enriched, status=status)
            repo.save_photos(
                session,
                listing_id=enriched.id,
                urls=list(enriched.photo_urls),
            )
        return enriched

    def _is_stale(self, posted_at: Optional[datetime]) -> bool:
        """True iff `posted_at` is set and older than the freshness cutoff.

        Unknown freshness (`None`) is treated as fresh — a parser
        regression must never silently drop every listing.
        """
        if posted_at is None:
            return False
        return posted_at < self._stale_cutoff()

    async def _run_source(self, source: Source) -> int:
        sp = self._search_profile()
        scraped = 0

        kinds = self._kinds_for(source)
        if not kinds:
            logger.info(
                "[%s] no kinds in scope (filter=%s, supported=%s); skipping",
                source.name,
                self._kind_filter,
                sorted(source.kind_supported),
            )
            return 0

        for kind in kinds:
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
            while page_index < self._max_pages:
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

                logger.info(
                    "[%s] kind=%s page=%d: %d stubs (city=%s)",
                    source.name,
                    kind,
                    page_index,
                    len(batch),
                    self._city,
                )

                for stub in batch:
                    # Stub-time freshness drop (free for sources whose
                    # search card already carries a date — wg-gesucht,
                    # tum-living). Stale stubs are dropped without a
                    # detail fetch and without persisting; the loop
                    # keeps walking the rest of the page + remaining
                    # pages up to `SCRAPER_MAX_PAGES` (ADR-027).
                    if self._is_stale(stub.posted_at):
                        logger.info(
                            "[%s] kind=%s skipping stale stub %s (posted_at=%s, max_age_days=%d)",
                            source.name,
                            kind,
                            stub.id,
                            stub.posted_at,
                            self._max_age_days,
                        )
                        continue

                    with Session(db_module.engine) as session:
                        existing = session.get(ListingRow, stub.id)
                    if not self._needs_scrape(existing, source):
                        continue

                    enriched = await self._scrape_and_persist(source, stub)
                    if enriched is None:
                        continue
                    scraped += 1

                page_index += 1

            if page_index >= self._max_pages:
                logger.info(
                    "[%s] kind=%s reached page cap (max_pages=%d); stopping",
                    source.name,
                    kind,
                    self._max_pages,
                )

        return scraped

    async def run_once(self) -> int:
        """One cross-source pass. Returns the number of listings scraped."""
        total = 0
        for source in self._sources:
            try:
                total += await self._run_source(source)
            except Exception:  # noqa: BLE001
                logger.exception("Scraper source %s pass failed", source.name)
        logger.info(
            "Scraper: scraped %d listings this pass across %d sources",
            total,
            len(self._sources),
        )
        return total

    async def run_forever(self) -> None:
        logger.info(
            "Starting scraper agent: interval=%ds, sources=%s, kind=%s, max_pages=%d, max_age_days=%d",
            self._interval,
            ",".join(s.name for s in self._sources),
            self._kind_filter,
            self._max_pages,
            self._max_age_days,
        )
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Scraper pass raised; sleeping before retry")
            await asyncio.sleep(self._interval)
