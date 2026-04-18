"""Source plugin protocol for the multi-source scraper loop.

Each per-site module implements one `Source`. `ScraperAgent` registers a
list of `Source` instances and iterates them sequentially per pass.

Identity / kind invariants:

- Stubs returned by `search` carry the namespaced `id` (`f"{source}:{external_id}"`)
  and the final `kind` (one of `'wg'` | `'flat'`).
- `scrape_detail` MUST NOT re-key the listing — `id` and `kind` are
  immutable from the moment the stub is built.
"""

from __future__ import annotations

from typing import Literal, Protocol

from ...wg_agent.models import Listing, SearchProfile

Kind = Literal["wg", "flat"]


class Source(Protocol):
    """One scraping source (e.g. wg-gesucht, tum-living, kleinanzeigen)."""

    name: str
    kind_supported: frozenset[Kind]
    search_page_delay_seconds: float
    detail_delay_seconds: float
    max_pages: int
    refresh_hours: int

    async def search(self, *, kind: Kind, profile: SearchProfile) -> list[Listing]:
        """Return one pass of stubs for `kind`. Stubs carry namespaced id + kind."""
        ...

    async def scrape_detail(self, stub: Listing) -> Listing:
        """Enrich `stub` with description / coords / photos. Never re-keys id/kind."""
        ...

    def looks_like_block_page(self, text: str, status: int) -> bool:
        """True when the response looks like an anti-bot interstitial."""
        ...
