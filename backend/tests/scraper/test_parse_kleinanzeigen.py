"""Offline parser tests for kleinanzeigen.de (saved HTML fixtures)."""

from __future__ import annotations

import os
import pathlib
import re
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from app.scraper.sources.kleinanzeigen import (  # noqa: E402
    KleinanzeigenSource,
    parse_listing_page_ka,
    parse_search_page_ka,
)
HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "kleinanzeigen"
FIXTURES.mkdir(parents=True, exist_ok=True)

WG_SEARCH_URL = "https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/c199l6411"
FLAT_SEARCH_URL = "https://www.kleinanzeigen.de/s-mietwohnung/muenchen/c203l6411"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _live_fetch_allowed() -> bool:
    v = os.environ.get("SCRAPER_LIVE_FETCH", "").strip().lower()
    return v not in ("0", "false", "no")


def _cached_fetch(url: str, cache_name: str) -> str:
    path = FIXTURES / cache_name
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8")
    if not _live_fetch_allowed():
        pytest.skip("Missing fixture %s and SCRAPER_LIVE_FETCH disables fetch" % cache_name)
    try:
        with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError:
        pytest.skip("Live fetch failed for %s (network or block)" % cache_name)
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def _ensure_detail_fixture_from_wg_search(wg_html: str) -> pathlib.Path:
    existing = sorted(FIXTURES.glob("detail_*.html"))
    if existing:
        return existing[0]
    if not _live_fetch_allowed():
        pytest.skip("No detail_*.html fixture and SCRAPER_LIVE_FETCH disables fetch")
    m = re.search(r'data-adid="(\d+)"\s+data-href="([^"]+)"', wg_html)
    if not m:
        pytest.skip("Could not locate first listing URL in WG search HTML")
    adid, href = m.group(1), m.group(2)
    if not href.startswith("http"):
        href = "https://www.kleinanzeigen.de%s" % href
    try:
        with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
            resp = client.get(href)
            resp.raise_for_status()
    except httpx.HTTPError:
        pytest.skip("Live detail fetch failed (network or block)")
    out = FIXTURES / ("detail_%s.html" % adid)
    out.write_text(resp.text, encoding="utf-8")
    return out


def test_parse_search_page_wg() -> None:
    html = _cached_fetch(WG_SEARCH_URL, "search_wg_p1.html")
    listings = parse_search_page_ka(html, kind="wg", city="München")
    assert len(listings) >= 10
    priced = [l for l in listings if l.price_eur is not None]
    assert len(priced) >= 5
    for listing in listings:
        assert re.match(r"^kleinanzeigen:\d+$", listing.id), "bad id %r" % listing.id
        assert listing.kind == "wg"
        assert str(listing.url).startswith("https://www.kleinanzeigen.de/s-anzeige/")


def test_parse_search_page_flat() -> None:
    html = _cached_fetch(FLAT_SEARCH_URL, "search_flat_p1.html")
    listings = parse_search_page_ka(html, kind="flat", city="München")
    assert len(listings) >= 10
    priced = [l for l in listings if l.price_eur is not None]
    assert len(priced) >= 5
    for listing in listings:
        assert re.match(r"^kleinanzeigen:\d+$", listing.id), "bad id %r" % listing.id
        assert listing.kind == "flat"
        assert str(listing.url).startswith("https://www.kleinanzeigen.de/s-anzeige/")


def test_parse_listing_page_detail_sample() -> None:
    wg_html = _cached_fetch(WG_SEARCH_URL, "search_wg_p1.html")
    stubs = parse_search_page_ka(wg_html, kind="wg", city="München")
    detail_path = _ensure_detail_fixture_from_wg_search(wg_html)
    m = re.search(r"detail_(\d+)\.html", detail_path.name)
    assert m
    adid = m.group(1)
    stub = next(s for s in stubs if s.id == "kleinanzeigen:%s" % adid)
    work = stub.model_copy(deep=True)
    detail_html = detail_path.read_text(encoding="utf-8")
    parse_listing_page_ka(detail_html, work)

    assert work.description
    assert work.lat is not None and work.lng is not None
    assert 47.5 < work.lat < 48.5
    assert 11.0 < work.lng < 12.5
    assert work.cover_photo_url
    assert len(work.photo_urls) >= 1
    assert work.price_eur is not None


def test_looks_like_block_page() -> None:
    src = KleinanzeigenSource()
    tiny = "<html><body>Please verify you are human.</body></html>"
    assert src.looks_like_block_page(tiny, 200) is True
    assert src.looks_like_block_page("", 403) is True

    wg_html = _cached_fetch(WG_SEARCH_URL, "search_wg_p1.html")
    assert len(wg_html) >= 350_000
    assert src.looks_like_block_page(wg_html, 200) is False
