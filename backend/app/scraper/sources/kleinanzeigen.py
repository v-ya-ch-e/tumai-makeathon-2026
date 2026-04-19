"""kleinanzeigen.de scraper plugin (Source protocol implementation).

Recipe + DOM selectors: `../SOURCE_KLEINANZEIGEN.md`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ...wg_agent.models import Listing, SearchProfile
from .base import Kind

logger = logging.getLogger(__name__)

name = "kleinanzeigen"
kind_supported = frozenset({"wg", "flat"})
search_page_delay_seconds = 2.5
detail_delay_seconds = 3.5
max_pages = 5
refresh_hours = 24

KA_BASE_URL = "https://www.kleinanzeigen.de"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
KA_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_BAD_CHARREF = re.compile(r"&#(\d+)(?![\d;])")
_DETAIL_URL_RE = re.compile(r"/s-anzeige/[^/]+/(\d+)-\d+-\d+")
KA_LOCALITY_BY_CITY = {"München": 6411, "Muenchen": 6411}

_DE_MONTHS = {
    "januar": 1,
    "jänner": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

_BLOCK_HINT_RE = re.compile(
    r"datadome|please verify you are human|sicherheits(über|ueber)pr(ü|ue)fung|"
    r"ungew(ö|oe)hnlichen datenverkehr|automated requests",
    re.I,
)


def _ka_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(_BAD_CHARREF.sub(r"&#\1;", html), "html.parser")


def _parse_price_eur_from_text(price_text: str) -> Optional[int]:
    if not price_text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*€", price_text)
    if not m:
        return None
    raw = m.group(1).replace(".", "")
    try:
        return int(float(raw))
    except ValueError:
        return None


def _parse_posting_date_de(span_text: str) -> Optional[datetime]:
    """Parse the value of `#viewad-extra-info > div:first-child > span`.

    Verified format on the recon ads is `dd.mm.yyyy` (`07.04.2026`,
    `17.04.2026`, `14.04.2026`). `Heute` / `Gestern` are also accepted as
    a defensive fallback in case Kleinanzeigen ever swaps in relative
    labels for very fresh ads (the recon never observed them, but the
    cost of supporting them is one extra branch).
    """
    if not span_text:
        return None
    raw = span_text.strip()
    low = raw.lower()
    if low == "heute":
        today = date.today()
        return datetime(today.year, today.month, today.day)
    if low == "gestern":
        y = date.today() - timedelta(days=1)
        return datetime(y.year, y.month, y.day)
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    return None


def _parse_month_year_de(val: str) -> Optional[date]:
    v = val.strip()
    if not v:
        return None
    low = v.lower()
    if low == "sofort":
        return date.today()
    parts = v.split()
    if len(parts) < 2:
        return None
    month_s = parts[0].lower().strip()
    year_s = parts[-1].strip()
    month = _DE_MONTHS.get(month_s)
    if month is None:
        return None
    try:
        year = int(year_s)
    except ValueError:
        return None
    return date(year, month, 1)


def _parse_size_m2_from_attr(val: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", val)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _attrs_map(soup: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for li in soup.select("li.addetailslist--detail"):
        val_el = li.select_one(".addetailslist--detail--value")
        if not val_el:
            continue
        full = li.get_text(" ", strip=True)
        val = val_el.get_text(" ", strip=True)
        if full.endswith(val):
            label = full[: -len(val)].strip()
        else:
            label = full.split(val, 1)[0].strip() if val in full else full
        out[label] = val
    return out


def _walk_ld_json_images(node: object, photo_urls: list[str], seen: set[str]) -> None:
    if isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else ([t] if t is not None else [])
        if "ImageObject" in types:
            url = node.get("contentUrl")
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                photo_urls.append(url)
        for v in node.values():
            _walk_ld_json_images(v, photo_urls, seen)
    elif isinstance(node, list):
        for item in node:
            _walk_ld_json_images(item, photo_urls, seen)


def parse_search_page_ka(html: str, *, kind: Kind, city: str = "München") -> list[Listing]:
    soup = _ka_soup(html)
    out: list[Listing] = []
    for art in soup.select("article.aditem[data-adid]"):
        adid = art.get("data-adid") or ""
        if not adid:
            continue
        href = (art.get("data-href") or "").strip()
        if not href:
            for a in art.select("a[href]"):
                h = a.get("href") or ""
                if _DETAIL_URL_RE.search(h):
                    href = h.strip()
                    break
        if href and not href.startswith("http"):
            href = f"{KA_BASE_URL}{href}"
        if not href:
            continue

        title_el = art.select_one("h2.text-module-begin a.ellipsis")
        price_el = art.select_one("p.aditem-main--middle--price-shipping--price")
        loc_el = art.select_one(".aditem-main--top--left")
        tags_el = art.select_one("p.aditem-main--middle--tags")

        tags_text = " ".join(tags_el.get_text(" ", strip=True).split()) if tags_el else ""
        size_m = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", tags_text)

        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        price_eur = _parse_price_eur_from_text(price_text)

        loc_text = loc_el.get_text(" ", strip=True).replace("\u200b", "") if loc_el else ""
        plz_m = re.match(r"(\d{5})\s+(.+)$", loc_text)

        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            title = "Listing %s" % adid

        out.append(
            Listing(
                id="kleinanzeigen:%s" % adid,
                url=href,
                title=title,
                kind=kind,
                city=city,
                district=(plz_m.group(2).strip() if plz_m else None),
                price_eur=price_eur,
                size_m2=(float(size_m.group(1).replace(",", ".")) if size_m else None),
                online_viewing=("Online-Besichtigung" in tags_text),
            )
        )
    return out


def parse_listing_page_ka(html: str, listing: Listing) -> Listing:
    soup = _ka_soup(html)

    title_el = soup.select_one("h1#viewad-title")
    if title_el:
        t = title_el.get_text(" ", strip=True)
        if t:
            listing.title = t

    price_el = soup.select_one("h2#viewad-price")
    if price_el:
        price_text = price_el.get_text(" ", strip=True)
        low = price_text.lower()
        if "auf anfrage" in low:
            listing.price_eur = None
        else:
            parsed = _parse_price_eur_from_text(price_text)
            if parsed is not None:
                listing.price_eur = parsed
            elif re.search(r"\bvb\b", low) and not re.search(r"(\d+(?:\.\d+)?)\s*€", price_text):
                listing.price_eur = None

    loc_el = soup.select_one("#viewad-locality")
    if loc_el:
        loc_text = loc_el.get_text(" ", strip=True).replace("\u200b", "")
        m = re.match(r"(\d{5})\s+([^-]+?)\s*-\s*(.+)$", loc_text)
        if m:
            pcity = m.group(2).strip()
            pdist = m.group(3).strip()
            if pcity:
                listing.city = pcity
            if pdist:
                listing.district = pdist

    desc_el = soup.select_one("#viewad-description-text")
    if desc_el:
        for bad in desc_el.find_all(["script", "iframe"]):
            bad.decompose()
        d = desc_el.get_text("\n", strip=True)
        listing.description = d if d else None

    lat_meta = soup.find("meta", attrs={"property": "og:latitude"})
    lng_meta = soup.find("meta", attrs={"property": "og:longitude"})
    if lat_meta and lat_meta.get("content"):
        try:
            listing.lat = float(lat_meta["content"])
        except (TypeError, ValueError):
            pass
    if lng_meta and lng_meta.get("content"):
        try:
            listing.lng = float(lng_meta["content"])
        except (TypeError, ValueError):
            pass

    attrs = _attrs_map(soup)

    vab = attrs.get("Verfügbar ab")
    if vab is not None:
        listing.available_from = _parse_month_year_de(vab)

    vbis = attrs.get("Verfügbar bis")
    if vbis is not None:
        listing.available_to = _parse_month_year_de(vbis)

    wf = attrs.get("Wohnfläche")
    if wf:
        sm = _parse_size_m2_from_attr(wf)
        if sm is not None:
            listing.size_m2 = sm

    if listing.kind == "wg":
        mb = attrs.get("Anzahl Mitbewohner")
        if mb:
            dm = re.search(r"(\d+)", mb)
            if dm:
                try:
                    listing.wg_size = int(dm.group(1)) + 1
                except ValueError:
                    pass

    listing.furnished = None
    for li in soup.select("li.checktag"):
        tag = li.get_text(" ", strip=True)
        if re.match(r"^Möbliert(/Teilmöbliert)?$", tag):
            listing.furnished = True
            break

    listing.pets_allowed = None
    for li in soup.select("li.checktag"):
        if li.get_text(" ", strip=True) == "Haustiere erlaubt":
            listing.pets_allowed = True
            break

    rauchen_val = attrs.get("Rauchen")
    if rauchen_val is not None:
        rv = rauchen_val.strip().lower()
        if "raucher willkommen" in rv:
            listing.smoking_ok = True
        elif "nichtraucher" in rv or "unerwünscht" in rv or "unerwuenscht" in rv:
            listing.smoking_ok = False
        else:
            listing.smoking_ok = None
    else:
        listing.smoking_ok = None

    ob_val = attrs.get("Online-Besichtigung")
    if ob_val is not None:
        ov = ob_val.strip().lower()
        if ov == "möglich" or ov == "moeglich":
            listing.online_viewing = True
        elif ov == "nicht möglich" or ov == "nicht moeglich":
            listing.online_viewing = False
        elif "nicht" in ov and ("möglich" in ov or "moeglich" in ov):
            listing.online_viewing = False

    listing.languages = []

    photo_urls: list[str] = []
    seen_urls: set[str] = set()
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        _walk_ld_json_images(data, photo_urls, seen_urls)
    listing.photo_urls = photo_urls[:12]

    cover_meta = soup.find("meta", attrs={"property": "og:image"})
    if cover_meta and cover_meta.get("content"):
        listing.cover_photo_url = str(cover_meta["content"])
    elif listing.photo_urls:
        listing.cover_photo_url = listing.photo_urls[0]
    else:
        listing.cover_photo_url = None

    # Posting date lives on the detail page only — search cards don't
    # expose it on Kleinanzeigen. The agent's freshness gate consumes
    # `posted_at` after `scrape_detail` returns, so a stale ad costs us
    # one detail fetch but never a write. Selector pinned per the plan
    # (`docs/SCRAPER_LOCAL_AND_FRESHNESS_PLAN.md` §3.2): the date sits
    # in the FIRST `<div>` child of `#viewad-extra-info`; `#viewad-cntr`
    # (the view counter) is a sibling div we must NOT match.
    date_span = soup.select_one("#viewad-extra-info > div:first-child > span")
    if date_span is not None:
        listing.posted_at = _parse_posting_date_de(
            date_span.get_text(" ", strip=True)
        )

    return listing


class KleinanzeigenSource:
    """Anonymous httpx + bs4 source for kleinanzeigen.de (Munich-only for now)."""

    name = name
    kind_supported = kind_supported
    search_page_delay_seconds = search_page_delay_seconds
    detail_delay_seconds = detail_delay_seconds
    max_pages = max_pages
    refresh_hours = refresh_hours

    def looks_like_block_page(self, text: str, status: int) -> bool:
        if status in (403, 429) or status >= 500:
            return True
        if len(text) < 5000:
            return True
        if _BLOCK_HINT_RE.search(text):
            return True
        return False

    def _build_search_url(self, *, kind: Kind, page: int) -> tuple[str, str, int]:
        vertical_slug = "s-auf-zeit-wg" if kind == "wg" else "s-mietwohnung"
        cat_id = 199 if kind == "wg" else 203
        city_slug = "muenchen"
        seite_seg = "" if page <= 1 else "/seite:%d" % page
        locality_id = KA_LOCALITY_BY_CITY["München"]
        url = "%s/%s/%s%s/c%dl%d" % (
            KA_BASE_URL,
            vertical_slug,
            city_slug,
            seite_seg,
            cat_id,
            locality_id,
        )
        return url, city_slug, locality_id

    async def search(self, *, kind: Kind, profile: SearchProfile) -> list[Listing]:
        if kind not in self.kind_supported:
            return []

        resolved_city = profile.city or "München"
        locality_id = KA_LOCALITY_BY_CITY.get(resolved_city)
        if locality_id is None:
            logger.warning(
                "Kleinanzeigen locality unknown for city %r; falling back to München (6411)",
                resolved_city,
            )
            locality_id = KA_LOCALITY_BY_CITY["München"]
            resolved_city = "München"

        results: list[Listing] = []
        async with httpx.AsyncClient(
            headers=KA_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            await client.get("%s/" % KA_BASE_URL)
            await asyncio.sleep(self.search_page_delay_seconds)

            for page in range(1, self.max_pages + 1):
                vertical_slug = "s-auf-zeit-wg" if kind == "wg" else "s-mietwohnung"
                cat_id = 199 if kind == "wg" else 203
                city_slug = "muenchen"
                seite_seg = "" if page <= 1 else "/seite:%d" % page
                url = "%s/%s/%s%s/c%dl%d" % (
                    KA_BASE_URL,
                    vertical_slug,
                    city_slug,
                    seite_seg,
                    cat_id,
                    locality_id,
                )

                try:
                    resp = await client.get(url)
                except httpx.HTTPError:
                    if page == 1:
                        raise
                    logger.warning("Kleinanzeigen search HTTP error on page %d, stopping", page)
                    break

                if self.looks_like_block_page(resp.text, resp.status_code):
                    logger.warning(
                        "Kleinanzeigen search block-like response (status=%s, len=%d) on page %d",
                        resp.status_code,
                        len(resp.text),
                        page,
                    )
                    break

                page_listings = parse_search_page_ka(resp.text, kind=kind, city=resolved_city)
                if not page_listings:
                    break
                results.extend(page_listings)

                if page < self.max_pages:
                    await asyncio.sleep(self.search_page_delay_seconds)

        return results

    async def scrape_detail(self, stub: Listing) -> Listing:
        async with httpx.AsyncClient(
            headers=KA_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            try:
                resp = await client.get(str(stub.url))
            except httpx.HTTPError:
                logger.warning("Kleinanzeigen detail HTTP error for %s", stub.id)
                return stub

            if self.looks_like_block_page(resp.text, resp.status_code):
                logger.warning(
                    "Kleinanzeigen detail block-like response (status=%s, len=%d) for %s",
                    resp.status_code,
                    len(resp.text),
                    stub.id,
                )
            else:
                parse_listing_page_ka(resp.text, stub)

        await asyncio.sleep(self.detail_delay_seconds)
        return stub
