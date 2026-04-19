# Scrapers

The scraper container ([`backend/app/scraper/`](../backend/app/scraper/)) is the **sole writer** of `ListingRow` and `PhotoRow`. It drives a registry of `Source` plugins ([`backend/app/scraper/sources/`](../backend/app/scraper/sources/)) — wg-gesucht (default), TUM Living, Kleinanzeigen — selectable via `SCRAPER_ENABLED_SOURCES`. This file is the single source of truth for the cross-source contract and the per-source recon notes.

Background reading: [BACKEND.md](./BACKEND.md) (file-by-file tour + agent loop), [DATA_MODEL.md](./DATA_MODEL.md) (`ListingRow` columns), [DECISIONS.md ADR-018](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only) (why the scraper is a separate process), [DECISIONS.md ADR-020 + ADR-021](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing) (id namespacing + `kind` column).

## Contract

| Prefix          | Site                              | Status        |
| --------------- | --------------------------------- | ------------- |
| `wg-gesucht`    | `https://www.wg-gesucht.de`       | live (WG and flat verticals)                                                  |
| `tum-living`    | `https://living.tum.de`           | live (both verticals) |
| `kleinanzeigen` | `https://www.kleinanzeigen.de`    | live (both verticals) |

### Identifier convention (no double entries)

`ListingRow.id` is a single `str` primary key. To make collisions across sources structurally impossible, every scraper writes a **namespaced** id:

```
ListingRow.id = f"{source}:{external_id}"
```

| Example external id (per source)                  | Persisted `ListingRow.id`                          |
| ------------------------------------------------- | -------------------------------------------------- |
| `12345678` (wg-gesucht numeric ad id)             | `wg-gesucht:12345678`                              |
| `cf76dd26-0bbb-45af-b74d-14f5face8ba0` (TUM UUID) | `tum-living:cf76dd26-0bbb-45af-b74d-14f5face8ba0`  |
| `3362398693` (Kleinanzeigen numeric ad id)        | `kleinanzeigen:3362398693`                         |

Each per-source section below specifies how its `external_id` is extracted (DOM attribute, URL regex, or JSON field).

**Why a string prefix and not a `(source, external_id)` composite key:** zero schema change (the existing `id: str` PK still works), zero migration of the API URLs / SSE payloads / frontend types, and `id.split(":", 1)[0]` recovers the source from any code path. See [ADR-020](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing).

### Dedup is automatic

`repo.upsert_global_listing` ([`../backend/app/wg_agent/repo.py`](../backend/app/wg_agent/repo.py)) does `session.get(ListingRow, listing.id)` first, then either updates the row in place (preserving `first_seen_at`, bumping `last_seen_at` / `scraped_at`) or inserts a new one. Because the id is the dedup key, **two scrape passes that surface the same listing produce one row, not two** — across all sources, automatically. No per-source dedup logic is allowed; everything goes through `upsert_global_listing`.

### Listing kind: WG vs full flat

Each scraped listing declares what it represents — a room in a shared flat (`'wg'`) or an entire apartment (`'flat'`) — so the matcher can honor `SearchProfile.mode` ([`../backend/app/wg_agent/models.py`](../backend/app/wg_agent/models.py), `Literal["wg", "flat", "both"]`). `kind` is a non-null column on `ListingRow` and a matching field on the domain `Listing` model. Each per-source scraper sets `kind` from the search vertical it iterated; the listing-detail page does **not** need to be parsed to determine kind.

| Source           | `kind='wg'` selector                                                            | `kind='flat'` selector                                                  |
| ---------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `wg-gesucht`     | `/wg-zimmer-in-…` URL pattern (category `0`) — wired today                      | `/wohnungen-in-…` URL pattern (category `2`) — wired today                      |
| `tum-living`     | GraphQL `type == "SHARED_APARTMENT"`                                            | GraphQL `type == "APARTMENT"` (treat `HOUSE` as `'flat'` too)           |
| `kleinanzeigen`  | `/s-auf-zeit-wg/…/c199…` URL pattern                                            | `/s-mietwohnung/…/c203…` URL pattern                                    |

Sources that support both verticals iterate them in two passes per cycle (one with `kind='wg'`, one with `kind='flat'`). `SCRAPER_KIND` (env, one of `wg` | `flat` | `both`, default `both`) intersects with each source's `kind_supported` set; pagination is hard-capped at `SCRAPER_MAX_PAGES` (default 6) per `(source, kind)` and stale stubs (`posted_at` older than `SCRAPER_MAX_AGE_DAYS`, default 4 days) are skipped without persisting (ADR-027). The matcher then filters by `SearchProfile.mode` when reading the global pool.

### Per-source scraper contract

Each source's module under [`backend/app/scraper/sources/`](../backend/app/scraper/sources/) must expose, at minimum:

1. An async **search** function that accepts the equivalent of a `SearchProfile` and yields stub `Listing` objects. The stub must carry the **namespaced `id`**, the canonical `url`, and the `kind` it was scraped with. Other fields (`title`, `price_eur`, `address`, …) are best-effort stubs that the detail pass overwrites.
2. An async **detail** function that accepts a stub `Listing` and returns it enriched (description, photos, lat/lng, structured booleans, …). The function must **never re-key** the listing — `id` and `kind` are immutable from the moment the stub is created.
3. A **block-page detector** (`looks_like_block_page(body, status) -> bool`). When a fetch returns an anti-bot interstitial, the detail pass must return the unmodified stub (so the loop persists what it has) rather than crashing.

