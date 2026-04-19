"""Source plugin protocol for the multi-source scraper loop.

Each per-site module implements one `Source`. `ScraperAgent` registers a
list of `Source` instances and iterates them sequentially per pass.

Pagination is now driven by `ScraperAgent` via `search_pages`, an async
iterator that yields one batch of stubs per source page. The agent stops
paginating when the first stub on a page is older than the freshness
cutoff (`SCRAPER_MAX_AGE_DAYS`); per-source `max_pages` ceilings have
been removed.

Identity / kind invariants:

- Stubs returned by `search_pages` carry the namespaced `id`
  (`f"{source}:{external_id}"`) and the final `kind`
  (one of `'wg'` | `'flat'`).
- `scrape_detail` MUST NOT re-key the listing — `id` and `kind` are
  immutable from the moment the stub is built.
"""

from __future__ import annotations

from typing import AsyncIterator, Literal, Protocol

from ...wg_agent.models import Listing, SearchProfile

Kind = Literal["wg", "flat"]


class Source(Protocol):
    """One scraping source (e.g. wg-gesucht, tum-living, kleinanzeigen)."""

    name: str
    kind_supported: frozenset[Kind]
    search_page_delay_seconds: float
    detail_delay_seconds: float
    refresh_hours: int

    def search_pages(
        self, *, kind: Kind, profile: SearchProfile
    ) -> AsyncIterator[list[Listing]]:
        """Yield one batch of stubs per source page, in source-defined order.

        Pagination terminates only on (a) empty page, (b) block-like
        response, or (c) HTTP error after the first page. The agent decides
        when to stop based on per-page freshness.
        """
        ...

    async def scrape_detail(self, stub: Listing) -> Listing:
        """Enrich `stub` with description / coords / photos. Never re-keys id/kind."""
        ...

    def looks_like_block_page(self, text: str, status: int) -> bool:
        """True when the response looks like an anti-bot interstitial."""
        ...
