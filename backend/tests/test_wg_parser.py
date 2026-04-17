"""Offline smoke tests for the WG-Gesucht parser.

We hit the live site once to fetch sample HTML (with a polite User-Agent), cache
it under ``tests/fixtures/``, and then assert that our parser extracts the
expected structured data. This catches regressions when wg-gesucht ships a DOM
change without requiring a real Playwright run.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx

from app.wg_agent.browser import parse_listing_page, parse_search_page
from app.wg_agent.models import Listing

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
FIXTURES.mkdir(exist_ok=True)

SEARCH_URL = (
    "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.90.0.1.0.html"
    "?rMax=800&sMin=12"
)
LISTING_URL = "https://www.wg-gesucht.de/13115694.html"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _cached_fetch(url: str, cache_name: str) -> str:
    path = FIXTURES / cache_name
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8")
    with httpx.Client(headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def test_parse_search_page() -> None:
    html = _cached_fetch(SEARCH_URL, "search_muenchen.html")
    listings = parse_search_page(html)
    assert listings, "Expected at least one listing in search results"
    # Sanity on basic invariants.
    for listing in listings:
        assert listing.id.isdigit(), f"bad id {listing.id}"
        assert str(listing.url).endswith(f"{listing.id}.html") or "/wg-zimmer-in-" in str(listing.url)
        if listing.price_eur is not None:
            assert 100 <= listing.price_eur <= 3000
        if listing.size_m2 is not None:
            assert 5 <= listing.size_m2 <= 200
    assert any(l.price_eur for l in listings), "At least one listing should have a price"
    print(f"parsed {len(listings)} listings")


def test_parse_listing_page() -> None:
    stub = Listing(id="13115694", url=LISTING_URL, title="stub")
    html = _cached_fetch(LISTING_URL, "listing_13115694.html")
    enriched = parse_listing_page(html, stub)
    assert enriched.title and enriched.title != "stub"
    assert enriched.description and len(enriched.description) > 200
    # This specific listing is 995€ / 14m² / 8er WG / available 2026-05-01
    assert enriched.price_eur == 995
    assert enriched.size_m2 == 14.0
    assert enriched.wg_size == 8
    assert enriched.available_from is not None
    assert enriched.furnished is True


if __name__ == "__main__":
    test_parse_search_page()
    test_parse_listing_page()
    print("parser smoke tests passed")
