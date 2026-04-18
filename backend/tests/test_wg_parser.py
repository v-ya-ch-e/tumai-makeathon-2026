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


def test_parse_listing_structured_fields() -> None:
    """The listing fixture exposes every structured field we now read
    from section panels. Locking these down catches DOM regressions on
    the fields the LLM scorer relies on (address, commute, dates,
    languages, pets/smoking flags)."""
    stub = Listing(id="13115694", url=LISTING_URL, title="stub")
    html = _cached_fetch(LISTING_URL, "listing_13115694.html")
    enriched = parse_listing_page(html, stub)

    assert enriched.address == "Fritz-Erler-Straße 32"
    assert enriched.city == "München"
    assert enriched.district == "Ramersdorf-Perlach"

    assert enriched.available_from.isoformat() == "2026-05-01"
    assert enriched.available_to is not None
    assert enriched.available_to.isoformat() == "2026-10-31"

    assert enriched.languages == ["Deutsch", "Englisch"]
    assert enriched.pets_allowed is False
    assert enriched.smoking_ok is False

    # map_config.markers ships the landlord's own pin, ~48.097/11.646.
    assert enriched.lat is not None and enriched.lng is not None
    assert 48.0 < enriched.lat < 48.2
    assert 11.5 < enriched.lng < 11.8


def test_parse_listing_description_is_not_page_chrome() -> None:
    """The old fallback dumped `soup.get_text()[:4000]` which meant the
    registration/login modal text and cookie banner text ended up in the
    scoring prompt. We should only ever see the freitext container."""
    stub = Listing(id="13115694", url=LISTING_URL, title="stub")
    html = _cached_fetch(LISTING_URL, "listing_13115694.html")
    enriched = parse_listing_page(html, stub)

    assert enriched.description is not None
    # Login modal boilerplate from /mein-wg-gesucht login form.
    assert "Kostenfrei registrieren" not in enriched.description
    # Cookie consent sentinel from the CMP banner.
    assert "Alle akzeptieren" not in enriched.description


def test_parse_listing_photo_urls() -> None:
    html = """
    <html>
      <head>
        <meta property="og:image" content="https://img.wg-gesucht.de/cover.jpg" />
      </head>
      <body>
        <div class="gallery">
          <img src="/images/logo.svg" alt="logo" />
          <img data-src="https://img.wg-gesucht.de/room-1.jpg" alt="room" />
          <img src="https://img.wg-gesucht.de/room-2.jpg" alt="room 2" />
        </div>
      </body>
    </html>
    """
    enriched = parse_listing_page(
        html, Listing(id="photo-test", url="https://www.wg-gesucht.de/1.html", title="stub")
    )

    assert enriched.cover_photo_url == "https://img.wg-gesucht.de/cover.jpg"
    assert enriched.photo_urls == [
        "https://img.wg-gesucht.de/cover.jpg",
        "https://img.wg-gesucht.de/room-1.jpg",
        "https://img.wg-gesucht.de/room-2.jpg",
    ]


def test_parse_listing_page_ignores_captcha_interstitial() -> None:
    html = """
    <html>
      <body>
        <h1>Bitte bestätige, dass du ein Mensch bist</h1>
        <form action="/captcha">
          <div class="cf-turnstile" data-sitekey="demo"></div>
        </form>
      </body>
    </html>
    """

    stub = Listing(
        id="13115694",
        url=LISTING_URL,
        title="AVAILABLE ROOM in internationaler 8er WG",
    )
    enriched = parse_listing_page(html, stub)

    assert enriched.title == "AVAILABLE ROOM in internationaler 8er WG"
    assert enriched.description is None


if __name__ == "__main__":
    test_parse_search_page()
    test_parse_listing_page()
    test_parse_listing_structured_fields()
    test_parse_listing_description_is_not_page_chrome()
    test_parse_listing_photo_urls()
    test_parse_listing_page_ignores_captcha_interstitial()
    print("parser smoke tests passed")
