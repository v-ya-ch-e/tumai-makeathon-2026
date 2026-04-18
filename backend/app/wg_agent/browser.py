"""Playwright driver for wg-gesucht.de.

Defensive parsing: regex + BeautifulSoup over the HTML the browser renders, so
small DOM changes on the site don't kill the agent.

See docs/WG_GESUCHT.md for the recon notes that justify every selector here.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from . import geocoder
from .models import CITY_CATALOGUE, Listing, SearchProfile, WGCredentials

BASE_URL = "https://www.wg-gesucht.de"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Delay between anonymous listing fetches to stay well under abuse thresholds.
ANONYMOUS_PAGE_DELAY_SECONDS = 1.5


# --- Small helpers ------------------------------------------------------------

def _city_slug_and_id(city: str) -> tuple[int, str]:
    if city in CITY_CATALOGUE:
        return CITY_CATALOGUE[city]
    # Defensive: case-insensitive lookup.
    for key, value in CITY_CATALOGUE.items():
        if key.lower() == city.lower():
            return value
    # Fallback to Munich so a typo never crashes the agent.
    return CITY_CATALOGUE["Muenchen"]


def build_search_url(req: SearchProfile, page_index: int = 0) -> str:
    """Compose a wg-gesucht listing-search URL.

    IMPORTANT: the ``offer_filter=1`` and ``city_id`` query parameters trigger a
    malformed 301 redirect on the server side and must NOT be included. We pass
    only the numeric filters (``rMax``, ``rMin``, ``sMin``, ``sMax``, …).
    """
    city_id, slug = _city_slug_and_id(req.city)
    path = f"/wg-zimmer-in-{slug}.{city_id}.0.{int(req.rent_type)}.{page_index}.html"
    qs: dict[str, str] = {"rMax": str(req.max_rent_eur)}
    if req.min_rent_eur:
        qs["rMin"] = str(req.min_rent_eur)
    if req.min_size_m2:
        qs["sMin"] = str(req.min_size_m2)
    if req.max_size_m2 and req.max_size_m2 < 120:
        qs["sMax"] = str(req.max_size_m2)
    if req.furnished is True:
        qs["furnishedSea"] = "1"
    return f"{BASE_URL}{path}?{urlencode(qs)}"


_DATE_PATTERNS = [
    re.compile(r"(\d{2})\.(\d{2})\.(\d{4})"),
]


def _parse_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            day, month, year = (int(x) for x in match.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def _parse_int(text: str) -> Optional[int]:
    match = re.search(r"-?\d+", (text or "").replace(".", ""))
    return int(match.group(0)) if match else None


def _parse_float(text: str) -> Optional[float]:
    match = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_block_page(soup: BeautifulSoup, full_text: str) -> bool:
    """Detect captcha / anti-bot interstitials so we keep the stub listing data."""
    listing_markers = (
        soup.select_one("#ad_description_text") is not None
        or soup.find("h2", string=re.compile(r"Kosten|Verfügbarkeit|Adresse", re.I))
        is not None
        or _find_contact_url(str(soup)) is not None
    )
    if listing_markers:
        return False

    if soup.find(attrs={"data-sitekey": True}) is not None:
        return True
    if soup.find(["iframe", "script"], src=re.compile(r"turnstile|captcha", re.I)) is not None:
        return True

    return bool(
        re.search(
            r"captcha|turnstile|verify you are human|are you human|"
            r"sicherheits(über|ueber)pr(ü|ue)fung|bist du ein mensch|"
            r"ungew(ö|oe)hnlichen datenverkehr|automated requests|robot",
            full_text,
            re.I,
        )
    )


def _normalized_photo_url(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate or candidate.startswith("data:"):
        return None
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif candidate.startswith("/"):
        candidate = urljoin(BASE_URL, candidate)
    lowered = candidate.lower()
    if any(
        token in lowered
        for token in ("logo", "avatar", "icon", "flag", "tracking", "googleads")
    ):
        return None
    return candidate


def _parse_photo_urls(soup: BeautifulSoup) -> list[str]:
    """Return likely listing-gallery image URLs, deduplicated and ordered."""
    candidates: list[str] = []

    meta_image = soup.find("meta", attrs={"property": "og:image"})
    if meta_image is not None:
        normalized = _normalized_photo_url(meta_image.get("content"))
        if normalized:
            candidates.append(normalized)

    selectors = (
        '[data-full-image]',
        'img[data-src]',
        'img[data-lazy]',
        'img[src]',
        'source[srcset]',
    )
    for selector in selectors:
        for el in soup.select(selector):
            values: list[str] = []
            for attr in ("data-full-image", "data-src", "data-lazy", "src", "srcset"):
                raw = el.get(attr)
                if not raw:
                    continue
                if attr == "srcset":
                    values.extend(part.strip().split(" ")[0] for part in raw.split(","))
                else:
                    values.append(raw)
            for raw in values:
                normalized = _normalized_photo_url(raw)
                if normalized:
                    candidates.append(normalized)

    out: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out[:12]


# --- Parsers ------------------------------------------------------------------

_LISTING_ID_RE = re.compile(r"[./](\d{5,9})\.html")


def parse_search_page(html: str, seen_ids: set[str] | None = None) -> list[Listing]:
    """Parse a search-results page into `Listing` stubs.

    We only populate the fields visible on the card (id, url, title, price, size,
    wg_size, district, address, available_from, online_viewing). The full
    description is fetched later via `scrape_listing`.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen_ids = seen_ids if seen_ids is not None else set()
    out: list[Listing] = []

    # Primary selector: the React/server-rendered card.
    cards = soup.select("div.wgg_card.offer_list_item, article.offer_list_item")

    # Fallback: walk every canonical `/<id>.html` link if no cards were found.
    if not cards:
        anchors = soup.find_all("a", href=_LISTING_ID_RE)
        cards = []
        for a in anchors:
            parent = a.find_parent(["article", "div"], class_=True)
            if parent is not None and parent not in cards:
                cards.append(parent)

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        # Prefer the data-id attribute wg-gesucht sets on each card.
        listing_id = card.get("data-id") if hasattr(card, "get") else None
        href: Optional[str] = None
        for a in card.find_all("a", href=_LISTING_ID_RE):
            candidate = a.get("href", "")
            if candidate.startswith("/"):
                candidate = f"{BASE_URL}{candidate}"
            match = _LISTING_ID_RE.search(candidate)
            if not match:
                continue
            if listing_id is None:
                listing_id = match.group(1)
            href = candidate
            if re.match(rf"{re.escape(BASE_URL)}/\d+\.html$", candidate):
                # The short canonical form is most stable; prefer it.
                break
        if not listing_id:
            continue
        if href is None:
            href = f"{BASE_URL}/{listing_id}.html"
        url = href
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        # Title: first h3 anchor, else the first anchor's text.
        title_el = card.select_one("h3 a") or card.find("a", href=_LISTING_ID_RE)
        title = _clean(title_el.get_text() if title_el else "")

        # Price + size: look for "\d+ €" and "\d+ m²" in the card text.
        price_match = re.search(r"(\d+(?:\.\d+)?)\s*€", card_text)
        size_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", card_text)
        wg_match = re.search(r"(\d+)er WG", card_text)

        # Address line: look for "München ...": city + district + street.
        city = None
        district = None
        address = None
        address_line_match = re.search(
            r"(\d+er WG)\s*\|\s*([^|]+)\|\s*(.+?)(?:Verfügbar|$)", card_text
        )
        if address_line_match:
            locale = _clean(address_line_match.group(2))
            address = _clean(address_line_match.group(3))
            if " " in locale:
                city_part, _, district = locale.partition(" ")
                city = city_part.strip()
                district = district.strip() or None
            else:
                city = locale

        avail_from = None
        m = re.search(r"Verfügbar:\s*(\d{2}\.\d{2}\.\d{4})", card_text)
        if m:
            avail_from = _parse_date(m.group(1))

        online_viewing = "Online-Besichtigung" in card_text

        out.append(
            Listing(
                id=listing_id,
                url=url,
                title=title or f"Listing {listing_id}",
                city=city,
                district=district,
                address=address,
                price_eur=int(float(price_match.group(1))) if price_match else None,
                size_m2=float(size_match.group(1).replace(",", ".")) if size_match else None,
                wg_size=int(wg_match.group(1)) if wg_match else None,
                available_from=avail_from,
                online_viewing=online_viewing,
            )
        )
    return out