[`./agent.py`](../backend/app/scraper/agent.py) holds the source-agnostic loop (`run_once` → search → per-stub freshness check → enrich → upsert). It iterates the active source list (built from `SCRAPER_ENABLED_SOURCES`) in sequence per pass and dispatches to the right `search` / `detail` pair based on the source token. See [BACKEND.md "Agent loop"](./BACKEND.md#agent-loop) for the end-to-end sequence diagrams.

### Refresh and pacing

These behaviors apply to **every** source — the per-source sections below only specify the source-specific constants:

- **Refresh:** a listing is re-fetched only if `scrape_status != 'full'` or `scraped_at < now - SCRAPER_REFRESH_HOURS` (`ScraperAgent._needs_scrape`). Tune the refresh window per source — TUM Living tolerates 48h, wg-gesucht and Kleinanzeigen 24h.
- **Freshness stop:** see "Pagination" below — there is no separate "deletion sweep". Listings that disappear from the source eventually fall off the matcher reads through `SCRAPER_REFRESH_HOURS` (stale rows simply stop being re-scraped and the matcher keeps showing the last known data). [ADR-026](./DECISIONS.md#adr-026-drop-the-deletion-sweep-stop-pagination-on-the-first-stale-stub) explains why the older sweep was removed.
- **Pacing:** each source declares its own request-pacing constant (see "Anti-bot posture" in each per-source section). The cross-source loop does not interleave sources within one pass — it iterates them sequentially so per-source pacing is local.

---

## Source: wg-gesucht

> Anonymous httpx + BeautifulSoup scrape of `wg-gesucht.de`. The default source.

### At a glance (wg-gesucht)

- **Site:** `https://www.wg-gesucht.de` (`BASE_URL` in [`../backend/app/wg_agent/browser.py`](../backend/app/wg_agent/browser.py)).
- **Transport:** anonymous `httpx.AsyncClient` + `BeautifulSoup`. **No Playwright at runtime.** Playwright code (`WGBrowser`, `launch_browser`, `ensure_logged_in`, `send_message`, `fetch_inbox`) lives in the same module but is dead code in v1 — the scraper loop never instantiates it.
- **Code:**
  - Search + parse + detail fetch: [`../backend/app/wg_agent/browser.py`](../backend/app/wg_agent/browser.py) (`build_search_url`, `parse_search_page`, `parse_listing_page`, `_parse_map_lat_lng`, `_anon_client`, `anonymous_search`, `anonymous_scrape_listing`).
  - Source plugin shim: [`../backend/app/scraper/sources/wg_gesucht.py`](../backend/app/scraper/sources/wg_gesucht.py).
  - Loop and dedup: [`../backend/app/scraper/agent.py`](../backend/app/scraper/agent.py).
- **Anti-bot:** real Chrome User-Agent + `Accept-Language: de-DE,de;q=0.9,en;q=0.8` (see `_anon_client`); captcha/Turnstile interstitials detected by `_looks_like_block_page` and returned as the unmodified stub instead of crashing; rate-limit constant `ANONYMOUS_PAGE_DELAY_SECONDS = 1.5` between search-page fetches.
- **Data freshness:** `SCRAPER_INTERVAL_SECONDS` (default 300s, between full passes) and `SCRAPER_REFRESH_HOURS` (default 24h, re-scrape threshold for full listings).

### URL schema (wg-gesucht)

Everything on the site is built around **stable, predictable URLs**. We do not need an API.

**Search:** `/wg-zimmer-in-<City>.<cityId>.<categoryId>.<rentType>.<page>.html`

- `<City>` is the URL-slugified city name (`Muenchen`, `Berlin`, `Hamburg`, `Muenster` …). Umlauts become `ae/oe/ue`.
- `<cityId>` is the integer city id. Confirmed: München=`90`, Berlin=`8`, Hamburg=`55`, Frankfurt=`41`. You can discover more by visiting `https://www.wg-gesucht.de/wg-zimmer.html` and watching the city autocomplete API (`/ajax/staedte.php?query=...`).
- `<categoryId>` — `0` = WG room (slug `wg-zimmer-in-`), `1` = 1-room flat (slug `1-zimmer-wohnungen-in-`), `2` = whole flat (slug `wohnungen-in-`), `3` = house (slug `haeuser-in-`). Verified on 2026-04-19 by reading the homepage type-selector `<select>` options. The scraper wires `wg` → `0` and `flat` → `2`; the per-listing detail-page DOM is identical across categories, so `parse_listing_page` doesn't dispatch on kind. Source code: `_CATEGORY_SLUG` table + `build_search_url(category_id=…)` in [`browser.py`](../backend/app/wg_agent/browser.py); `_KIND_TO_CATEGORY_ID` in [`wg_gesucht.py`](../backend/app/scraper/sources/wg_gesucht.py).
- `<rentType>` — **`1`** (unlimited), `2` (temporary), `3` (overnight). We default to `1` (`unbefristet`).
- `<page>` — 0-indexed pagination (so page `0` = first page).

Example (München, WG-room, unbefristet, page 0): `https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.90.0.1.0.html`

**Filters are query-string** appended after the URL:

| Param         | Meaning                                                                                  | Example            |
| ------------- | ---------------------------------------------------------------------------------------- | ------------------ |
| `rMax`        | Maximum total rent in €.                                                                 | `rMax=700`         |
| `rMin`        | Minimum total rent in €.                                                                 | `rMin=300`         |
| `sMin`        | Minimum room size in m².                                                                 | `sMin=12`          |
| `sMax`        | Maximum room size in m².                                                                 | `sMax=40`          |
| `wgSea`       | WG size (flatmates). `2` = "2er WG" and up, up to `7`.                                   | `wgSea=2`          |
| `furnishedSea`| `1` = furnished, `2` = unfurnished.                                                      | `furnishedSea=1`   |
| `dFr`         | Available from (`DD.MM.YYYY`).                                                           | `dFr=01.05.2026`   |
| `dTo`         | Available until (if temporary).                                                          | `dTo=30.09.2026`   |
| `sort_column` | Sort field. `0` = "Online seit" (date posted). Verified.                                 | `sort_column=0`    |
| `sort_order`  | Sort direction. `0` = newest first. Verified.                                            | `sort_order=0`     |

`build_search_url` always sends `sort_column=0&sort_order=0` so the agent's per-stub freshness stop ([ADR-026](./DECISIONS.md#adr-026-drop-the-deletion-sweep-stop-pagination-on-the-first-stale-stub)) sees results in chronological order — the moment a stub's `posted_at` falls outside `SCRAPER_MAX_AGE_DAYS`, the rest of the (source, kind) walk halts.

**Gotchas (confirmed 2026-04):**

- `offer_filter=1` (the param the browser UI appears to use when the "apply filters" button is clicked) triggers a **301 redirect to a malformed URL** (the category segment gets dropped: `/…-Muenchen.90.0.1.0.html` → `/…-Muenchen.90..1.0.html`) which then 404s. **Never send it.** `build_search_url` deliberately omits it.
- `city_id` also appears to cause the same bad redirect in some combinations. Skip it.

Because of these quirks, the robust strategy is: pass a **small, safe filter set** in the URL, and then apply the fine-grained match scoring server-side in the scorecard [`evaluator`](../backend/app/wg_agent/evaluator.py).

**Canonical listing URL:** every listing has a short canonical URL `https://www.wg-gesucht.de/<listingId>.html` (long form: `https://www.wg-gesucht.de/wg-zimmer-in-<City>-<Bezirk>.<listingId>.html`).

### Identifier mapping (wg-gesucht)

- **External id format:** digit string, 5–9 digits (e.g. `12345678`).
- **Extraction sites:**
  - `_LISTING_ID_RE = re.compile(r"[./](\d{5,9})\.html")` — runs against every `<a href>` on the search-result card.
  - `data-id` attribute on `div.wgg_card.offer_list_item` — preferred when present.
- **`ListingRow.id`:** `f"wg-gesucht:{external_id}"`.

### Search-result DOM (wg-gesucht, confirmed 2026-04)

Listings render inside a JSON-hydrated React app now, but a server-rendered HTML list still exists for SEO. Relevant selectors:

- Each listing card: `div.wgg_card.offer_list_item` (and `article.offer_list_item` as a fallback).
- Card id: `div.wgg_card[data-id="13115694"]` (so we can dedupe).
- Title anchor: `h3 a` — gives both the title text and the long URL.
- Price + size: `div.row.middle .col-xs-3 b` (first is `"995 €"`, second is `"14 m²"`).
- Address: the card's second line `div.col-sm-6` has `"<N>er WG | München Ramersdorf-Perlach | Fritz-Erler-Straße 32"`.
- Availability: `div.row .text-right` contains `"Verfügbar: 01.05.2026"`.
- Short link: bottom `a[href^="https://www.wg-gesucht.de/"]` with the pattern `/<id>.html`.

Because the DOM changes, **we parse defensively with BeautifulSoup**: find every anchor that matches `r"/(\d{5,8})\.html"` and walk up to the nearest `wgg_card` or `article`, then extract numbers via regex.

### Listing page (wg-gesucht)

Canonical URL `https://www.wg-gesucht.de/<id>.html` renders:

- `<h1>` — listing title.
- Address block (`Adresse`): street + postal code + Bezirk.
- Cost table (`## Kosten`): `Miete`, `Nebenkosten`, `Sonstige Kosten`, `Kaution`, `Ablösevereinbarung`.
- Availability table (`## Verfügbarkeit`): `frei ab`, `frei bis`.
- Long description `<div id="ad_description_text">` wrapping ordered freitext tabs (`#freitext_0..3` = Zimmer / Lage / WG-Leben / Sonstiges); bilingual in Munich.
- WG-Details: flatmates, ages, smoking, pets, spoken languages.
- **Contact button** (only when logged-in): green "Nachricht senden" button → links to `/nachricht-senden/<listingId>,<offerType>,<deactivated>.html`.

We scrape the listing page once per new listing to get the real description (the card text is truncated).

**Stable DOM anchors** — [`parse_listing_page`](../backend/app/wg_agent/browser.py) prefers these anchors over `get_text` regex:

| Field(s) | Anchor |
| --- | --- |
| `price_eur`, `Nebenkosten`, `Kaution`, etc. | `<h2>Kosten</h2>`, then rows of `span.section_panel_detail` + sibling `span.section_panel_value` inside `div.row` until the next `<h2>`. |
| `available_from`, `available_to` | `<h2>Verfügbarkeit</h2>`, same label/value row shape. |
| `address`, `postal_code`, `city`, `district` | `<h2>Adresse</h2>` → its `col-sm-6` wrapper → first `.section_panel_detail` (two lines: `"Straße Nr"` then `"<PLZ> <City> <District>"`). |
| `languages`, `pets_allowed`, `smoking_ok` | `<h2>WG-Details</h2>` → `panel.panel` → `li` rows, one signal per line. |
| `furnished` | Same WG-Details `<li>`s, plus `div.utility_icons > div.text-center` quick-facts tiles. Negations (`nicht`, `un-`, `teilweise`) are colocated on short lines, so a same-line check is reliable. |
| `lat`, `lng` | The map snippet at the bottom of the page ships `var map_config = { ... markers: [{"lat":48.09,"lng":11.64,...}] }`. A tight regex in `browser._parse_map_lat_lng` reads the first marker; no external API call. |

Every DOM path degrades to the pre-existing full-text regex if an anchor goes missing so the parser never returns `None` for a field the page actually has.

### Per-listing data we extract (wg-gesucht)

Source code: `parse_listing_page` and `_parse_map_lat_lng` in [`../backend/app/wg_agent/browser.py`](../backend/app/wg_agent/browser.py). Search-card stub fields are filled by `parse_search_page` first; the detail pass overwrites where the page provides better data.

| `Listing` field | Stub source (search card) | Detail source (listing page) |
| --- | --- | --- |
| `id` | `data-id` on `.wgg_card`, fallback `_LISTING_ID_RE` on card anchors | — (carried from stub) |
| `url` | first matching `/<id>.html` anchor in the card | — |
| `title` | `h3 a` text on the card | `<h1>` |
| `price_eur` | regex `(\d+) €` on card text | `Kosten` panel → `Miete` row (`_section_pairs`); regex fallback on full text |
| `size_m2` | regex `(\d+) m²` on card text | regex `Zimmergröße: (\d+)` on full text |
| `wg_size` | regex `(\d+)er WG` on card text | same regex on full text as fallback |
| `city` | parsed from card address line `<...>er WG \| <City> <District> \| <Street>` | `Adresse` panel → second line `<PLZ> <City> <District>` (`_parse_address_panel`) |
| `district` | same address-line split | same `Adresse` panel parse |
| `address` | tail of the card address line | `Adresse` panel first line (street + number) |
| `available_from` | regex `Verfügbar: dd.mm.yyyy` on card text | `Verfügbarkeit` panel → `frei ab`; regex fallback |
| `available_to` | — | `Verfügbarkeit` panel → `frei bis`; regex fallback |
| `description` | — | `#ad_description_text` is the wrapper that contains all 1–4 `#freitext_N` children (Zimmer / Lage / WG-Leben / Sonstiges) plus their `<h3>` headings. The parser walks the wrapper with `get_text("\n", strip=True)`, capturing every section in document order; scrubs `<script>`, `<iframe>`, `[id^="div-gpt-ad-"]`. Fallback to standalone `#freitext_*` selectors only fires if `#ad_description_text` is missing entirely. Never falls back to whole-page text. |
| `languages` | — | WG-Details `<li>` matching `Sprache(n): …`; regex fallback on full text |
| `furnished` | — | WG-Details `<li>` or `div.utility_icons > div.text-center` quick-fact tile matching `möbliert` with no same-line negation (`nicht`/`un-`/`teilweise`) |
| `pets_allowed` | — | WG-Details `<li>` `Haustiere vorhanden: Ja|Nein`; full-text regex fallback |
| `smoking_ok` | — | WG-Details `<li>` `Rauchen (nicht) erwünscht`; full-text regex fallback |
| `lat`, `lng` | — | `_parse_map_lat_lng` reads the first marker out of the embedded `var map_config = { ... markers: [{"lat":…,"lng":…}] }` block. Falls back to `geocoder.geocode(address or "<district>, <city>")` only when the map block is absent or unparseable. |
| `online_viewing` | substring `"Online-Besichtigung"` in card text | — |
| `photo_urls`, `cover_photo_url` | — | `_parse_photo_urls` walks `og:image`, `[data-full-image]`, `img[data-src/data-lazy/src]`, `source[srcset]`; filters out logos/avatars/icons/placeholder gallery elements; capped at 12. `cover_photo_url = photo_urls[0]`. |
| `posted_at` (transient — used by `ScraperAgent._is_stale`) | `parse_search_page` finds the first `<span>` text matching `^\s*Online\s*:` inside the card, strips the `Online:` prefix, then `_parse_wgg_online_value` returns either `now - <n*unit>` for the relative form (`Online: 25 Minuten`, `Online: 1 Stunde`, `Online: 2 Tage`) or `datetime(y, m, d)` for the absolute form (`Online: 12.03.2026`). The regex is **anchored at the start of the string** so it doesn't catch `Online-Besichtigung` (the unrelated online-viewing flag). The relative form fires for ads <24h old; the absolute form (`dd.mm.yyyy`) takes over from ≥24h. With `sort_column=0&sort_order=0` (set unconditionally by `build_search_url`), results are newest-first; stale stubs cluster at the tail and the agent drops them without persisting (skip-and-continue, ADR-027) until either the page cap (`SCRAPER_MAX_PAGES`, default 6) is reached or pagination ends naturally. | `<b>` next to `<span class="section_panel_detail">Online:</span>` inside a `div.row` — currently unused by the parser since the search-card value is always populated. |

Fields on the `Listing` domain model that this source **never** populates (filled later by the matcher per user, not by the scraper): `score`, `score_reason`, `match_reasons`, `mismatch_reasons`, `components`, `veto_reason`, `best_commute_minutes`.

### Anti-bot posture (wg-gesucht)

- **Headers:** `_anon_client` sets `User-Agent` to a real Chrome string (`USER_AGENT` constant, Chrome 124 / macOS) and `Accept-Language: de-DE,de;q=0.9,en;q=0.8`. `follow_redirects=True`. No cookie jar persisted across passes.
- **Block detection:** `_looks_like_block_page(soup, full_text)` returns `True` when the page has no `#ad_description_text`, no `Kosten/Verfügbarkeit/Adresse` `<h2>`, no contact link, AND either ships a `data-sitekey` element, a `turnstile|captcha`-named iframe/script, or matches a German/English captcha-language regex (`captcha`, `turnstile`, `verify you are human`, `Sicherheitsüberprüfung`, `ungewöhnlichen Datenverkehr`, `automated requests`, `robot`, …). When `True`, `parse_listing_page` returns the stub unchanged so the loop persists what it has rather than crashing.
- **Pacing:** `ANONYMOUS_PAGE_DELAY_SECONDS = 1.5` between consecutive search-result page fetches inside one pass (`anonymous_search`). Detail fetches inside one pass run back-to-back; pacing between passes is `SCRAPER_INTERVAL_SECONDS` (default 300s).
- **Refresh:** `ScraperAgent._needs_scrape` skips re-scraping a listing whose `scrape_status == "full"` and whose `scraped_at` is newer than `now - SCRAPER_REFRESH_HOURS`. Stubs (`status != "full"`) and rows with `scraped_at is None` are always re-scraped.

### Future: authenticated wg-gesucht flows (dead code in v1)

The Playwright code paths in [`../backend/app/wg_agent/browser.py`](../backend/app/wg_agent/browser.py) (`WGBrowser`, `launch_browser`, `ensure_logged_in`, `send_message`, `fetch_inbox`) cover login, messaging, and inbox polling. They are **not wired** into the scraper loop or the matcher in v1 — kept for a future iteration that would add landlord messaging.

- **Login URL:** `https://www.wg-gesucht.de/mein-wg-gesucht.html`. Login modal opens via `#login-link` or `/login.html`. Form fields: `input[name="login_email_username"]`, `input[name="login_password"]`, submit `button[name="login_submit"]`. Sets cookies: `X-Client-Id`, `user_id`, `wgg_sid`, plus several CSRF tokens. After ~3 failed logins the site shows a CAPTCHA. Strongly prefer logging in **once manually** and reusing `storage_state.json` (env var `WG_STATE_FILE`).
- **Sending a message:** "Nachricht senden" button on the listing page opens `/nachricht-senden/<listingId>,<offerType>,<deactivated>.html`. Form fields: `textarea[name="message"]`, `input[name="user_salutation"]`, `input[name="anrede"]` (`0` female, `1` male, `2` divers), `input[name="verfuegbar_ab"]` / `input[name="verfuegbar_bis"]` (dd.mm.yyyy), `input[type="submit"][name="send_message_offer"]`. On success: redirect to `/nachrichten-senden.html?...` with `class="alert alert-success"`. wg-gesucht **rate-limits messages**: ~3–5/day on free accounts before a soft block ("zu viele Nachrichten").
- **Reading replies:** `https://www.wg-gesucht.de/nachrichten-lesen.html` (requires login). Each thread is `a.mailbox_thread_unread` or `a.mailbox_thread_read` with a `data-conversation-id`. Conversation page: `/nachrichten-lesen.html?conv_id=<convId>`. Messages: `<div class="message-container">` with `.message-sender-name`, `.message-body`, `.message-time`. Polling strategy: every 45s GET the inbox, diff with last-seen conversation ids + message timestamps.
- **Anti-bot for the authenticated path:** wg-gesucht runs Cloudflare Turnstile on some endpoints. Launch with `headless=False` for demos; `headless=True` + `playwright-stealth` works for most search pages but sometimes fails on login. Always land on the homepage once per session before navigating to a listing — going straight to `/nachricht-senden/...` from a cold session triggers a CAPTCHA more often. Once logged in, dump `context.storage_state()` and reuse it for all subsequent runs.

**Legal / ethical guardrails (when this lands):** wg-gesucht's ToS forbid automated scraping, so this agent is strictly a **hackathon demo**. Default to ≤1 message/30s pacing, hard cap of 5 messages per run (`WG_MAX_MESSAGES`), never auto-accept legally binding rental offers, and surface every action to the operator via the action log (SSE).

---

## Source: tum-living

> GraphQL API scrape of TUM Living housing platform. Serves **both** WG rooms and full apartments for TUM students, staff, and researchers.

### At a glance (tum-living)

- **Site:** `https://living.tum.de/listings?viewMode=list`
- **Transport:** `httpx + JSON` (Apollo-server GraphQL endpoint at `https://living.tum.de/graphql`). **No Playwright needed.**
- **Anonymous-accessible?** Yes, with a one-shot **CSRF mint**: `GET /api/me` returns `{user: null, csrf: "<token>"}` and sets a `csrf-token` cookie. Reuse both (cookie kept by the client jar, token sent back as the `csrf-token` request header) on every GraphQL POST. Without the pair the server replies `{"errors":[{"message":"invalid csrf token","code":"EBADCSRFTOKEN"}],"data":null}`. No login needed for any read query.
- **Listing kinds offered:** **both** — `type=APARTMENT` (whole flat), `type=HOUSE` (rare, treat as flat), and `type=SHARED_APARTMENT` (room in a shared flat). `ListingType` is the WG-vs-flat discriminator. (Don't confuse with the unrelated `housingType` enum, which describes the building/floor: `APARTMENT | ATTIC | BASEMENT | GROUND_FLOOR | MEZZANINE`.)
- **Suggested cadence:** one search every 15 min (conservative, the corpus is small — 167 active listings on 2026-04-18); refresh detail after 48h (listings turn over much slower than wg-gesucht).
- **Code:** [`../backend/app/scraper/sources/tum_living.py`](../backend/app/scraper/sources/tum_living.py).

### Recon summary (tum-living, date: 2026-04-18)

- **Verified via:** real Chrome capture in the cursor-ide-browser MCP (`browser_network_requests`) on `https://living.tum.de/listings?viewMode=list`, plus extraction of the verbatim GraphQL query strings from the React bundle at `https://living.tum.de/static/js/main.affa1ba3.js`, plus anonymous `curl` POSTs to `https://living.tum.de/graphql` whose JSON responses confirmed every field name in the mapping table below. Sample listing `id=691` (uuid `cf76dd26-0bbb-45af-b74d-14f5face8ba0`) and one `SHARED_APARTMENT` (`3ec9cfbb-…`) were used as fixtures.
- **Page structure:** The listings page is a Create-React-App SPA (single bundle `main.affa1ba3.js`) that hydrates from a **GraphQL API** at `https://living.tum.de/graphql`. The initial HTML is a 3.3 KB shell.
- **Network on first paint** (verified): two GraphQL POSTs (`GetListings` and `GetNumberOfListings`), one REST GET (`/api/tags`), then one `/api/image/thumbnail/<id>?ts=<modifiedAt-millis>` GET per visible listing card.
- **Listing detail pages:** UUID-based URLs like `https://living.tum.de/listings/cf76dd26-0bbb-45af-b74d-14f5face8ba0/view`. The detail page POSTs `GetListingByUUIDWithoutContactInfo` (anonymous) or `GetListingByUUID` (logged-in with contact info) to `/graphql`.
- **Anonymous access:** Confirmed end-to-end. `/api/me` returns `404` with body `{"user": null, "csrf": "<token>"}` and sets a `csrf-token` cookie — that pair unlocks every read query. The `404` status is misleading: the body is the intended response.
- **Anonymous landlord visibility (counter-intuitive):** `GetListings` exposes `landlord.email` and `landlord.phone` to anonymous callers (verified in the raw response). `GetListingByUUID*` returns `landlord: null` for anonymous callers — even when `GetListingByUUID` (the with-contact variant) is used, the server silently nulls the landlord rather than erroring. Net: anonymous **list** has contacts, anonymous **detail** does not.
- **Anti-bot:** `robots.txt` allows everything (`User-agent: *, Disallow:`). No captcha, WAF, or Cloudflare interstitial observed. Server is `nginx/1.26.3` fronting `Express` (`X-Powered-By: Express`).
- **RSS feed:** **Does not exist as XML.** `GET /rss`, `/feed`, `/listings.rss` all return the SPA shell (3313 bytes, `Content-Type: text/html`); `/api/rss`, `/api/feed` return 404. Treat the footer link as decorative; use `GetListings(orderBy: MOST_RECENT)` for change detection instead.
- **Photos:** Two URL helpers exist in the bundle:
  - `https://living.tum.de/api/image/thumbnail/<image_id>?ts=<modifiedAt-millis>` — small thumbnail used in the list view (e.g. ~40 KB). Verified by the network log.
  - `https://living.tum.de/api/image/<image_id>[/<size>]` — generic helper with optional resize. Verified sizes: `320` (~13 KB), `640` (~39 KB), `1280` (~120 KB), `2048` (~246 KB, may upscale). Omitting `<size>` (or passing `full` / `thumbnail`) returns the original (~200 KB for a 1920×1280 JPEG). All image URLs are hot-linkable without auth (no cookies, no CSRF).
- **Coordinates:** Confirmed in both list and detail responses as `coordinates { x, y }` where **`x` is latitude, `y` is longitude** (sample: `{x: 48.1184617, y: 11.5707928}` for a Munich listing). Not nested under address; coordinates is a sibling field.

### Identifier mapping (tum-living)

- **External id format:** UUID (e.g. `cf76dd26-0bbb-45af-b74d-14f5face8ba0`). Appears as `uuid` on every listing object **and** as the URL path segment in `https://living.tum.de/listings/<uuid>/view`. There is also a separate numeric `id` field (e.g. `"691"`) used as the database primary key, but the **UUID is the public identifier** — every URL and every detail-query input uses the UUID, not the numeric `id`.
- **Mapping to `ListingRow.id`:** `f"tum-living:{uuid}"` (e.g. `"tum-living:cf76dd26-0bbb-45af-b74d-14f5face8ba0"`).
- **Extraction strategy:** read `listing.uuid` straight off the GraphQL response.

### How to list listings (tum-living search)

- **HTTP request:** `POST https://living.tum.de/graphql` with headers:
  ```
  Content-Type: application/json
  csrf-token: <token-from-/api/me>
  Cookie: csrf-token=<cookie-from-/api/me>
  User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
  ```
  The `csrf-token` cookie value and the `csrf-token` header value are **different strings** (standard double-submit pattern: cookie carries the secret, header carries the public token). Both are minted in the same `GET /api/me` round-trip.
- **Request body (verbatim from `main.affa1ba3.js`):**
  ```graphql
  query GetListings($resultLimit: Int, $pageOffset: Int, $orderBy: ListingSortOrder, $filter: ListingFilter) {
    listings(resultLimit: $resultLimit, pageOffset: $pageOffset, orderBy: $orderBy, filter: $filter) {
      id
      uuid
      landlord { companyName firstName lastName email phone }
      type
      numberOfRooms
      availableFrom
      availableUntil
      city
      tumLocation
      district
      street
      houseNumber
      postalCode
      totalRent
      deposit
      squareMeter
      tags
      images { id modifiedAt description descriptionEn }
      seekingStudents
      seekingProfessors
      seekingIncomings
      seekingDoctoralStudents
      seekingPostDoctoralStudents
      seekingGuestResearchers
      seekingTumEmployees
      isListingPublic
      publicationDate
      verifiedAt
      createdAt
      isActive
      coordinates { x y }
      previewImage { id description descriptionEn }
    }
  }
  ```
  Variables (defaults match the live frontend at `resultLimit: 25`):
  ```json
  {
    "resultLimit": 25,
    "pageOffset": 0,
    "orderBy": "MOST_RECENT",
    "filter": {}
  }
  ```
- **Response shape:** `{"data": {"listings": [ {…}, … ]}}`. **Flat array**, no `total` / `items` / `pageInfo` wrapper. Use the sibling `GetNumberOfListings` query (below) when you need a count.
- **Total-count query** (verified):
  ```graphql
  query GetNumberOfListings($filter: ListingFilter) {
    numberOfListings(filter: $filter)
  }
  ```
  Response: `{"data": {"numberOfListings": 167}}`. Issue alongside `GetListings` (the frontend does so on every page load).
- **Pagination:** integer-offset. The frontend defaults to `resultLimit: 25` and exposes 10/25/50 in the per-page selector. Set `pageOffset = page_index * resultLimit` (page-index is 0-based — i.e. `pageOffset` is the absolute item offset, not the page number).
- **Sort:** `orderBy` is a single `ListingSortOrder` **string enum**, not a `{field, direction}` object. Verified members: `LAST_MODIFIED | MOST_RECENT | RENT_ASCENDING | RENT_DESCENDING`.
- **WG-vs-flat filter:** the discriminator is `type` (`ListingType` enum). Verified members: `APARTMENT | HOUSE | SHARED_APARTMENT`. Pass `filter: {"type": "SHARED_APARTMENT"}` for WG rooms or `filter: {"type": "APARTMENT"}` for full flats. Omitting `type` returns both. Note the field name in the filter is `type` (singular); attempting `types` triggers a server error (`Field "types" is not defined by type "ListingFilter". Did you mean "type" or "tags"?`).

### How to read one listing (tum-living detail)

- **HTTP request:** `POST https://living.tum.de/graphql` (same endpoint, same CSRF cookie+header pair).
- **Request body (verbatim, anonymous variant):**
  ```graphql
  fragment listingByUUID on Listing {
    id
    uuid
    type
    housingType
    floor
    numberOfRooms
    availableFrom
    availableUntil
    city
    tumLocation
    district
    street
    houseNumber
    postalCode
    rent
    incidentalCosts
    incidentalCostsCustomLabel
    incidentalCostsTypes
    oneTimeCosts
    oneTimeCostsLabel
    parkingSpace
    parkingSpaceCosts
    deposit
    squareMeter
    tags
    images { id listing fileName description descriptionEn isPreview modifiedAt }
    seekingStudents
    seekingProfessors
    seekingIncomings
    seekingDoctoralStudents
    seekingPostDoctoralStudents
    seekingGuestResearchers
    seekingTumEmployees
    furtherEquipment
    furtherEquipmentEn
    isActive
    isListingPublic
    publicationDate
    expirationDate
    verifiedAt
    createdAt
    modifiedAt
    coordinates { x y }
    contactName
    contactPhone
    contactEmail
  }

  query GetListingByUUIDWithoutContactInfo($uuid: String!) {
    listingByUUID(uuid: $uuid) {
      landlord { id uuid contactType }
      ...listingByUUID
    }
  }
  ```
  Variables: `{"uuid": "cf76dd26-0bbb-45af-b74d-14f5face8ba0"}`. The fragment can also include `totalRent` (verified extra) — the server returns the warm-rent sum (`rent + incidentalCosts`).
- **Why `WithoutContactInfo`:** the alternate `GetListingByUUID` query also accepts a `landlordContactInfo` fragment, but the server returns `landlord: null` for anonymous callers either way. The `WithoutContactInfo` variant is the one we use because it omits the unused fragment and avoids a non-result.
- **Response shape:** `{"data": {"listingByUUID": { … }}}`. The free-text "description" is stored as **`furtherEquipment`** (German) and **`furtherEquipmentEn`** (English). There is no top-level `description` field — the only `description` / `descriptionEn` fields are per-image captions.

### Field mapping (tum-living)

Every row verified by inspecting the live response for `id=691` / uuid `cf76dd26-…`:

| `Listing` field       | Source on TUM Living GraphQL response                                                                                       |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `id`                  | `f"tum-living:{listingByUUID.uuid}"`                                                                                        |
| `url`                 | `f"https://living.tum.de/listings/{listingByUUID.uuid}/view"`                                                               |
| `title`               | **not exposed by API** — TUM Living has no listing title field. Synthesise (e.g. `f"{type} · {numberOfRooms}R · {city}"`).  |
| `price_eur`           | `listingByUUID.totalRent` (warm rent = `rent + incidentalCosts`, in €). Add `totalRent` to the fragment to fetch it.        |
| `size_m2`             | `listingByUUID.squareMeter` (number, m²)                                                                                    |
| `wg_size`             | **not exposed by API.** `numberOfRooms` on a `SHARED_APARTMENT` is the rooms-in-the-offered-share, not flatmate count. Leave `None`. |
| `address`             | `listingByUUID.street + " " + listingByUUID.houseNumber` (both top-level; not nested under any `address` object)            |
| `city`                | `listingByUUID.city` (string, e.g. `"München"`, `"Karlsfeld"` — free-text, not the `tumLocation` enum)                      |
| `district`            | `listingByUUID.district` (enum-style, e.g. `"UNTERGIESING_HARLACHING"` or `null`)                                            |
| `lat`                 | `listingByUUID.coordinates.x` (latitude)                                                                                    |
| `lng`                 | `listingByUUID.coordinates.y` (longitude)                                                                                   |
| `available_from`      | `listingByUUID.availableFrom` (ISO 8601 datetime string → `date`)                                                           |
| `available_to`        | `listingByUUID.availableUntil` (ISO 8601 datetime string or `null` for unlimited). Note: the field is `availableUntil`, not `availableTo`. |
| `description`         | `listingByUUID.furtherEquipmentEn` (English) or `listingByUUID.furtherEquipment` (German). Free text, no HTML.              |
| `photo_urls`          | `[f"https://living.tum.de/api/image/{img['id']}/1280" for img in listingByUUID.images]` (cap at 12; sort `isPreview` first) |
| `cover_photo_url`     | `f"https://living.tum.de/api/image/{listingByUUID.images[i]['id']}/1280"` for the image where `isPreview is True`, fallback `images[0]` |
| `furnished`           | `"FURNISHED" in listingByUUID.tags` → `True`; `"PARTLY_FURNISHED" in tags` → `True`; else `None` (don't infer `False` from absence) |
| `pets_allowed`        | `"PETS_ALLOWED" in listingByUUID.tags` → `True`; else `None`                                                                |
| `smoking_ok`          | `"SMOKING" in listingByUUID.tags` → `True`; else `None`                                                                     |
| `languages`           | **not exposed by API**; leave `[]`                                                                                          |
| `online_viewing`      | **not exposed by API**; leave `False`                                                                                       |
| `kind`                | `'wg'` if `listingByUUID.type == "SHARED_APARTMENT"`, else `'flat'` (covers `APARTMENT` and `HOUSE`)                        |

Verified `tags` enum members (a partial list, harvested from the bundle and live samples; treat as denylist-by-absence): `FURNISHED, PARTLY_FURNISHED, BATHTUB, SHOWER, GUEST_TOILET, WASHING_MACHINE, DISHWASHER, TERRACE, BALCONY, GARDEN, CELLAR, LIFT, PETS_ALLOWED, BICYCLE_CELLAR, ATTIC, BARRIER_FREE, FITTED_KITCHEN, FAMILY_FRIENDLY, SMOKING, FLAT_SHARING_POSSIBLE, PARKING_SPACE`.

Additional fields TUM Living **does** provide that wg-gesucht doesn't (verified): `rent` (Kaltmiete), `totalRent` (Warmmiete), `incidentalCosts` + `incidentalCostsTypes` (string enum array, e.g. `["CARETAKER", "HEATING_COSTS", …]`), `incidentalCostsCustomLabel`, `oneTimeCosts` + `oneTimeCostsLabel`, `deposit`, `parkingSpace` + `parkingSpaceCosts`, `floor` (fractional values like `1.5` for mezzanine allowed), `housingType` (building/floor enum: `APARTMENT | ATTIC | BASEMENT | GROUND_FLOOR | MEZZANINE` — distinct from the WG-vs-flat `type`), `tumLocation` (city enum: `MUNICH | GARCHING | FREISING | HEILBRONN | STRAUBING | GARMISCH_PARTENKIRCHEN`), seven `seekingX` booleans for target groups, plus `verifiedAt`, `publicationDate`, `expirationDate`, `createdAt`, `modifiedAt`, `isActive`, `isListingPublic`. These can be persisted in a future `ListingRow.extras` JSON column or ignored if the evaluator doesn't need them.

### Anti-bot posture (tum-living)

- **Cookies / CSRF / auth required?** The CSRF double-submit pair is required (cookie `csrf-token=<secret>` set by `GET /api/me`, plus the header `csrf-token: <token-from-/api/me-body>` on every POST). Without the pair the server returns `{"errors":[{"message":"invalid csrf token","code":"EBADCSRFTOKEN"}],"data":null}`. No login token is required.
- **Pacing recommendation:** 2–3 seconds between requests. The active corpus is small (~167 listings on 2026-04-18) — at 25/page that's ~7 paginated requests per pass; refresh detail every 48h to keep traffic low.
- **Captcha / WAF observations:** none. `robots.txt` is `User-agent: * / Disallow:` (full allow). Server is `nginx/1.26.3` fronting `Express`; no Cloudflare.
- **Block detection:** `looks_like_block_page` returns `True` on `EBADCSRFTOKEN` (re-mint CSRF and retry once before giving up), on any GraphQL response with `"errors"` populated and `"data": null`, and on HTTP 5xx. The `/api/me` 404-with-body is the *intended* response and is **not** a block page.
- **Headers to send:**
  ```
  User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
  Accept: application/json
  Accept-Language: en-US,en;q=0.9
  Content-Type: application/json
  csrf-token: <token-from-/api/me-body>
  Cookie: csrf-token=<cookie-from-/api/me-Set-Cookie>
  ```

### Verified end-to-end recipe (tum-living)

Anonymous, read-only, no Playwright. Mints a CSRF pair, fetches one page of listings, fetches one listing's detail, and downloads one full-size image. Verified to run as written on 2026-04-18 with `httpx==0.28.1`. The GraphQL queries below are the **source of truth** referenced verbatim by [`../backend/app/scraper/sources/tum_living.py`](../backend/app/scraper/sources/tum_living.py).

```python
import asyncio

import httpx

BASE_URL = "https://living.tum.de"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LISTINGS_QUERY = """\
query GetListings($resultLimit: Int, $pageOffset: Int, $orderBy: ListingSortOrder, $filter: ListingFilter) {
    listings(resultLimit: $resultLimit, pageOffset: $pageOffset, orderBy: $orderBy, filter: $filter) {
      id
      uuid
      type
      numberOfRooms
      availableFrom
      availableUntil
      city
      tumLocation
      district
      street
      houseNumber
      postalCode
      totalRent
      deposit
      squareMeter
      tags
      images { id modifiedAt description descriptionEn }
      seekingStudents
      seekingProfessors
      seekingIncomings
      seekingDoctoralStudents
      seekingPostDoctoralStudents
      seekingGuestResearchers
      seekingTumEmployees
      isListingPublic
      publicationDate
      verifiedAt
      createdAt
      isActive
      coordinates { x y }
      previewImage { id description descriptionEn }
    }
  }
"""

DETAIL_QUERY = """\
fragment listingByUUID on Listing {
    id
    uuid
    type
    housingType
    floor
    numberOfRooms
    availableFrom
    availableUntil
    city
    tumLocation
    district
    street
    houseNumber
    postalCode
    rent
    totalRent
    incidentalCosts
    incidentalCostsCustomLabel
    incidentalCostsTypes
    oneTimeCosts
    oneTimeCostsLabel
    parkingSpace
    parkingSpaceCosts
    deposit
    squareMeter
    tags
    images { id listing fileName description descriptionEn isPreview modifiedAt }
    seekingStudents
    seekingProfessors
    seekingIncomings
    seekingDoctoralStudents
    seekingPostDoctoralStudents
    seekingGuestResearchers
    seekingTumEmployees
    furtherEquipment
    furtherEquipmentEn
    isActive
    isListingPublic
    publicationDate
    expirationDate
    verifiedAt
    createdAt
    modifiedAt
    coordinates { x y }
    contactName
    contactPhone
    contactEmail
  }

  query GetListingByUUIDWithoutContactInfo($uuid: String!) {
    listingByUUID(uuid: $uuid) {
      landlord { id uuid contactType }
      ...listingByUUID
    }
  }
"""


async def main() -> None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=20.0) as client:
        me_resp = await client.get("/api/me")
        csrf_token = me_resp.json()["csrf"]
        client.headers["csrf-token"] = csrf_token

        listings_resp = await client.post(
            "/graphql",
            json={
                "operationName": "GetListings",
                "variables": {
                    "resultLimit": 5,
                    "pageOffset": 0,
                    "orderBy": "MOST_RECENT",
                    "filter": {},
                },
                "query": LISTINGS_QUERY,
            },
        )
        listings_resp.raise_for_status()
        listings = listings_resp.json()["data"]["listings"]
        print(f"Fetched {len(listings)} listings")

        first_uuid = listings[0]["uuid"]
        detail_resp = await client.post(
            "/graphql",
            json={
                "operationName": "GetListingByUUIDWithoutContactInfo",
                "variables": {"uuid": first_uuid},
                "query": DETAIL_QUERY,
            },
        )
        detail_resp.raise_for_status()
        listing = detail_resp.json()["data"]["listingByUUID"]
        print(
            f"Detail uuid={listing['uuid']} type={listing['type']} "
            f"city={listing['city']} totalRent={listing['totalRent']} "
            f"coords=({listing['coordinates']['x']}, {listing['coordinates']['y']}) "
            f"images={len(listing['images'])}"
        )

        if listing["images"]:
            image_id = listing["images"][0]["id"]
            image_resp = await client.get(f"/api/image/{image_id}/1280")
            image_resp.raise_for_status()
            print(
                f"Image id={image_id} bytes={len(image_resp.content)} "
                f"content-type={image_resp.headers.get('content-type')}"
            )


if __name__ == "__main__":
    asyncio.run(main())
```

Sample output (2026-04-18):

```
Fetched 5 listings
Detail uuid=cf76dd26-0bbb-45af-b74d-14f5face8ba0 type=APARTMENT city=München totalRent=1700 coords=(48.1184617, 11.5707928) images=10
Image id=5400 bytes=120528 content-type=image/jpg
```

---

## Source: kleinanzeigen

> Anonymous httpx + BeautifulSoup scrape of `kleinanzeigen.de` (formerly eBay Kleinanzeigen). Targets **both** WG rooms ("Auf Zeit & WG", `c199`) and full apartments ("Mietwohnungen", `c203`). Anti-bot is **Akamai Bot Manager** (not DataDome as commonly reported).

### At a glance (kleinanzeigen)

- **Site:** `https://www.kleinanzeigen.de`
- **Transport:** `httpx + BeautifulSoup`. A homepage cookie warm-up is **recommended but not required** in today's recon — both warmed and cold requests returned 200 + parsable HTML on every endpoint we tried. Reuse one `AsyncClient` per pass so Akamai sees a stable cookie jar (`bm_sz`, `_abck`, `up`) across pages. If anti-bot escalates, escalate to `curl_cffi` (TLS/HTTP2 fingerprint impersonation) for the warm-up only; Playwright is the last resort.
- **Anonymous-accessible?** **Yes.** Verified: 5 sequential search-page fetches, 3 detail-page fetches, plus filtered/paginated/alt-slug variants — all returned 200 with full HTML, no challenge interstitial, no 403 / 429.
- **Listing kinds offered:** `wg` (Auf Zeit & WG, `c199`) **and** `flat` (Mietwohnungen, `c203`). Both verticals are public, share one listing-card DOM, and share the `/s-anzeige/<slug>/<adid>-<categoryId>-<localityId>` detail-page schema. The flat vertical also accepts the alternate slug `/s-wohnung-mieten/`.
- **Suggested cadence:** one full search pass every **15 min** per vertical (≈2× wg-gesucht's interval); refresh detail pages after **24h**. Hold consecutive search-page fetches at ≥2.5s apart and detail fetches at ≥3.5s apart.
- **Code:** [`../backend/app/scraper/sources/kleinanzeigen.py`](../backend/app/scraper/sources/kleinanzeigen.py).

### Recon summary (kleinanzeigen, date: 2026-04-18)

**Verified via:** anonymous `httpx.AsyncClient` (Chrome UA + `de-DE` Accept-Language + `follow_redirects=True`) and `curl` with shared cookie jar.

What was actually observed:

- **Immobilien category root for Munich:** `https://www.kleinanzeigen.de/s-immobilien/muenchen/c195l6411` — left sidebar lists every housing subcategory with counts. Confirms `c195` is the parent Immobilien category and `l6411` is the Munich locality id used in **search** URLs.
- **WG vertical (Auf Zeit & WG):** `https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/c199l6411` (page title "Auf Zeit & WG in München - Bayern"). Returned 27 `<article class="aditem" data-adid="…">` cards on page 1 in raw HTML.
- **Flat vertical (Mietwohnungen):** direct GET on `https://www.kleinanzeigen.de/s-mietwohnung/muenchen/c203l6411` returned 27 cards, identical card DOM. Page title "Mietwohnung in München - Bayern". The breadcrumb anchor on flat detail pages uses the alternate slug `/s-wohnung-mieten/` — both slugs route to the same vertical.
- **Detail page (sample):** `https://www.kleinanzeigen.de/s-anzeige/moebliert-naehe-prinzregentenplatz-moderne-klare-linien/3362398693-199-6461`. Routing is **adid-only**: requesting the same ad as `/s-anzeige/x/3362398693-199-6411` (placeholder slug, wrong locality id) still returned the canonical detail page; the server rewrote the locality id in the response's `<meta property="og:url">`. **Don't hand-construct the trailing `<cat>-<loc>` triplet — read it from the search-card `data-href`.**
- **GDPR overlay:** appears on the first browser visit but is purely client-side (a JS-rendered modal). The raw HTML behind it is fully populated; httpx never sees it.
- **No Cloudflare / DataDome interstitial observed** during the entire httpx recon. Cookies dropped by the homepage are Akamai bot-manager (`bm_sz`, `_abck`, `kameleoonVisitorCode`, `up`, `lnFeMonitoring`) — no `datadome` cookie at any point. The "DataDome on Kleinanzeigen" reputation in the wider web doesn't reflect what an anonymous httpx client sees today.
- **`robots.txt`:** **Listing paths (`/s-auf-zeit-wg/`, `/s-mietwohnung/`, `/s-wohnung-mieten/`, `/s-anzeige/`, `/s-immobilien/`) are NOT disallowed** for `User-agent: *`. But several patterns we considered are: see "Robots.txt notes" below for the exact strings that bite us.

### Identifier mapping (kleinanzeigen)

- **External id format:** numeric ad id, ~10 digits in current-era listings (sample: `3362398693`). Kleinanzeigen ids have varied in length historically; do **not** hard-code a digit count. Match `\d+` greedily against the documented anchors below.
- **Mapping to `ListingRow.id`:** `f"kleinanzeigen:{external_id}"` (e.g. `"kleinanzeigen:3362398693"`).
- **Extraction strategy (in order of preference):**
  1. **Listing-card attribute** — `article.aditem` carries both `data-adid="<numeric>"` and `data-href="/s-anzeige/<slug>/<adid>-<cat>-<loc>"`. Verified on every one of the 27 cards in the WG search fixture.
  2. **Detail-URL regex** — `re.compile(r"/s-anzeige/[^/]+/(\d+)-\d+-\d+(?:[?#].*)?$")` matches the trailing `<adid>-<categoryId>-<localityId>` triplet. The first group is the external id.
  3. **Sidebar `Anzeigen-ID` — for cross-checks only.** On the detail page, `<ul class="flexlist text-light-800"><li>Anzeigen-ID</li><li>3362398693</li></ul>` exposes the same id and lets us assert the URL-derived id and the page's own id match.

### URL patterns for the two listing kinds (kleinanzeigen)

- **WG (`kind='wg'`):** `https://www.kleinanzeigen.de/s-auf-zeit-wg/<city-slug>/sortierung:neuste/c199l<localityId>` — `/s-auf-zeit-wg/muenchen/sortierung:neuste/c199l6411` for Munich. Category `c199` = "Auf Zeit & WG".
- **Flat (`kind='flat'`):** `https://www.kleinanzeigen.de/s-mietwohnung/<city-slug>/sortierung:neuste/c203l<localityId>` — `/s-mietwohnung/muenchen/sortierung:neuste/c203l6411` for Munich. The alternate slug `/s-wohnung-mieten/<city-slug>/c203l<localityId>` returns the same listings. Category `c203` = "Mietwohnungen".
- **Generalization to other cities:** the `c<categoryId>` segment is global (`c199` / `c203` apply everywhere). The `l<localityId>` segment is per-city. Discover by visiting `https://www.kleinanzeigen.de/s-immobilien/<city-slug>/` (no `c…l…` suffix) and inspecting the resulting redirect — it lands on `/s-immobilien/<city-slug>/c195l<localityId>`.
- **Filters (URL-path segments):**
  - `sortierung:neuste` — verified to put newest postings first. The plugin always sends it because the per-stub freshness stop ([ADR-026](./DECISIONS.md#adr-026-drop-the-deletion-sweep-stop-pagination-on-the-first-stale-stub)) only works on chronologically sorted results, and Kleinanzeigen's posting date is detail-page-only — so without it the scraper would have to fetch every detail page in `(source, kind)` to find the freshness boundary.
  - `preis:<min>:<max>` — verified working with `/preis:800:1500/c199l6411`. **But:** `robots.txt` says `Disallow: /*/preis:*`. Don't use this in production scrapes.
  - `anbieter:privat`, `sortierung:empfohlen` — also `robots.txt`-disallowed (`/*/anbieter:*` and `/*/sortierung:*`). We accept the trade-off for `sortierung:neuste` because the source has to be opted in via `SCRAPER_ENABLED_SOURCES`; mirror wg-gesucht's strategy for the rest and leave price/seller filtering to the scorecard evaluator.

### How to list listings (kleinanzeigen search)

- **HTTP request:** `GET https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/c199l6411` (or the `c203l6411` flat variant) with headers:
  ```
  User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
  Accept-Language: de-DE,de;q=0.9,en;q=0.8
  Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
  Accept-Encoding: gzip, deflate, br
  ```
- **Cookies needed before the first useful response:** **No, but reuse a session.** The Akamai cookies (`bm_sz`, `_abck`, `up`) get set on first response, so subsequent requests within the same `httpx.AsyncClient` benefit. The recommended (defensive) pattern is to `GET https://www.kleinanzeigen.de/` first with `follow_redirects=True`, persisting cookies, then reuse the same `AsyncClient` for every search and detail fetch in the pass.

**Verified search-card selectors** (every count below was measured against the 27 articles in the WG search fixture):

| Field                | Selector                                                              | Verified count        | Notes                                                                                                                                  |
| -------------------- | --------------------------------------------------------------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Card root            | `article.aditem[data-adid]` *(or `li.ad-listitem > article.aditem`)*  | 27 / 27               | `data-adid` is the numeric external id; `data-href` on the same `<article>` is the canonical detail URL.                              |
| Title + detail URL   | `article > div.aditem-main h2.text-module-begin a.ellipsis`           | 27 / 27               | The `<a>` `href` is the same as `article[data-href]`. Either works.                                                                    |
| Price                | `p.aditem-main--middle--price-shipping--price`                        | 27 / 27               | Inner `<span>` holds e.g. `2.990 €`. Strip thousands separator (`.`), `€`, and trailing whitespace; some ads append `VB`. |
| Quick-fact tags      | `p.aditem-main--middle--tags`                                         | 27 / 27               | Dot-separated text like `108 m² · 2 Zi.` or `14 m² · 1 Zi. · Online-Besichtigung`. **This is where to grep size, room count, and online-viewing flag.** |
| Teaser text          | `p.aditem-main--middle--description`                                  | 27 / 27               | First ~150 chars of the description.                                                                                                   |
| Location             | `div.aditem-main--top--left`                                          | 27 / 27               | Text like `81675 Bogenhausen`. Note `\u200B` (zero-width space) embedded in district names like `Schwabing-\u200bFreimann`; strip when rendering. The first 5 digits are the PLZ. **City is not in this string** — it's implied by the search URL (`muenchen`). |
| Posting date         | *(not present on the search card — current Kleinanzeigen layout)*     | 0 / 27                | Posting date is only on the detail page (`#viewad-extra-info`). |
| Seller-type tag      | `.aditem-main--bottom .text-module-end span.simpletag`                | 8 / 27 (when present) | `Von Privat` for private sellers; absent for commercial sellers (Mr. Lodge etc.). |
| Cover image          | `article > div.aditem-image img[src]`                                 | 27 / 27               | `?rule=$_59.AUTO` is the small thumbnail variant; swap to `?rule=$_59.JPG` for full-resolution.                                       |

**HTML-parser gotcha (Python 3.14):** Kleinanzeigen ships an unterminated numeric character reference `&#8203` (zero-width space, no trailing `;`). bs4's bundled `html.parser` raises `ValueError: invalid literal for int() with base 10` on it. Pre-process raw HTML with `re.sub(r"&#(\d+)(?![\d;])", r"&#\1;", html)` before feeding to BeautifulSoup. The recipe at the bottom of this section shows the fix.

**Pagination:** Kleinanzeigen uses a `/seite:<N>` path segment inserted **before** the `sortierung:neuste/c<cat>l<loc>` token, e.g. `https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/seite:2/sortierung:neuste/c199l6411`. **Robots.txt cap:** `Disallow: /*/seite:6*` through `/*/seite:59*` — pages 1-5 are crawl-allowed, pages 6+ are not. The plugin doesn't enforce its own ceiling; the agent enforces `SCRAPER_MAX_PAGES` (default 6) and additionally pagination terminates when (a) the page contains zero `article.aditem` cards or (b) the response trips `looks_like_block_page`. Stale stubs (revealed by `scrape_detail`, since Kleinanzeigen doesn't expose posting dates on the search card) are dropped without persisting and the walk continues — one detail fetch per stale ad is the price of the date being detail-only. With `SCRAPER_MAX_PAGES=6` the agent walks `seite:1` through `seite:6`; `seite:6` falls inside the robots cap. We accept this trade-off for the same reason as `sortierung:neuste` (operator-opted-in via `SCRAPER_ENABLED_SOURCES`), and in practice the page count rarely matters because freshness-driven thinning leaves few stubs after a couple of pages.

### How to read one listing (kleinanzeigen detail)

- **HTTP request:** `GET https://www.kleinanzeigen.de/s-anzeige/<slug>/<adid>-<categoryId>-<localityId>` with the same headers and the same cookie-bearing `AsyncClient` from the search pass. Always pull the URL from the search card's `data-href` rather than constructing it.
- **Field-by-field mapping into the domain `Listing`** (every selector below verified by direct `bs4` query against three distinct ads spanning commercial-WG / private-WG / flat verticals):

  | `Listing` field                     | Source on Kleinanzeigen detail page                                                                                                                                                                                                                                                                                                                                                |
  | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `id`                                | from search-card `data-adid` (preferred) or detail-URL trailing `(\d+)-\d+-\d+`. Then `f"kleinanzeigen:{numeric}"`. Cross-check against the sidebar `<ul class="flexlist text-light-800"><li>Anzeigen-ID</li><li><id></li></ul>`. |
  | `url`                               | the `data-href` from the search card, or `<meta property="og:url">` on the detail page (canonical, with the server-corrected locality id). |
  | `kind`                              | `'wg'` if the search vertical was `c199`, `'flat'` if it was `c203`. Set by the scraper, not parsed.                                                                                                                                                                                                                                                                              |
  | `title`                             | `h1#viewad-title`. Sample: `<h1 id="viewad-title" class="…">Möbliert: Nähe Prinzregentenplatz: moderne, klare Linien</h1>`. |
  | `price_eur`                         | `h2#viewad-price` (also matches `#viewad-price`). Renders e.g. `2.990 €` for fixed prices, `20 € VB` for "Verhandlungsbasis". Strip thousands separator (`.`), `€`, and trailing `VB`; parse to int. Leave `price_eur=None` for `VB`-only or `Auf Anfrage` ads.                                                                                            |
  | `size_m2`                           | from the attribute table (label `Wohnfläche`, value `108 m²`). Selector: `li.addetailslist--detail` whose text starts with `Wohnfläche`, with the value in the inner `span.addetailslist--detail--value`. Parent is `<div class="addetailslist">` (not `<ul>`). |
  | `wg_size`                           | WG vertical only. Label `Anzahl Mitbewohner` (= existing flatmates). Same `addetailslist--detail` row pattern. Map to total flatmates via `wg_size = mitbewohner + 1` for parity with wg-gesucht's `(\d+)er WG` (which counts the new tenant). |
  | `address` / `city` / `district`     | `#viewad-locality` — text like `81675 München - Bogenhausen`. Note: the selector matches **two** elements (header info + map widget) because Kleinanzeigen reuses the `id`. Both have identical text; pick the first via `.select_one()`. Split on whitespace + ` - `: PLZ → first 5 digits, city → middle, district → tail. Backup: `<meta property="og:locality">`. |
  | `lat` / `lng`                       | **Verified present:** `<meta property="og:latitude" content="48.1381386"/>` and `<meta property="og:longitude" content="11.6033512"/>` on the detail page. Coordinate precision is street-level (7 decimal digits). **No geocoding needed.** Selector: `soup.find("meta", attrs={"property": "og:latitude"})["content"]`. |
  | `available_from`                    | `addetailslist--detail` row labelled `Verfügbar ab` — verified on the flat sample ad as `April 2026` (month-year text, not `dd.mm.yyyy`). On WG ads it may also render `Mai 2026`-style or be absent. Parse as `date(year, month, 1)` for month-year strings; treat absence as `None`. |
  | `available_to`                      | only present when `Mietart: befristet`. Look for a `Bis` or `Verfügbar bis` row in `addetailslist--detail`. |
  | `description`                       | `#viewad-description-text`. Sibling `#viewad-description` is the wrapper that prepends the literal label "Beschreibung "; prefer `#viewad-description-text` to skip the label. Strip leading/trailing whitespace; preserve `\n`. Scrub `<script>` / `<iframe>` like wg-gesucht does. |
  | `photo_urls` / `cover_photo_url`    | **Two verified strategies:** (a) DOM walk: `div.galleryimage-element img`. (b) **Cleaner**: parse every `<script type="application/ld+json">` block, keep entries with `@type=ImageObject`, dedup by `contentUrl`. Verified to return all 23 unique gallery photos for the recon's 20-photo ad. `cover_photo_url = <meta property="og:image">` directly. Cap at 12. |
  | `furnished`                         | `<li class="checktag">Möbliert</li>` inside `<ul class="checktaglist">`. **Caveat:** the flat sample also exposes `<li class="checktag">Möbliert/Teilmöbliert</li>` — match `^Möbliert(/Teilmöbliert)?$` to catch both. `True` when present, else `None` (don't infer `False` from absence). |
  | `pets_allowed`                      | `<li class="checktag">` whose text equals `Haustiere erlaubt`. Same true-or-None rule. |
  | `smoking_ok`                        | `addetailslist--detail` row labelled `Rauchen` — values: `unerwünscht` → `False`, `Raucher willkommen` → `True`, `Nichtraucher` → `False`. |
  | `online_viewing`                    | `addetailslist--detail` row labelled `Online-Besichtigung` — values: `Möglich` → `True`, `Nicht möglich` → `False`, absent row → `None`. Also visible as a free-text token in the search-card's `p.aditem-main--middle--tags` ("…· Online-Besichtigung"), useful as a stub-time signal. |
  | `languages`                         | **not exposed** on Kleinanzeigen. Leave `[]`. |
  | *(extra)* posting date              | `#viewad-extra-info > div:first-child > span` — text `dd.mm.yyyy`. Sibling `#viewad-cntr` holds the view counter. Persist if/when the schema gets a `posted_at` column. |

### Anti-bot posture (kleinanzeigen)

- **WAF / challenge observations:** in the verified recon (anonymous httpx, fresh client, no warm-up — and again with warm-up, and again across 5 sequential paginated fetches) **every response was 200 with the full listing DOM**. No Cloudflare interstitial, no DataDome challenge HTML, no `<script src="…datadome…">` reference, no 403 / 429. Cookies set by Kleinanzeigen on first response are **Akamai bot-manager** (`bm_sz`, `_abck`, `kameleoonVisitorCode`, `up`, `lnFeMonitoring`).
- **Cookie warm-up strategy (recommended, not required):**
  1. First request of every fresh `httpx.AsyncClient` session: `GET https://www.kleinanzeigen.de/` with the same headers as the listing fetches and `follow_redirects=True`. Persist cookies into the client's jar. Discard the response body.
  2. Sleep ≥ 1s, then begin search/detail fetches with the same client.
  3. **If** subsequent fetches return 4xx or anti-bot HTML, escalate to `curl_cffi.requests.AsyncSession(impersonate="chrome124")` (TLS + HTTP/2 fingerprint impersonation) for the warm-up only, then re-attempt with httpx using the harvested cookie jar. Real browser (Playwright) is the last resort.
- **Rate limit guidance:** ≥ **2.5s** between consecutive search-page fetches (≈ 1.6× wg-gesucht's `ANONYMOUS_PAGE_DELAY_SECONDS`); ≥ **3.5s** between detail-page fetches; ≥ **15 min** between full passes per vertical. On 403 / 429 / block-page detection: exponential backoff starting at 30s, cap at 30 min, and surrender the current pass.
- **Block-page detection:** treat the response as a block when **none** of the verified positive markers are present, AND any of the negative signals fire. Verified positive markers: `soup.select("article.aditem[data-adid]")` returns ≥1 (search page); `soup.select_one("h1#viewad-title")` is not None (detail page); `soup.find("meta", attrs={"property": "og:url"})` content starts with `https://www.kleinanzeigen.de/s-anzeige/`. Negative signals: response status `403`/`429`; body matches `/datadome|please verify you are human|sicherheitsüberprüfung|ungewöhnlichen datenverkehr|automated requests/i`; body contains `<script src="…datadome…">`; soft-redirect to homepage detected via `response.url`; `len(response.text) < 5_000` for a search or detail URL (a real search page is ≥350 KB; a real detail page is ≥250 KB).

### Robots.txt notes (kleinanzeigen)

`https://www.kleinanzeigen.de/robots.txt` (~10 KB, captured 2026-04-18). For `User-agent: *` (us), the rules that touch our planned URLs are:

- **Listing-path roots — NOT disallowed (crawlable):** `/s-auf-zeit-wg/`, `/s-mietwohnung/`, `/s-wohnung-mieten/`, `/s-anzeige/`, `/s-immobilien/`. Also no `Crawl-delay` directive — the pacing in "Anti-bot posture" is our self-imposed budget.
- **Pagination ceiling:** `Disallow: /*/seite:6*` through `/*/seite:59*`. Pages 1-5 are crawl-allowed, **pages 6 and beyond are forbidden by `robots.txt`**. The 27-cards-per-page × 5 pages = ≤135 listings per pass per vertical. Don't crawl past `seite:5`.
- **URL-path filters — disallowed:** `/*/preis:*`, `/*/anbieter:*`, `/*/sortierung:*`, `/*+options:*`, `/*/c*r5` through `/*/c*r200` (radius-around-locality), `/*/l*r5` through `/*/l*r200`, `/*/k0*r5` through `/*/k0*r200`. Do all numeric range / sorting / radius / "anbieter:privat" filtering client-side after fetching the unfiltered page.

### Verified end-to-end recipe (kleinanzeigen)

The snippet below was executed against the live site on 2026-04-18 with the project's `backend/venv` (Python 3.14, `httpx==0.28.1`, `beautifulsoup4==4.14.3`, no `lxml`). All selectors and behaviors above derive from running this script. The selector tables and the `_BAD_CHARREF` patch are the **source of truth** referenced verbatim by [`../backend/app/scraper/sources/kleinanzeigen.py`](../backend/app/scraper/sources/kleinanzeigen.py).

```python
"""Anonymous httpx + BeautifulSoup recipe for kleinanzeigen.de."""
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

        # 3. Detail page for the first card.
        if not cards:
            return
        await asyncio.sleep(KA_PAGE_DELAY_SECONDS + 1.0)
        resp = await client.get(cards[0]["url"])
        resp.raise_for_status()
        detail = parse_listing_page_ka(resp.text)
        print(f"detail: {detail['title']!r}  coords=({detail.get('lat')}, {detail.get('lng')})")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Local scraper run (laptop)

The scraper is no longer a cloud service — a developer runs it on their laptop against the shared RDS using the same `.env` the backend reads:

```bash
# From the repo root, with .env populated.
cd backend
source venv/bin/activate           # or: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
python -m app.scraper.main
```

The agent listens for these env knobs:

- `SCRAPER_ENABLED_SOURCES` — comma-separated source names. Default `wg-gesucht`. Valid: `wg-gesucht`, `tum-living`, `kleinanzeigen`.
- `SCRAPER_CITY` — default `München`.
- `SCRAPER_MAX_RENT` — default `2000`.
- `SCRAPER_INTERVAL_SECONDS` — between full passes. Default `300`.
- `SCRAPER_REFRESH_HOURS` — re-scrape threshold for full listings. Default `24`.
- `SCRAPER_KIND` — restrict which verticals the agent iterates. One of `wg`, `flat`, `both`. Default `both`.
- `SCRAPER_MAX_AGE_DAYS` — drop listings whose posting date is older than this. Default `4`. Stale stubs are skipped without persisting and the walk continues with the next stub (ADR-027). For sources whose stub lacks `posted_at` (kleinanzeigen — date is detail-only) the same drop fires post-`scrape_detail`, costing one detail fetch per stale ad.
- `SCRAPER_MAX_PAGES` — hard cap on pages walked per `(source, kind)` per pass. Default `6`. The cap is per `(source, kind)` rather than per source, so a source that supports both verticals can do up to `2 × SCRAPER_MAX_PAGES` pages per pass.
- `SCRAPER_ENRICH_ENABLED` / `SCRAPER_ENRICH_MODEL` / `SCRAPER_ENRICH_MIN_DESC_CHARS` — optional LLM enrichment of missing structured fields (`furnished`, `wg_size`, …) when the description states them clearly. Default off (`false` / `gpt-4o-mini` / `200`); requires `OPENAI_API_KEY`. Coordinates remain on the deterministic Google Geocoding fallback path. See [DECISIONS.md ADR-025](./DECISIONS.md#adr-025-llm-driven-enrichment-of-missing-structured-fields).

## Migration verification

After running [`migrate_multi_source.py`](../backend/app/scraper/migrate_multi_source.py) and cycling the scraper, these SQL checks confirm the multi-source rollout is healthy. They originated as the G1–G9 success criteria of the multi-source plan and remain useful as a one-off sanity sweep:

- **G1** — Every new `ListingRow.id` is namespaced. `SELECT id FROM listingrow WHERE id NOT REGEXP '^(wg-gesucht|tum-living|kleinanzeigen):' AND first_seen_at > <cycle_start>;` returns 0 rows.
- **G2** — Every `ListingRow` carries a non-null `kind ∈ {'wg', 'flat'}`. `SELECT count(*) FROM listingrow WHERE kind NOT IN ('wg','flat') OR kind IS NULL;` returns 0.
- **G3** — A user with `SearchProfile.mode = 'flat'` only gets `kind='flat'` listings. `SELECT l.kind, count(*) FROM listingrow l JOIN userlistingrow u ON u.listing_id = l.id WHERE u.username = '<u>' GROUP BY l.kind;` returns one row, `flat`.
- **G7** — Each source has fixture-driven offline parser tests: `pytest backend/tests/scraper/ -v` passes offline.
- **G9** — No `ListingRow.description` is silently truncated. `SELECT count(*) FROM listingrow WHERE scrape_status = 'full' AND CHAR_LENGTH(description) = 255;` returns 0. Schema check: `SHOW COLUMNS FROM listingrow LIKE 'description';` reports `text` (not `varchar(255)`).

## See also

- [BACKEND.md "Agent loop"](./BACKEND.md#agent-loop) — end-to-end sequence diagrams for one scraper pass and one match pass.
- [DATA_MODEL.md](./DATA_MODEL.md) — `ListingRow` columns + the three-layer rule.
- [DECISIONS.md ADR-018](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only), [ADR-020](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing), [ADR-021](./DECISIONS.md#adr-021-listing-kind-as-a-first-class-column) — the relevant ADRs.
- [ROADMAP.md](./ROADMAP.md) — queued: deterministic pre-filter on search results.
- [context/TUM_SYSTEMS.md](../context/TUM_SYSTEMS.md) — broader TUM API + scraping notes.
