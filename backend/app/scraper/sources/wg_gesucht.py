"""wg-gesucht.de scraper plugin (Source protocol implementation).

Inlines the same anonymous httpx + bs4 loop that
`browser.anonymous_search` exposes, but as an async generator so the
agent can drive pagination + per-stub freshness drops
(`SCRAPER_MAX_AGE_DAYS`) + the page cap (`SCRAPER_MAX_PAGES`). Search
URLs are sorted newest-first (`sort_column=0&sort_order=0`) so stale
stubs cluster at the tail. The helper-style `browser.anonymous_search`
function is still kept (back-compat for non-scraper callers + a stable
patch point for the existing `test_scraper.py` / `test_periodic.py`
tests).

Both verticals (`wg`, `flat`) are supported. The numeric `category_id`
maps to a different URL slug per kind: WG rooms hit `/wg-zimmer-in-…`
(category `0`), whole flats hit `/wohnungen-in-…` (category `2`). The
listing-detail DOM is identical across verticals, so `scrape_detail`
doesn't dispatch on `kind`. See `_CATEGORY_SLUG` in `browser.py` for the
full id→slug table (verified by reading the wg-gesucht homepage's
type-selector `<select>` options on 2026-04-19).

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


# Per-kind wg-gesucht numeric category id (matches the homepage type
# selector — see `browser._CATEGORY_SLUG`).
_KIND_TO_CATEGORY_ID = {"wg": 0, "flat": 2}


class WgGesuchtSource:
    """Anonymous httpx + bs4 source. Iterates the WG and flat verticals."""

    name = "wg-gesucht"
    kind_supported = frozenset({"wg", "flat"})
    search_page_delay_seconds = 1.5
    detail_delay_seconds = 1.5
    refresh_hours = 24

    async def search_pages(
        self, *, kind: Kind, profile: SearchProfile
    ) -> AsyncIterator[list[Listing]]:
        if kind not in self.kind_supported:
            return
        category_id = _KIND_TO_CATEGORY_ID[kind]
        seen: set[str] = set()
        async with browser._anon_client() as client:
            page_index = 0
            while True:
                url = browser.build_search_url(
                    profile, page_index=page_index, category_id=category_id
                )
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    if page_index == 0:
                        raise
                    return
                batch = browser.parse_search_page(
                    response.text, seen_ids=seen, kind=kind
                )
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