def _section_pairs(soup: BeautifulSoup, heading: str) -> dict[str, str]:
    """Return `{label: value}` pairs from the panel whose `<h2>` matches `heading`.

    wg-gesucht lays every "section_panel" out as `<h2>Kosten</h2>` followed by
    rows of `<span class="section_panel_detail">Miete:</span>` +
    `<span class="section_panel_value">995€</span>`. The `Adresse` / `Verfügbarkeit`
    pair share a single `div.panel` parent, so we can't scope by parent; instead
    we walk forward from the h2 until the next h2 appears.
    """
    h2 = soup.find(
        "h2", string=re.compile(rf"^\s*{re.escape(heading)}\s*$")
    )
    if h2 is None:
        return {}
    out: dict[str, str] = {}
    for node in h2.find_all_next():
        if node is h2:
            continue
        if getattr(node, "name", None) == "h2":
            break
        if not isinstance(node, Tag):
            continue
        if "section_panel_detail" not in (node.get("class") or []):
            continue
        row = node.find_parent("div", class_="row")
        if row is None:
            continue
        value = row.select_one(".section_panel_value")
        if value is None:
            continue
        key = _clean(node.get_text(" ")).rstrip(":").strip()
        out[key] = _clean(value.get_text(" "))
    return out


def _wg_details_lines(soup: BeautifulSoup) -> list[str]:
    """Return the WG-Details bullet lines (one per `<li>`), empty on no panel."""
    h2 = soup.find("h2", string=re.compile(r"^\s*WG-Details\s*$"))
    if h2 is None:
        return []
    panel = h2.find_parent("div", class_="panel")
    if panel is None:
        return []
    return [
        _clean(li.get_text(" "))
        for li in panel.select("li")
        if _clean(li.get_text(" "))
    ]


