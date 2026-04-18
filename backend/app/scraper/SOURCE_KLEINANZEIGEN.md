# Source: kleinanzeigen.de

> Anonymous httpx + BeautifulSoup scrape of `kleinanzeigen.de` (formerly eBay Kleinanzeigen). Targets **both** WG rooms ("Auf Zeit & WG", `c199`) and full apartments ("Mietwohnungen", `c203`). Sister source to [`./SOURCE_WG_GESUCHT.md`](./SOURCE_WG_GESUCHT.md) and [`./SOURCE_TUM_LIVING.md`](./SOURCE_TUM_LIVING.md). Anti-bot is **Akamai Bot Manager** (not DataDome as commonly reported), and an anonymous httpx client survives a 5-page paginated pass cleanly today — see warnings below.

## At a glance

- **Site:** `https://www.kleinanzeigen.de`
- **Transport:** `httpx + BeautifulSoup` (mirroring [`../wg_agent/browser.py`](../wg_agent/browser.py)). A homepage cookie warm-up is **recommended but not required** in today's recon — both warmed and cold requests returned 200 + parsable HTML on every endpoint we tried. Reuse one `AsyncClient` per pass so Akamai sees a stable cookie jar (`bm_sz`, `_abck`, `up`) across pages. If anti-bot escalates, escalate to `curl_cffi` (TLS/HTTP2 fingerprint impersonation) for the warm-up only; Playwright is the last resort.
- **Anonymous-accessible?** **Yes.** Verified: 5 sequential search-page fetches, 3 detail-page fetches, plus filtered/paginated/alt-slug variants — all returned 200 with full HTML, no challenge interstitial, no 403 / 429. Cookies dropped on first response are Akamai bot-manager (`bm_sz`, `_abck`), not DataDome.
- **Listing kinds offered:** `wg` (Auf Zeit & WG, `c199`) **and** `flat` (Mietwohnungen, `c203`). Both verticals are public, share one listing-card DOM, and share the `/s-anzeige/<slug>/<adid>-<categoryId>-<localityId>` detail-page schema. The flat vertical also accepts the alternate slug `/s-wohnung-mieten/` (used by the breadcrumb anchor on flat detail pages); both `/s-mietwohnung/` and `/s-wohnung-mieten/` route to the same content.
- **Suggested cadence:** one full search pass every **15 min** per vertical (≈2× wg-gesucht's interval); refresh detail pages after **24 h**. Hold consecutive search-page fetches at ≥2.5 s apart and detail fetches at ≥3.5 s apart (matches our recon pacing). Backoff aggressively on 403/429 or block-page detection.

## Recon summary (date: 2026-04-18)

**Verified via:** anonymous `httpx.AsyncClient` (Chrome UA + `de-DE` Accept-Language + `follow_redirects=True`) and `curl` with shared cookie jar. Saved fixtures used during selector verification:

- `/tmp/ka_home.html` — homepage warm-up (~34 KB, status 200)
- `/tmp/ka_wg.html` — WG search page 1 of `/s-auf-zeit-wg/muenchen/c199l6411` (~360 KB, 27 cards)
- `/tmp/ka_flat.html` — flat search page 1 of `/s-mietwohnung/muenchen/c203l6411` (~360 KB, 27 cards)
- `/tmp/ka_wg_p2.html`, `/tmp/ka_wg_p50.html` — pagination probes (page 2 = 27 cards / page 50 = 25 cards; both 200, see "Robots.txt notes" for the pagination ceiling)
- `/tmp/ka_wg_filter.html` — `/preis:800:1500/` filter (returned 27 cards in 800-1031 € range; **note**: `preis:` is `robots.txt`-disallowed, see "Robots.txt notes")
- `/tmp/ka_flat_alt.html` — `/s-wohnung-mieten/muenchen/c203l6411` (returned identical content to `/s-mietwohnung/`)
- `/tmp/ka_detail.html`, `ka_detail2.html`, `ka_detail3.html` — three detail pages (commercial WG, private WG, flat) — all 200 with `og:latitude`/`og:longitude` meta tags present
- `/tmp/ka_robots.txt` — full live `robots.txt` (~10 KB)
- 5-sequential-page pass against `/s-auf-zeit-wg/muenchen/seite:N/c199l6411` for `N=1..5` with 2.5 s delay: all 200, 129 unique listing ids returned, no escalation observed. Selectors verified by direct `bs4` queries on these fixtures.

What was actually observed:

- **Immobilien category root for Munich:** `https://www.kleinanzeigen.de/s-immobilien/muenchen/c195l6411` — left sidebar lists every housing subcategory with counts. Confirms `c195` is the parent Immobilien category and `l6411` is the Munich locality id used in **search** URLs.
- **WG vertical (Auf Zeit & WG):** `https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/c199l6411` (page title "Auf Zeit & WG in München - Bayern"). Returned 27 `<article class="aditem" data-adid="…">` cards on page 1 in raw HTML.
- **Flat vertical (Mietwohnungen):** direct GET on `https://www.kleinanzeigen.de/s-mietwohnung/muenchen/c203l6411` returned 27 cards, identical card DOM. Page title "Mietwohnung in München - Bayern". The breadcrumb anchor on flat detail pages uses the alternate slug `/s-wohnung-mieten/` — both slugs route to the same vertical, confirmed by sample-id diffing.
- **Detail page (sample):** `https://www.kleinanzeigen.de/s-anzeige/moebliert-naehe-prinzregentenplatz-moderne-klare-linien/3362398693-199-6461`. Routing is **adid-only**: requesting the same ad as `/s-anzeige/x/3362398693-199-6411` (placeholder slug, wrong locality id) still returned the canonical detail page; the server rewrote the locality id in the response's `<meta property="og:url">`. Don't hand-construct the trailing `<cat>-<loc>` triplet — read it from the search-card `data-href`.
- **GDPR overlay:** appears on the first browser visit but is purely client-side (a JS-rendered modal). The raw HTML behind it is fully populated; httpx never sees it.
- **No Cloudflare / DataDome interstitial observed** during the entire httpx recon. Cookies dropped by the homepage are Akamai bot-manager (`bm_sz`, `_abck`, `kameleoonVisitorCode`, `up`, `lnFeMonitoring`) — no `datadome` cookie at any point. The "DataDome on Kleinanzeigen" reputation in the wider web doesn't reflect what an anonymous httpx client sees today.
- **`robots.txt`:** fetched at `https://www.kleinanzeigen.de/robots.txt`. **Listing paths (`/s-auf-zeit-wg/`, `/s-mietwohnung/`, `/s-wohnung-mieten/`, `/s-anzeige/`, `/s-immobilien/`) are NOT disallowed** for `User-agent: *`. But several patterns we considered are: see "Robots.txt notes" for the exact strings that bite us.

## Identifier mapping

- **External id format:** numeric ad id, ~10 digits in current-era listings (sample: `3362398693`). Kleinanzeigen ids have varied in length historically; do **not** hard-code a digit count. Match `\d+` greedily against the documented anchors below.
- **Mapping to `ListingRow.id`:** `f"kleinanzeigen:{external_id}"` (e.g. `"kleinanzeigen:3362398693"`).
- **Extraction strategy (in order of preference):**
  1. **Listing-card attribute** — `article.aditem` carries both `data-adid="<numeric>"` and `data-href="/s-anzeige/<slug>/<adid>-<cat>-<loc>"`. Verified on every one of the 27 cards in `/tmp/ka_wg.html` and `/tmp/ka_flat.html`.
  2. **Detail-URL regex** — `re.compile(r"/s-anzeige/[^/]+/(\d+)-\d+-\d+(?:[?#].*)?$")` matches the trailing `<adid>-<categoryId>-<localityId>` triplet. The first group is the external id.
  3. **Sidebar `Anzeigen-ID` — for cross-checks only.** On the detail page, `<ul class="flexlist text-light-800"><li>Anzeigen-ID</li><li>3362398693</li></ul>` exposes the same id and lets us assert the URL-derived id and the page's own id match. We always have the id from `data-adid` first; this is a defensive sanity check.
  4. **`<a href>` walk on the card** — same regex against every anchor in the card, then dedup. Mirrors `_LISTING_ID_RE` in [`../wg_agent/browser.py`](../wg_agent/browser.py).

## URL patterns for the two listing kinds

- **WG (`kind='wg'`):** `https://www.kleinanzeigen.de/s-auf-zeit-wg/<city-slug>/c199l<localityId>` — confirmed for Munich as `/s-auf-zeit-wg/muenchen/c199l6411`. Category `c199` = "Auf Zeit & WG".
- **Flat (`kind='flat'`):** `https://www.kleinanzeigen.de/s-mietwohnung/<city-slug>/c203l<localityId>` — confirmed for Munich as `/s-mietwohnung/muenchen/c203l6411`. The alternate slug `/s-wohnung-mieten/<city-slug>/c203l<localityId>` returns the same listings (the breadcrumb anchor on flat detail pages uses this form). Category `c203` = "Mietwohnungen".
- **Generalization to other cities:** the `c<categoryId>` segment is global (`c199` / `c203` apply everywhere). The `l<localityId>` segment is per-city. Two ways to discover it:
  1. Visit `https://www.kleinanzeigen.de/s-immobilien/<city-slug>/` (no `c…l…` suffix) and inspect the resulting redirect — it lands on `/s-immobilien/<city-slug>/c195l<localityId>`. Capture the `l<id>` segment from the final URL.
  2. The homepage city-autocomplete (`PLZ oder Ort` searchbox) returns suggestions with the locality id baked in. Maintain a small city catalogue similar to [`CITY_CATALOGUE`](../wg_agent/models.py) for the cities we care about (Muenchen=`6411`, …) and discover lazily for the rest.
- **Locality-id discrepancy resolved.** The trailing locality id in `/s-anzeige/<slug>/<adid>-<cat>-<loc>` is a **per-listing neighborhood id** (e.g. `6461` = Bogenhausen, `6418` = Schwabing-Freimann, `6479` = Pasing-Obermenzing — all sub-localities under Munich's `6411`). The locality id is **not used as a routing key**; we requested `/s-anzeige/x/3362398693-199-6411` (placeholder slug, search-style locality id) and the server returned the canonical page anyway, with `<meta property="og:url">` rewritten to the canonical `…-199-6461`. **Conclusion:** for URL-building, use the `data-href` attribute from the search card verbatim. The trailing triplet is informational; never hand-construct it.
- **Filters (URL-path segments):**
  - `preis:<min>:<max>` — verified working with `/preis:800:1500/c199l6411` (returned 27 results in the expected price range). **But:** `robots.txt` says `Disallow: /*/preis:*`. Don't use this in production scrapes.
  - `anbieter:privat` — pattern verified by inspection of breadcrumb-anchor URLs that include `anbieter:`. Also `robots.txt`-disallowed (`Disallow: /*/anbieter:*`).
  - `sortierung:empfohlen` — appears in breadcrumb anchors. **Also `robots.txt`-disallowed** (`Disallow: /*/sortierung:*`); the site emits these links itself but tells crawlers not to follow them.
  - **What this means for the scraper:** mirror wg-gesucht's strategy — pass **no** filter segments (skip `preis:`, `anbieter:`, `sortierung:`) and let the scorecard evaluator filter. Robots.txt-respectful is the only safe default.

## How to list listings (search)

- **HTTP request:** `GET https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/c199l6411` (or the `c203l6411` flat variant) with headers:
  ```
  User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
  Accept-Language: de-DE,de;q=0.9,en;q=0.8
  Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
  Accept-Encoding: gzip, deflate, br
  ```
- **Cookies needed before the first useful response:** **No, but reuse a session.** Verified: a cold request directly to `/s-auf-zeit-wg/muenchen/c199l6411` (no warm-up) still returned 200 with 27 parsable cards. The Akamai cookies (`bm_sz`, `_abck`, `up`) get set on first response, so subsequent requests within the same `httpx.AsyncClient` benefit. The recommended (defensive, future-proof) pattern is still:
  1. `GET https://www.kleinanzeigen.de/` first, with `follow_redirects=True`, persisting cookies into a session-scoped `httpx.AsyncClient`. This collects Akamai's bot-manager cookies (`bm_sz`, `_abck`, `up`, `kameleoonVisitorCode`, `lnFeMonitoring`). Discard the response body.
  2. **Don't** programmatically click "Alle Cookies und Tracking akzeptieren". Listing pages render to httpx without the consent string.
  3. Reuse the same `AsyncClient` (and therefore the same cookie jar) for every search and detail fetch in the pass.
- **Response shape:** server-rendered HTML. Each listing card is **verified** as:
  ```
  <article class="aditem" data-adid="3362398693"
           data-href="/s-anzeige/moebliert-naehe-prinzregentenplatz-moderne-klare-linien/3362398693-199-6461">
    <div class="aditem-image">…<img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/…?rule=$_59.AUTO" …/></div>
    <div class="aditem-main">
      <div class="aditem-main--top">
        <div class="aditem-main--top--left"><i …icon-pin-gray…/> 81675 Bogenhausen</div>
        <div class="aditem-main--top--right"></div>           ← always empty in current layout
      </div>
      <div class="aditem-main--middle">
        <h2 class="text-module-begin"><a class="ellipsis" href="/s-anzeige/…">Möbliert: Nähe Prinzregentenplatz: …</a></h2>
        <p class="aditem-main--middle--description">Weitere Informationen, viele hochauflösende Fotos…</p>
        <p class="aditem-main--middle--tags">108 m² · 2 Zi.</p>
        <div class="aditem-main--middle--price-shipping">
          <p class="aditem-main--middle--price-shipping--price"><span>2.990 €</span></p>
        </div>
      </div>
      <div class="aditem-main--bottom"><p class="text-module-end"><span class="simpletag">Von Privat</span></p></div>
    </div>
  </article>
  ```

  **Verified search-card selectors** (every count below was measured against the 27 articles in `/tmp/ka_wg.html`):

  | Field                | Selector                                                              | Verified count        | Notes                                                                                                                                  |
  | -------------------- | --------------------------------------------------------------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
  | Card root            | `article.aditem[data-adid]` *(or `li.ad-listitem > article.aditem`)*  | 27 / 27               | `data-adid` is the numeric external id; `data-href` on the same `<article>` is the canonical detail URL.                              |
  | Title + detail URL   | `article > div.aditem-main h2.text-module-begin a.ellipsis`           | 27 / 27               | The `<a>` `href` is the same as `article[data-href]`. Either works.                                                                    |
  | Price                | `p.aditem-main--middle--price-shipping--price`                        | 27 / 27               | Inner `<span>` holds e.g. `2.990 €`. Strip thousands separator (`.`), `€`, and trailing whitespace; some ads append `VB` (= "20 € VB"). |
  | Quick-fact tags      | `p.aditem-main--middle--tags`                                         | 27 / 27               | Dot-separated text like `108 m² · 2 Zi.` or `14 m² · 1 Zi. · Online-Besichtigung`. **This is where to grep size, room count, and online-viewing flag** — the doc previously claimed these came from separate selectors. They don't on the search card. |
  | Teaser text          | `p.aditem-main--middle--description`                                  | 27 / 27               | First ~150 chars of the description. **Not** `.aditem-main--bottom .text-module-end` (that selector matched only 8/27 and contains "Von Privat", not teaser text). |
  | Location             | `div.aditem-main--top--left`                                          | 27 / 27               | Text like `81675 Bogenhausen`. Note `\u200B` (zero-width space) embedded in district names like `Schwabing-\u200bFreimann`; strip when rendering. The first 5 digits are the PLZ. **City is not in this string** — it's implied by the search URL (`muenchen`). |
  | Posting date         | *(not present on the search card — current Kleinanzeigen layout)*     | 0 / 27                | `.aditem-main--top--right` exists for every card but is **empty**. Posting date is only on the detail page (`#viewad-extra-info`). The doc's previous claim that this selector held `Heute / Gestern / dd.mm.yyyy` was wrong. |
  | Seller-type tag      | `.aditem-main--bottom .text-module-end span.simpletag`                | 8 / 27 (when present) | `Von Privat` for private sellers; absent for commercial sellers (Mr. Lodge etc.). Useful for "filter privates only" without using the robots-disallowed `anbieter:privat` URL filter. |
  | Cover image          | `article > div.aditem-image img[src]`                                 | 27 / 27               | `?rule=$_59.AUTO` is the small thumbnail variant; swap to `?rule=$_59.JPG` for full-resolution.                                       |
  | TopAd badge          | `.aditem-image--badges .badge-topad`                                  | varies                | Marks promoted listings. They appear once per pass; useful for dedupe across `seite:1` and `seite:2`.                                  |

  - **Defensive fallback** (mirroring [`parse_search_page`](../wg_agent/browser.py)): walk every `<a href>` matching the detail-URL regex, walk up to the nearest `article` ancestor, regex out price (`(\d+(?:[.,]\d+)?)\s*€`), size (`(\d+(?:[.,]\d+)?)\s*m²`), Mitbewohner-count (`(\d+)\s*Mitbewohner`), location, posting date.

  - **HTML-parser gotcha (Python 3.14):** Kleinanzeigen ships an unterminated numeric character reference `&#8203` (zero-width space, no trailing `;`). bs4's bundled `html.parser` (the only one available in the project venv — no `lxml`, no `html5lib`) raises `ValueError: invalid literal for int() with base 10` on it. Pre-process raw HTML with `re.sub(r"&#(\d+)(?![\d;])", r"&#\1;", html)` before feeding to BeautifulSoup. The recipe at the bottom of this file shows the fix.

- **Pagination:** Kleinanzeigen uses a `/seite:<N>` path segment inserted **before** the `c<cat>l<loc>` token, e.g. `https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/seite:2/c199l6411`. Verified with status 200 and 27 cards on `seite:2` (and 25 cards on `seite:50`). **Robots.txt cap:** `Disallow: /*/seite:6*` through `/*/seite:59*` — pages 1-5 are crawl-allowed, pages 6+ are not. Use `max_pages=5`.
- **Pagination footer DOM** (verified against `#srchrslt-pagination` block):
  ```
  <div class="pagination">
    <div class="pagination-pages">
      <span class="pagination-current">1</span>
      <a href="/s-auf-zeit-wg/muenchen/seite:2/c199l6411" class="pagination-page" aria-label="Seite 2">2</a>
      …
      <a href="/s-auf-zeit-wg/muenchen/seite:N/c199l6411" class="pagination-next" …>Nächste</a>
    </div>
  </div>
  ```
  **Verified pagination selectors:** `div.pagination` (1 root), `span.pagination-current` (1 = current page), `a.pagination-page` (4 = visible page numbers), `a.pagination-next` (1 = "Nächste" link). Stop paginating when (a) page index reaches 5 (robots.txt limit), (b) the `pagination-next` anchor is absent, or (c) the page contains zero `article.aditem` cards.
- **Pseudocode (mirrors [`anonymous_search`](../wg_agent/browser.py)):**
  ```python
  async def anonymous_search_kleinanzeigen(
      kind: Literal["wg", "flat"],
      city_slug: str,
      locality_id: int,
      max_pages: int = 5,  # ← respects robots.txt seite:6..59 disallow
  ) -> list[Listing]:
      category_id = 199 if kind == "wg" else 203
      vertical_slug = "s-auf-zeit-wg" if kind == "wg" else "s-mietwohnung"
      results: list[Listing] = []
      async with httpx.AsyncClient(
          headers=_KA_HEADERS,
          follow_redirects=True,
          timeout=20.0,
      ) as client:
          await client.get(f"{KA_BASE_URL}/")
          await asyncio.sleep(1.0)

          for page_index in range(1, max_pages + 1):
              page_seg = "" if page_index == 1 else f"/seite:{page_index}"
              url = (
                  f"{KA_BASE_URL}/{vertical_slug}/{city_slug}"
                  f"{page_seg}/c{category_id}l{locality_id}"
              )
              resp = await client.get(url)
              soup = BeautifulSoup(resp.text, "html.parser")
              if _looks_like_block_page_ka(soup, resp.text):
                  break
              page_listings = parse_search_page_ka(soup, kind=kind)
              if not page_listings:
                  break
              results.extend(page_listings)
              await asyncio.sleep(KA_PAGE_DELAY_SECONDS)
      return results
  ```

## How to read one listing (detail)

- **HTTP request:** `GET https://www.kleinanzeigen.de/s-anzeige/<slug>/<adid>-<categoryId>-<localityId>` with the same headers and the same cookie-bearing `AsyncClient` from the search pass. The `<slug>` and the `<localityId>` are tolerated by the server (verified: `/s-anzeige/x/3362398693-199-6411` was rewritten to the canonical URL). Always pull the URL from the search card's `data-href` rather than constructing it.
- **Field-by-field mapping into the domain `Listing`** (every selector below was verified by direct `bs4` query against `/tmp/ka_detail.html`, `ka_detail2.html`, `ka_detail3.html` — three distinct ads spanning commercial-WG / private-WG / flat verticals):

  | `Listing` field                     | Source on Kleinanzeigen detail page                                                                                                                                                                                                                                                                                                                                                |
  | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `id`                                | from search-card `data-adid` (preferred) or detail-URL trailing `(\d+)-\d+-\d+`. Then `f"kleinanzeigen:{numeric}"`. Cross-check against the sidebar `<ul class="flexlist text-light-800"><li>Anzeigen-ID</li><li><id></li></ul>` — verified to match for all three ads.                                                                                                            |
  | `url`                               | the `data-href` from the search card, or `<meta property="og:url">` on the detail page (canonical, with the server-corrected locality id). Both verified. |
  | `kind`                              | `'wg'` if the search vertical was `c199`, `'flat'` if it was `c203`. Set by the scraper, not parsed.                                                                                                                                                                                                                                                                              |
  | `title`                             | `h1#viewad-title` — verified across all 3 detail pages (and falls back to a plain `<h1>` lookup since there's only one h1). Sample: `<h1 id="viewad-title" class="…">Möbliert: Nähe Prinzregentenplatz: moderne, klare Linien</h1>`. |
  | `price_eur`                         | `h2#viewad-price` (also matches `#viewad-price`) — verified across all 3. Renders e.g. `2.990 €` for fixed prices, `20 € VB` for "Verhandlungsbasis". Strip thousands separator (`.`), `€`, and trailing `VB`; parse to int. Leave `price_eur=None` for `VB`-only or `Auf Anfrage` ads.                                                                                            |
  | `size_m2`                           | from the attribute table (label `Wohnfläche`, value `108 m²`). Selector: `li.addetailslist--detail` whose text starts with `Wohnfläche`, with the value in the inner `span.addetailslist--detail--value`. Verified raw HTML: `<li class="addetailslist--detail">Wohnfläche<span class="addetailslist--detail--value">108 m²</span></li>`. Parent is `<div class="addetailslist">` (not `<ul>`). |
  | `wg_size`                           | WG vertical only. Label `Anzahl Mitbewohner` (= existing flatmates). Same `addetailslist--detail` row pattern. Verified: `5` for the recon's private WG ad. Map to total flatmates via the convention agreed for parity with wg-gesucht's `(\d+)er WG` (which counts the new tenant); see "Open questions". |
  | `address` / `city` / `district`     | `#viewad-locality` — text like `81675 München - Bogenhausen`. Note: the selector matches **two** elements (one in the header info, one in the map widget) because Kleinanzeigen reuses the `id` (HTML-invalid but tolerated). Both have identical text; pick the first via `.select_one()`. Split on whitespace + ` - `: PLZ → first 5 digits, city → middle, district → tail. Backup: `<meta property="og:locality">` (e.g. `München - Bogenhausen`). |
  | `lat` / `lng`                       | **Verified present:** `<meta property="og:latitude" content="48.1381386"/>` and `<meta property="og:longitude" content="11.6033512"/>` on the detail page. Confirmed across all 3 sample ads. Coordinate precision is street-level (7 decimal digits), **not** the postal-code centroid as previously assumed. **No geocoding needed.** Selector: `soup.find("meta", attrs={"property": "og:latitude"})["content"]`. |
  | `available_from`                    | `addetailslist--detail` row labelled `Verfügbar ab` — verified on the flat sample ad as `April 2026` (month-year text, not `dd.mm.yyyy`). On WG ads it may also render `Mai 2026`-style or be absent. Parse as `date(year, month, 1)` for month-year strings; treat absence as `None` (Mr. Lodge–style commercial ads omit the row entirely). |
  | `available_to`                      | only present when `Mietart: befristet`. Look for a `Bis` or `Verfügbar bis` row in `addetailslist--detail`. The recon's commercial ad was `Mietart: unbefristet` → `available_to = None`. |
  | `description`                       | `#viewad-description-text` — verified across all 3 detail pages. Sibling `#viewad-description` is the wrapper that prepends the literal label "Beschreibung "; prefer `#viewad-description-text` to skip the label. Strip leading/trailing whitespace; preserve `\n`. Scrub `<script>` / `<iframe>` like wg-gesucht does. **⚠ Storage truncation:** `ListingRow.description` is currently `VARCHAR(255)` (SQLModel default for bare `Optional[str]`); Kleinanzeigen descriptions are routinely 1–5 KB and will silently truncate. Fix is step 1 of [`../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md) (widen to `TEXT` + force re-scrape) and **must land before** any kleinanzeigen writes. |
  | `photo_urls` / `cover_photo_url`    | **Two verified strategies:** (a) DOM walk: `div.galleryimage-element img` (20 elements on the recon's 20-photo ad) — these wrap the same gallery images that share the duplicated `id="viewad-image"`. (b) **Cleaner**: parse every `<script type="application/ld+json">` block, keep the ones with `@type=ImageObject`, dedup by `contentUrl`. Verified to return all 23 unique gallery photos for the recon's 20-photo ad (some are slideshow re-emissions). `cover_photo_url = <meta property="og:image">` directly. Reuse [`_parse_photo_urls`](../wg_agent/browser.py) heuristics: filter logos/icons/placeholders, dedupe, cap at 12. The doc's previous `#viewad-thumbnail-bar img` selector returned 0 matches and does not exist. |
  | `furnished`                         | `<li class="checktag">Möbliert</li>` inside `<ul class="checktaglist">`. **Caveat:** the flat sample also exposes `<li class="checktag">Möbliert/Teilmöbliert</li>` — match `^Möbliert(/Teilmöbliert)?$` to catch both. `True` when present, else `None` (don't infer `False` from absence — many ads skip the equipment block entirely). |
  | `pets_allowed`                      | `<li class="checktag">` whose text equals `Haustiere erlaubt` (verified label; appears in the WG-vertical filter sidebar). Same true-or-None rule.                                                                                                                                                                                                                                  |
  | `smoking_ok`                        | `addetailslist--detail` row labelled `Rauchen` — verified value `unerwünscht` on the recon's sample, mapped to `False`. Other observed values (per filter sidebar): `Raucher willkommen` → `True`, `Nichtraucher` → `False`. Label-driven parse on the `addetailslist--detail--value` span.                                                                                       |
  | `online_viewing`                    | `addetailslist--detail` row labelled `Online-Besichtigung` — verified value `Möglich` / `Nicht möglich` on the recon's flat ad. Map `Möglich` → `True`, `Nicht möglich` → `False`, absent row → `None`. Also visible as a free-text token in the search-card's `p.aditem-main--middle--tags` ("…· Online-Besichtigung"), useful as a stub-time signal. |
  | `languages`                         | **not exposed** on Kleinanzeigen. Leave `[]`.                                                                                                                                                                                                                                                                                                                                      |
  | *(extra)* posting date              | `#viewad-extra-info > div:first-child > span` — text `dd.mm.yyyy` (verified `07.04.2026` / `17.04.2026` / `14.04.2026` on the three samples). Sibling `#viewad-cntr` holds the view counter. Persist if/when the schema gets a `posted_at` column. |
  | *(extra)* seller name / commercial  | `<div class="aditem-main--middle--store">` on the search card holds `<img>` logo + `<span class="aditem-main--middle--store-text">Mr. Lodge GmbH</span>`. Useful for filtering out commercial sellers without resorting to the robots-disallowed `anbieter:privat` URL filter.                                                                                                |

Fields the source does **not** populate (filled later by the matcher per user, not by the scraper): `score`, `score_reason`, `match_reasons`, `mismatch_reasons`, `components`, `veto_reason`, `best_commute_minutes`.

## Anti-bot posture

- **WAF / challenge observations:** in the verified recon (anonymous httpx, headers below, fresh client, no warm-up — and again with warm-up, and again across 5 sequential paginated fetches) **every response was 200 with the full listing DOM**. No Cloudflare interstitial, no DataDome challenge HTML, no `<script src="…datadome…">` reference, no 403 / 429. Cookies set by Kleinanzeigen on first response are **Akamai bot-manager** (`bm_sz`, `_abck`, `kameleoonVisitorCode`, `up`, `lnFeMonitoring`, plus content-app cookies `CSRF-TOKEN`, `__ka-ls`, `__ka-sh`, `__ka_postad-v1`). Kleinanzeigen's "DataDome" reputation in the wider web does not match what an httpx client experiences today.
- **Cookie warm-up strategy (recommended, not required):**
  1. First request of every fresh `httpx.AsyncClient` session: `GET https://www.kleinanzeigen.de/` with the same headers as the listing fetches and `follow_redirects=True`. Persist cookies into the client's jar. Discard the response body.
  2. Sleep ≥ 1 s, then begin search/detail fetches with the same client.
  3. **If** subsequent fetches return 4xx or anti-bot HTML, escalate to `curl_cffi.requests.AsyncSession(impersonate="chrome124")` (TLS + HTTP/2 fingerprint impersonation) for the warm-up only, then re-attempt with httpx using the harvested cookie jar. Real browser (Playwright) is the last resort. We did not need either escalation in today's recon.
- **Headers (minimum baseline, observed during recon):**
  ```
  User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
  Accept-Language: de-DE,de;q=0.9,en;q=0.8
  Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
  Accept-Encoding: gzip, deflate, br
  ```
  These match the `_anon_client` defaults in [`../wg_agent/browser.py`](../wg_agent/browser.py); reuse the same constants.
- **Rate limit guidance:** ≥ **2.5 s** between consecutive search-page fetches (≈ 1.6× wg-gesucht's `ANONYMOUS_PAGE_DELAY_SECONDS`); ≥ **3.5 s** between detail-page fetches; ≥ **15 min** between full passes per vertical. On 403 / 429 / block-page detection: exponential backoff starting at 30 s, cap at 30 min, and surrender the current pass.
- **Block-page detection (analogue of `_looks_like_block_page` in [`../wg_agent/browser.py`](../wg_agent/browser.py)):** treat the response as a block when **none** of the verified positive markers are present, AND any of the negative signals fire. Verified positive markers (a healthy response always has at least one):
  - `soup.select("article.aditem[data-adid]")` returns ≥1 (search page)
  - `soup.select_one("h1#viewad-title")` is not None (detail page)
  - `soup.find("meta", attrs={"property": "og:url"})` content starts with `https://www.kleinanzeigen.de/s-anzeige/` (detail page, canonical URL)

  Negative / block signals:
  - response status is `403` or `429`
  - response body matches `/datadome|data-dome|please enable js and cookies|verifying you are human|sicherheits(über|ueber)pr(ü|ue)fung|ungew(ö|oe)hnlichen datenverkehr|automated requests|please verify you are not a robot/i` (defensive — none of these strings appeared in the 8+ live responses we captured today)
  - response body contains `<script src="…datadome…">`
  - HTML title is `Kleinanzeigen – früher eBay Kleinanzeigen.…` (the homepage title) when the URL we requested was `/s-anzeige/…` or `/s-auf-zeit-wg/…` (suggests a soft-redirect to the homepage as a soft block — verify by checking `response.url` after `follow_redirects=True`)
  - `len(response.text) < 5_000` for a search or detail URL (a real search page is ≥350 KB; a real detail page is ≥250 KB; anything tiny is a challenge stub)

  When detected, return the unmodified stub (or empty list) so the scheduler persists what it has and retries later — never crash.

## Robots.txt notes

`https://www.kleinanzeigen.de/robots.txt` (~10 KB, captured 2026-04-18) declares one `User-agent: *` block plus per-bot blocks for Adsbot-Google, GPTBot, ChatGPT-User, PerplexityBot, OAI-SearchBot, AmazonAdBot, Mediapartners-Google, Google-Display-Ads-Bot, and `zoomRank/2.0` (totally disallowed). For `User-agent: *` (us), the rules that touch our planned URLs are:

- **Listing-path roots — NOT disallowed (crawlable):** `/s-auf-zeit-wg/`, `/s-mietwohnung/`, `/s-wohnung-mieten/`, `/s-anzeige/`, `/s-immobilien/`. Also no `Crawl-delay` directive — the pacing in "Anti-bot posture" is our self-imposed budget.
- **Pagination ceiling:** `Disallow: /*/seite:6*` through `/*/seite:59*`. Pages 1-5 are crawl-allowed, **pages 6 and beyond are forbidden by `robots.txt`**. The 27-cards-per-page × 5 pages = ≤135 listings per pass per vertical. Don't crawl past `seite:5`.
- **URL-path filters — disallowed:** `Disallow: /*/preis:*`, `/*/anbieter:*`, `/*/sortierung:*`, `/*+options:*`, `/*/c*r5` through `/*/c*r200` (radius-around-locality), `/*/l*r5` through `/*/l*r200`, `/*/k0*r5` through `/*/k0*r200`. We tested `/preis:800:1500/` during recon (one-shot, for verification); production scrapes must omit it. Do all numeric range / sorting / radius / "anbieter:privat" filtering client-side after fetching the unfiltered page.
- **Other patterns we never construct (good):** `/checkout.html`, `/m-merkliste*`, `/m-meine-*`, `/m-einloggen.html`, `/p-anzeige-*`, `/api`, `/messages/`, `/SEARCH`, `/HOME`, `/BROWSE`, `/VIP`, `*.json`, `/gdpr/*`, `/liberty/*`, `/bffstatic/*`, `/*?*utm_source=ekde`, `/*/anzeige:gesuche`, `/*/s-anzeige:angebote`, `/s-suchanfrage.html`, `/s-feed.rss`. None of these intersect with what the scraper does today.

## Open questions / TODO

- [ ] **Schema prerequisite: widen `ListingRow.description` to `TEXT`.** Kleinanzeigen's `#viewad-description-text` is routinely 1–5 KB; current `VARCHAR(255)` will silently truncate on write. Sequenced as step 1 of [`../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md). Must land before any kleinanzeigen writes.
- [ ] **`wg_size` semantics.** The visible attribute is `Anzahl Mitbewohner = 5` (existing flatmates). Decide whether to persist this raw or to map to `wg_size = mitbewohner + 1` for parity with wg-gesucht's `(\d+)er WG` (which counts the new tenant as part of the WG). The evaluator (`wg_size_fit` in `evaluator.py`) needs a consistent convention across sources. Verified: the `Anzahl Mitbewohner` row exists; the open question is purely a semantic-mapping choice.
- [ ] **`available_from` date format.** Verified renderings on the recon ads were month-year strings (`April 2026`, `Mai 2026`), not `dd.mm.yyyy`. Some commercial / "sofort verfügbar" ads may render `Sofort` or omit the row entirely. Decide the parse strategy: round month-year to the 1st of the month, treat `Sofort` as `today`, treat absence as `None`.
- [ ] **`available_to` selector confirmation.** No `befristet` ad was sampled (all three recon ads were `unbefristet`). Need one `befristet` ad to confirm the row label (likely `Verfügbar bis` or `Bis`) and date format.
- [ ] **City catalogue extension.** Munich is `6411`. Add Berlin / Hamburg / Frankfurt / Köln / Stuttgart / Leipzig locality ids by the homepage-redirect discovery method, store alongside [`CITY_CATALOGUE`](../wg_agent/models.py) (or a sibling `KA_CITY_CATALOGUE`).
- [ ] **Long-term anti-bot stability.** Today's recon was clean across 5 paginated requests + 3 detail requests + a price-filtered probe with no warm-up rescue needed. We don't know whether Akamai escalates after N requests in a longer-running session, or after observing the same source IP across many passes. Once the scraper ships, monitor for first 4xx and prepare the `curl_cffi` warm-up fallback.

## See also

- [`./README.md`](./README.md) — multi-source contract (id namespacing + `kind` column).
- [`./SOURCE_WG_GESUCHT.md`](./SOURCE_WG_GESUCHT.md) — sibling source recon (template style; cleaner anti-bot than Kleinanzeigen).
- [`./SOURCE_TUM_LIVING.md`](./SOURCE_TUM_LIVING.md) — sibling source recon (GraphQL-based, simplest source).
- [`../wg_agent/browser.py`](../wg_agent/browser.py) — reference implementation of `_anon_client`, `anonymous_search`, `parse_search_page`, `parse_listing_page`, `_looks_like_block_page` — mirror these for the Kleinanzeigen scraper.
- [`../../../docs/WG_GESUCHT.md`](../../../docs/WG_GESUCHT.md) — wg-gesucht site recon (compare DOM-pattern strategies).

## Verified end-to-end recipe

The snippet below was executed against the live site on 2026-04-18 with the project's `backend/venv` (Python 3.14, `httpx==0.28.1`, `beautifulsoup4==4.14.3`, no `lxml`). All selectors and behaviors above derive from running this script — no `**(TODO)**`, no placeholders. Mirror the style of `_anon_client` / `parse_search_page` in [`../wg_agent/browser.py`](../wg_agent/browser.py).

```python
"""Anonymous httpx + BeautifulSoup recipe for kleinanzeigen.de.

Usage: python ka_recipe.py
Behavior:
  1. Anonymous httpx.AsyncClient (Chrome UA + de-DE Accept-Language).
  2. Homepage warm-up (drops Akamai bm_sz/_abck cookies; defensive — not strictly required today).
  3. GET /s-auf-zeit-wg/muenchen/c199l6411 (page 1 of WG vertical).
  4. Parse the 27 search cards with verified selectors.
  5. GET the first card's detail page.
  6. Extract title, price, locality, lat/lng (from og: meta), posting date, attribute table, checktags, photos.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx
from bs4 import BeautifulSoup

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
KA_PAGE_DELAY_SECONDS = 2.5

# Kleinanzeigen ships an unterminated &#8203 (zero-width space) charref.
# bs4's html.parser (Python 3.14) raises on that. Patch defensively.
_BAD_CHARREF = re.compile(r"&#(\d+)(?![\d;])")
_DETAIL_URL_RE = re.compile(r"/s-anzeige/[^/]+/(\d+)-\d+-\d+")


def _ka_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(_BAD_CHARREF.sub(r"&#\1;", html), "html.parser")


def parse_search_page_ka(html: str) -> list[dict]:
    """Parse one Kleinanzeigen search-results page into card stubs."""
    soup = _ka_soup(html)
    out: list[dict] = []
    for art in soup.select("article.aditem[data-adid]"):
        adid = art["data-adid"]
        href = art.get("data-href") or ""
        if href and not href.startswith("http"):
            href = f"{KA_BASE_URL}{href}"

        title_el = art.select_one("h2.text-module-begin a.ellipsis")
        price_el = art.select_one("p.aditem-main--middle--price-shipping--price")
        loc_el = art.select_one(".aditem-main--top--left")
        tags_el = art.select_one("p.aditem-main--middle--tags")
        teaser_el = art.select_one("p.aditem-main--middle--description")
        store_el = art.select_one(".aditem-main--middle--store-text")

        # Tags carry e.g. "108 m² · 2 Zi.   · Online-Besichtigung"
        tags_text = " ".join(tags_el.get_text(" ", strip=True).split()) if tags_el else ""
        size_m = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", tags_text)
        rooms_m = re.search(r"(\d+(?:[.,]\d+)?)\s*Zi\.", tags_text)

        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        price_m = re.search(r"(\d+(?:\.\d+)?)\s*€", price_text)

        loc_text = loc_el.get_text(" ", strip=True).replace("\u200b", "") if loc_el else ""
        plz_m = re.match(r"(\d{5})\s+(.+)$", loc_text)

        out.append({
            "id": f"kleinanzeigen:{adid}",
            "url": href,
            "title": (title_el.get_text(" ", strip=True) if title_el else None),
            "price_eur": (int(float(price_m.group(1).replace(".", ""))) if price_m else None),
            "size_m2": (float(size_m.group(1).replace(",", ".")) if size_m else None),
            "rooms": (rooms_m.group(1) if rooms_m else None),
            "online_viewing": "Online-Besichtigung" in tags_text,
            "plz": (plz_m.group(1) if plz_m else None),
            "district": (plz_m.group(2) if plz_m else None),
            "teaser": (teaser_el.get_text(" ", strip=True) if teaser_el else None),
            "seller_name": (store_el.get_text(" ", strip=True) if store_el else None),
        })
    return out


def parse_listing_page_ka(html: str) -> dict:
    """Parse one Kleinanzeigen detail page into a flat dict."""
    soup = _ka_soup(html)
    out: dict = {}

    title_el = soup.select_one("h1#viewad-title")
    out["title"] = title_el.get_text(" ", strip=True) if title_el else None

    price_el = soup.select_one("h2#viewad-price")
    if price_el:
        price_text = price_el.get_text(" ", strip=True)
        m = re.search(r"(\d+(?:\.\d+)?)\s*€", price_text)
        out["price_eur"] = int(float(m.group(1).replace(".", ""))) if m else None
        out["price_is_negotiable"] = "VB" in price_text

    loc_el = soup.select_one("#viewad-locality")  # matches twice; first is the header
    if loc_el:
        loc_text = loc_el.get_text(" ", strip=True).replace("\u200b", "")
        out["locality"] = loc_text
        m = re.match(r"(\d{5})\s+([^-]+?)\s*-\s*(.+)$", loc_text)
        if m:
            out["plz"], out["city"], out["district"] = m.group(1), m.group(2).strip(), m.group(3).strip()

    desc_el = soup.select_one("#viewad-description-text")
    out["description"] = desc_el.get_text("\n", strip=True) if desc_el else None

    extra = soup.select_one("#viewad-extra-info")
    if extra:
        date_span = extra.find("span")
        out["posting_date"] = date_span.get_text(" ", strip=True) if date_span else None

    # Coordinates from Open Graph meta — no geocoding needed.
    lat_meta = soup.find("meta", attrs={"property": "og:latitude"})
    lng_meta = soup.find("meta", attrs={"property": "og:longitude"})
    out["lat"] = float(lat_meta["content"]) if lat_meta else None
    out["lng"] = float(lng_meta["content"]) if lng_meta else None

    # Attribute table: <li class="addetailslist--detail">Wohnfläche<span class="addetailslist--detail--value">108 m²</span></li>
    attrs_map: dict[str, str] = {}
    for li in soup.select("li.addetailslist--detail"):
        val_el = li.select_one(".addetailslist--detail--value")
        if not val_el:
            continue
        full = li.get_text(" ", strip=True)
        val = val_el.get_text(" ", strip=True)
        label = full[: -len(val)].strip() if full.endswith(val) else full.split(val)[0].strip()
        attrs_map[label] = val
    out["attrs"] = attrs_map

    # Equipment / checktag list (e.g. "Möbliert", "WLAN", "Möbliert/Teilmöbliert").
    out["checktags"] = [li.get_text(" ", strip=True) for li in soup.select("li.checktag")]
    out["furnished"] = any(re.match(r"^Möbliert(/Teilmöbliert)?$", t) for t in out["checktags"]) or None

    # Photos: parse JSON-LD ImageObject blocks (cleaner than DOM walk; deduped contentUrls).
    photo_urls: list[str] = []
    seen: set[str] = set()
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "ImageObject":
            url = data.get("contentUrl")
            if url and url not in seen:
                seen.add(url)
                photo_urls.append(url)
    out["photo_urls"] = photo_urls[:12]
    cover_meta = soup.find("meta", attrs={"property": "og:image"})
    out["cover_photo_url"] = cover_meta["content"] if cover_meta else (photo_urls[0] if photo_urls else None)

    return out


async def main() -> None:
    async with httpx.AsyncClient(
        headers=KA_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=10.0),
    ) as client:
        # 1. Warm-up (defensive; not strictly required as of 2026-04-18).
        await client.get(f"{KA_BASE_URL}/")
        await asyncio.sleep(KA_PAGE_DELAY_SECONDS)

        # 2. Search page 1, WG vertical, Munich.
        search_url = f"{KA_BASE_URL}/s-auf-zeit-wg/muenchen/c199l6411"
        resp = await client.get(search_url)
        resp.raise_for_status()
        cards = parse_search_page_ka(resp.text)
        print(f"parsed {len(cards)} cards from {search_url}")
        for c in cards[:3]:
            print(f"  {c['id']}: {c['title']!r}  {c['price_eur']} €  {c['size_m2']} m²  {c['plz']} {c['district']}")

        # 3. Detail page for the first card.
        if not cards:
            return
        await asyncio.sleep(KA_PAGE_DELAY_SECONDS + 1.0)
        resp = await client.get(cards[0]["url"])
        resp.raise_for_status()
        detail = parse_listing_page_ka(resp.text)
        print(f"\ndetail: {detail['title']!r}")
        print(f"  price: {detail.get('price_eur')} €  posting_date: {detail.get('posting_date')}")
        print(f"  city/district: {detail.get('city')} / {detail.get('district')}  PLZ: {detail.get('plz')}")
        print(f"  coords: ({detail.get('lat')}, {detail.get('lng')})")
        print(f"  attrs: {detail.get('attrs')}")
        print(f"  checktags: {detail.get('checktags')}  furnished={detail.get('furnished')}")
        print(f"  photos: {len(detail.get('photo_urls', []))}; cover: {detail.get('cover_photo_url')}")


if __name__ == "__main__":
    asyncio.run(main())
```
