"""wg-gesucht.de scraper plugin (Source protocol implementation).

Thin shim over the verified `browser.anonymous_search` /
`browser.anonymous_scrape_listing` / `browser._looks_like_block_page`
helpers in `wg_agent/browser.py`. The plugin adds nothing functional;
its job is to expose the existing wg-gesucht code path through the
`Source` protocol so `ScraperAgent` can dispatch to it the same way
as `tum-living` and `kleinanzeigen`.

Recipe + DOM selectors: `../SOURCE_WG_GESUCHT.md`.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from ...wg_agent import browser
from ...wg_agent.models import Listing, SearchProfile
from .base import Kind


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
    max_pages = 2
    refresh_hours = 24

    async def search(self, *, kind: Kind, profile: SearchProfile) -> list[Listing]:
        if kind not in self.kind_supported:
            return []
        return await browser.anonymous_search(profile, max_pages=self.max_pages)

    async def scrape_detail(self, stub: Listing) -> Listing:
        return await browser.anonymous_scrape_listing(stub, req_city=stub.city)

    def looks_like_block_page(self, text: str, status: int) -> bool:
        if status >= 500:
            return True
        soup = BeautifulSoup(text, "html.parser")
        return browser._looks_like_block_page(soup, text)