def _parse_address_panel(soup: BeautifulSoup) -> tuple[
    Optional[str], Optional[str], Optional[str], Optional[str]
]:
    """Return `(street, postal_code, city, district)` from the Adresse panel.

    The detail span renders two lines separated by `<br/>`:
      line 1 = street (e.g. "Fritz-Erler-Straße 32"),
      line 2 = "<PLZ> <City> <District>" (e.g. "81737 München Ramersdorf-Perlach").
    When the district is absent the second line is "<PLZ> <City>".
    """
    h2 = soup.find("h2", string=re.compile(r"^\s*Adresse\s*$"))
    if h2 is None:
        return None, None, None, None
    col = h2.find_parent("div", class_="col-sm-6") or h2.find_parent("div")
    if col is None:
        return None, None, None, None
    detail = col.select_one(".section_panel_detail")
    if detail is None:
        return None, None, None, None
    lines = [
        _clean(part)
        for part in detail.get_text("\n", strip=True).split("\n")
        if _clean(part)
    ]
    street = lines[0] if lines else None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    if len(lines) >= 2:
        m = re.match(r"(\d{5})\s+(\S+)(?:\s+(.+))?$", lines[1])
        if m:
            postal_code, city, district = m.group(1), m.group(2), m.group(3)
    return street, postal_code, city, district


_MAP_MARKERS_RE = re.compile(
    r"markers\s*:\s*\[\s*\{[^}]*?\"lat\"\s*:\s*(-?\d+(?:\.\d+)?)\s*,"
    r"\s*\"lng\"\s*:\s*(-?\d+(?:\.\d+)?)",
    re.DOTALL,
)


