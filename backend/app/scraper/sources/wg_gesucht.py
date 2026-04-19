"""wg-gesucht.de scraper plugin (Source protocol implementation).

Inlines the same anonymous httpx + bs4 loop that
`browser.anonymous_search` exposes, but as an async generator so the
agent can drive pagination via the per-stub freshness stop
(`SCRAPER_MAX_AGE_DAYS`). Search URLs are sorted newest-first
(`sort_column=0&sort_order=0`) so the first stale stub means everything
after it is also stale. The helper-style `browser.anonymous_search`
function is still kept (back-compat for non-scraper callers + a stable
patch point for the existing `test_scraper.py` / `test_periodic.py`
tests).

Recipe + DOM selectors: `../SOURCE_WG_GESUCHT.md`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from ...wg_agent import browser
from ...wg_agent.models import Listing, SearchProfile
from .base import Kind

logger = logging.getLogger(__name__)


class WgGesuchtSource:
    """Anonymous httpx + bs4 source. Iterates the WG vertical only.

    Flat-vertical category id is unverified (see `SOURCE_WG_GESUCHT.md`
    TODO #3); `kind_supported` is therefore `{'wg'}` until live recon
    pins down the right slug. Kleinanzeigen and TUM Living already
    cover the flat vertical so users with `mode='flat'` still get
    results.
    """

    name = "wg-gesucht"
    kind_supported = frozenset({"wg"})
    search_page_delay_seconds = 1.5
    detail_delay_seconds = 1.5
    refresh_hours = 24

    async def search_pages(
        self, *, kind: Kind, profile: SearchProfile
    ) -> AsyncIterator[list[Listing]]:
        if kind not in self.kind_supported:
            return
        seen: set[str] = set()
        async with browser._anon_client() as client:
            page_index = 0
            while True:
                url = browser.build_search_url(profile, page_index=page_index)
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    if page_index == 0:
                        raise
                    return
                batch = browser.parse_search_page(response.text, seen_ids=seen)
                if page_index == 0 and not batch:
                    raise RuntimeError(
                        "Search page returned no parsable listings on the first page."
                    )
                if not batch:
                    return
                yield batch
                await asyncio.sleep(browser.ANONYMOUS_PAGE_DELAY_SECONDS)
                page_index += 1

    async def scrape_detail(self, stub: Listing) -> Listing:
        return await browser.anonymous_scrape_listing(stub, req_city=stub.city)

    def looks_like_block_page(self, text: str, status: int) -> bool:
        if status >= 500:
            return True
        soup = BeautifulSoup(text, "html.parser")
        return browser._looks_like_block_page(soup, text)