def _parse_map_lat_lng(html: str) -> Optional[tuple[float, float]]:
    """Extract the listing's `(lat, lng)` from the `map_config` script block.

    Every detail page that renders a Leaflet map ships a JS snippet of the
    form `var map_config = { ... markers: [{"lat":48.09, "lng":11.64, ...}] }`.
    Reading those coords directly is more precise than the Geocoding API (we
    get the landlord's own pin, not a best-guess geocode) and costs zero
    external calls. Returns `None` when the block is absent or malformed.
    """
    match = _MAP_MARKERS_RE.search(html)
    if not match:
        return None
    try:
        return float(match.group(1)), float(match.group(2))
    except ValueError:
        return None


def parse_listing_page(html: str, listing: Listing) -> Listing:
    """Fill in long-form fields by parsing the detail page HTML.

    Prefers scoped DOM selectors (section_panel rows, WG-Details `<li>`s,
    Adresse detail span, `ad_description_text` container) to avoid the
    false-positives you get when grepping the whole page text. Every DOM
    lookup falls back to the previous regex-on-full-text behavior so a
    future layout tweak degrades gracefully instead of returning None.
    """
    soup = BeautifulSoup(html, "html.parser")
    full_text = _clean(soup.get_text(" "))

    if _looks_like_block_page(soup, full_text):
        return listing

    h1 = soup.find("h1")
    if h1:
        listing.title = _clean(h1.get_text()) or listing.title

    # Description: prefer the structured container, strip embedded ad slots
    # and scripts so the scorer doesn't see googletag/iframe noise. Never
    # fall back to `full_text[:4000]` -- that poisons the LLM prompt with
    # cookie banners and the login modal markup.
    desc_parts: list[str] = []
    desc_root = soup.select_one("#ad_description_text")
    if desc_root is not None:
        scrub = BeautifulSoup(str(desc_root), "html.parser")
        for junk in scrub.select('script, iframe, [id^="div-gpt-ad-"]'):
            junk.decompose()
        text = scrub.get_text("\n", strip=True)
        if text:
            desc_parts.append(text)
    if not desc_parts:
        for el in soup.select("#freitext_description, [id^='freitext_']"):
            chunk = _clean(el.get_text(" "))
            if chunk:
                desc_parts.append(chunk)
    if desc_parts:
        listing.description = "\n\n".join(dict.fromkeys(desc_parts))

    kosten = _section_pairs(soup, "Kosten")
    verfuegbar = _section_pairs(soup, "Verfügbarkeit")

    miete_raw = kosten.get("Miete")
    if miete_raw and not listing.price_eur:
        price = _parse_int(miete_raw)
        if price:
            listing.price_eur = price
    if not listing.price_eur:
        miete_match = re.search(r"Miete[:\s]+(\d+(?:\.\d+)?)\s*€", full_text)
        if miete_match:
            listing.price_eur = int(float(miete_match.group(1)))

    for label, attr in (("frei ab", "available_from"), ("frei bis", "available_to")):
        raw = verfuegbar.get(label)
        if raw:
            parsed = _parse_date(raw)
            if parsed:
                setattr(listing, attr, parsed)

    if not listing.available_from:
        label_match = re.search(r"frei\s+ab\s*:", full_text, re.I)
        if label_match:
            tail = full_text[label_match.end() : label_match.end() + 200]
            parsed = _parse_date(tail)
            if parsed:
                listing.available_from = parsed
    if not listing.available_to:
        label_match = re.search(r"frei\s+bis\s*:", full_text, re.I)
        if label_match:
            tail = full_text[label_match.end() : label_match.end() + 200]
            parsed = _parse_date(tail)
            if parsed:
                listing.available_to = parsed

    street, _postal, addr_city, addr_district = _parse_address_panel(soup)
    if street:
        listing.address = street
    if addr_city and not listing.city:
        listing.city = addr_city
    if addr_district and not listing.district:
        listing.district = addr_district
    if not listing.address:
        address_heading = soup.find(string=re.compile(r"Adresse", re.I))
        if address_heading:
            parent = address_heading.find_parent()
            if parent:
                listing.address = _clean(
                    parent.get_text(" ").replace("Adresse", "")
                )

    size_match = re.search(r"Zimmergröße\s*[:\s]+(\d+(?:[.,]\d+)?)", full_text)
    if size_match and not listing.size_m2:
        listing.size_m2 = float(size_match.group(1).replace(",", "."))

    details = _wg_details_lines(soup)

    languages: list[str] = []
    for line in details:
        lang_match = re.match(r"Sprache/?n?\s*:\s*(.+)$", line)
        if lang_match:
            languages = [
                _clean(part)
                for part in re.split(r"[,/]", lang_match.group(1))
                if _clean(part)
            ]
            break
    if languages:
        listing.languages = languages
    elif not listing.languages:
        lang_match = re.search(
            r"Sprache/?n?\s*[:\s]+([A-Za-zäöüÄÖÜß,\s]+?)(?:Haustiere|Bewohner|$)",
            full_text,
        )
        if lang_match:
            listing.languages = [
                _clean(x)
                for x in re.split(r"[,/]", lang_match.group(1))
                if _clean(x)
            ]

    for line in details:
        pets = re.match(r"Haustiere\s+vorhanden:\s*(Ja|Nein)\s*$", line, re.I)
        if pets:
            listing.pets_allowed = pets.group(1).lower() == "ja"
            break
        smoking_no = re.match(r"Rauchen\s+nicht\s+erw(ü|ue)nscht\s*$", line, re.I)
        smoking_yes = re.match(r"Rauchen\s+erw(ü|ue)nscht\s*$", line, re.I)
        if smoking_no:
            listing.smoking_ok = False
        elif smoking_yes:
            listing.smoking_ok = True

    if listing.pets_allowed is None:
        if re.search(r"Haustiere[^:]*:\s*Ja", full_text, re.I):
            listing.pets_allowed = True
        elif re.search(r"Haustiere[^:]*:\s*Nein", full_text, re.I):
            listing.pets_allowed = False
    if listing.smoking_ok is None:
        if re.search(r"Rauchen\s+nicht\s+erwünscht", full_text, re.I):
            listing.smoking_ok = False
        elif re.search(r"Rauchen\s+erwünscht", full_text, re.I):
            listing.smoking_ok = True

    # Furnished: only trust explicit structured signals. The old
    # `re.search("möbliert", full_text)` triggered True on "nicht möbliert"
    # because the negation lives 40+ chars before the keyword.
    #
    # The reliable sources are (a) a "möbliert" line in WG-Details and
    # (b) a tile in the `div.utility_icons` quick-facts strip at the top
    # of the listing. Both are short labels, so any negation adverb
    # (`nicht`, `un-`, `teilweise`) appears on the same line.
    if listing.furnished is None:
        utility_tiles = [
            _clean(tile.get_text(" "))
            for tile in soup.select("div.utility_icons > div.text-center")
        ]
        for line in (*details, *utility_tiles):
            if re.search(r"m(ö|oe)bliert", line, re.I) and not re.search(
                r"(nicht|un|teilweise)", line, re.I
            ):
                listing.furnished = True
                break

    if not listing.wg_size:
        wg_match = re.search(r"(\d+)er WG", full_text)
        if wg_match:
            listing.wg_size = int(wg_match.group(1))

    coords = _parse_map_lat_lng(html)
    if coords is not None:
        listing.lat, listing.lng = coords

    photo_urls = _parse_photo_urls(soup)
    if photo_urls:
        listing.photo_urls = photo_urls
        listing.cover_photo_url = photo_urls[0]

    return listing


def _find_contact_url(html: str) -> Optional[str]:
    """The listing page contains a link to '/nachricht-senden/<id>,<x>,<y>.html'."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/nachricht-senden/" in href:
            return href if href.startswith("http") else f"{BASE_URL}{href}"
    return None


# --- Driver -------------------------------------------------------------------

@dataclass
class WGBrowser:
    """High-level wrapper around a logged-in Playwright context."""

    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    creds: WGCredentials
    storage_state_path: Optional[Path]
    logged_in: bool = False

    async def close(self) -> None:
        try:
            await self.context.close()
        finally:
            await self.browser.close()
            await self.playwright.stop()

    # -- navigation -----------------------------------------------------------

    async def _goto(self, url: str, *, wait: str = "domcontentloaded") -> None:
        await self.page.goto(url, wait_until=wait, timeout=45_000)
        # Dismiss the cookie/consent dialog if it appears. We click any button
        # whose text matches an "accept" or "agree" pattern.
        try:
            await self.page.wait_for_timeout(400)
            for pattern in (
                "text=/Alle akzeptieren/i",
                "text=/Accept all/i",
                "text=/Zustimmen/i",
                "text=/Agree/i",
            ):
                locator = self.page.locator(pattern)
                if await locator.count():
                    await locator.first.click(timeout=2_000)
                    break
        except Exception:
            pass
        await asyncio.sleep(random.uniform(1.2, 2.4))

    # -- login ----------------------------------------------------------------

    async def ensure_logged_in(self) -> bool:
        """Log in with cookies first, then username/password. Returns True on success."""
        await self._goto(f"{BASE_URL}/mein-wg-gesucht.html")
        html = await self.page.content()
        if 'id="logout-button"' in html or 'href="/logout.html"' in html:
            self.logged_in = True
            return True

        # Fallback: submit the login form on /login.html.
        await self._goto(f"{BASE_URL}/login.html")
        try:
            await self.page.wait_for_selector(
                'input[name="login_email_username"]', timeout=8_000
            )
        except PlaywrightTimeoutError:
            # Some locale variants use a different login route.
            await self._goto(f"{BASE_URL}/mein-wg-gesucht.html?mode=login")
            await self.page.wait_for_selector(
                'input[name="login_email_username"]', timeout=8_000
            )

        await self.page.fill('input[name="login_email_username"]', self.creds.username)
        await self.page.fill('input[name="login_password"]', self.creds.password)
        await self.page.click('button[name="login_submit"]')

        # Wait for either the dashboard or an error.
        try:
            await self.page.wait_for_url(re.compile(r"mein-wg-gesucht"), timeout=15_000)
        except PlaywrightTimeoutError:
            pass
        html = await self.page.content()
        self.logged_in = (
            'id="logout-button"' in html or 'href="/logout.html"' in html
        )
        if self.logged_in and self.storage_state_path:
            state = await self.context.storage_state()
            self.storage_state_path.write_text(json.dumps(state))
        return self.logged_in

    # -- search ---------------------------------------------------------------

    async def search(self, req: SearchProfile, *, max_pages: int = 2) -> list[Listing]:
        """Return a deduplicated list of listing stubs for the given requirements."""
        seen: set[str] = set()
        out: list[Listing] = []
        for page_index in range(max_pages):
            url = build_search_url(req, page_index=page_index)
            await self._goto(url)
            html = await self.page.content()
            batch = parse_search_page(html, seen_ids=seen)
            if not batch:
                break
            out.extend(batch)
            if len(out) >= req.max_listings_to_consider:
                break
            await asyncio.sleep(random.uniform(1.5, 3.0))
        return out[: req.max_listings_to_consider]

    async def scrape_listing(self, listing: Listing) -> Listing:
        """Visit a listing and fill in long-form fields."""
        await self._goto(str(listing.url))
        html = await self.page.content()
        return parse_listing_page(html, listing)

    # -- messaging ------------------------------------------------------------

    async def send_message(self, listing: Listing, text: str) -> tuple[bool, str]:
        """Send a message to the landlord of `listing`. Returns (ok, detail).

        Caller is responsible for respecting dry-run mode / rate limits.
        """
        await self._goto(str(listing.url))
        html = await self.page.content()
        contact_url = _find_contact_url(html)
        if not contact_url:
            return False, "No contact URL found on listing page."

        await self._goto(contact_url)
        try:
            await self.page.wait_for_selector('textarea[name="message"]', timeout=10_000)
        except PlaywrightTimeoutError:
            return False, "Message form did not load (maybe logged out or blocked)."

        await self.page.fill('textarea[name="message"]', text)
        await asyncio.sleep(random.uniform(0.6, 1.4))
        try:
            await self.page.click('input[type="submit"][name="send_message_offer"], button[type="submit"]')
        except PlaywrightTimeoutError:
            return False, "Submit button not found."

        # Wait for response.
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except PlaywrightTimeoutError:
            pass
        html_after = await self.page.content()
        if "alert-danger" in html_after or "zu viele" in html_after.lower():
            return False, "wg-gesucht rejected the message (rate limit or validation)."
        if "erfolgreich" in html_after.lower() or "successfully" in html_after.lower():
            return True, "Message sent."
        return True, "Message submitted (no explicit confirmation; assume OK)."

    async def fetch_inbox(self) -> str:
        """Return the raw HTML of the inbox page (for parsing by the caller)."""
        await self._goto(f"{BASE_URL}/nachrichten-lesen.html")
        return await self.page.content()


# --- Factory ------------------------------------------------------------------

async def launch_browser(
    creds: WGCredentials,
    *,
    headless: bool = False,
    storage_state_env: str = "WG_STATE_FILE",
) -> WGBrowser:
    """Launch a Playwright browser with (optional) saved session cookies."""
    playwright = await async_playwright().start()
    chromium = playwright.chromium
    browser = await chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])

    storage_path: Optional[Path] = None
    candidate = creds.storage_state_path or os.getenv(storage_state_env)
    if candidate:
        p = Path(candidate).expanduser()
        if p.exists() and p.stat().st_size > 0:
            storage_path = p

    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        storage_state=str(storage_path) if storage_path else None,
    )
    page = await context.new_page()

    save_path: Optional[Path] = None
    if candidate:
        save_path = Path(candidate).expanduser()
        save_path.parent.mkdir(parents=True, exist_ok=True)

    return WGBrowser(
        playwright=playwright,
        browser=browser,
        context=context,
        page=page,
        creds=creds,
        storage_state_path=save_path,
    )


# --- Anonymous (no-login) httpx path -----------------------------------------
# Used when the user has not connected a wg-gesucht account. Listing pages are
# publicly readable, so we can search + deep-scrape without Playwright at all.


def _anon_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"},
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=10.0),
    )


async def anonymous_search(
    req: SearchProfile, *, max_pages: int = 2
) -> list[Listing]:
    """Return a deduplicated list of listing stubs without logging in."""
    seen: set[str] = set()
    out: list[Listing] = []
    async with _anon_client() as client:
        for page_index in range(max_pages):
            url = build_search_url(req, page_index=page_index)
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                if page_index == 0:
                    raise
                break
            batch = parse_search_page(response.text, seen_ids=seen)
            if page_index == 0 and not batch:
                raise RuntimeError(
                    "Search page returned no parsable listings on the first page."
                )
            if not batch:
                break
            out.extend(batch)
            if page_index + 1 < max_pages:
                await asyncio.sleep(ANONYMOUS_PAGE_DELAY_SECONDS)
    return out


async def anonymous_scrape_listing(
    listing: Listing, *, req_city: Optional[str] = None
) -> Listing:
    """Deep-scrape a listing's public detail page using httpx + parse_listing_page.

    `parse_listing_page` already reads the landlord's own map pin from the
    page's `map_config.markers` block when available. We only hit the
    Google Geocoding API as a fallback for listings without a map pin,
    which keeps geocoder spend close to zero on typical hunts.
    """
    async with _anon_client() as client:
        response = await client.get(str(listing.url))
        response.raise_for_status()
    parse_listing_page(response.text, listing)

    if listing.lat is not None and listing.lng is not None:
        return listing

    query: Optional[str] = None
    if listing.address:
        query = listing.address
    elif listing.district:
        city = listing.city or req_city
        query = f"{listing.district}, {city}" if city else listing.district
    if query:
        coords = await geocoder.geocode(query)
        if coords is not None:
            listing.lat, listing.lng = coords
    return listing
