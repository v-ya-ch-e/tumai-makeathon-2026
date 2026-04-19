# Source: wg-gesucht.de

> Anonymous httpx + BeautifulSoup scrape of `wg-gesucht.de`. Owns every listing the loop in [`./agent.py`](./agent.py) writes via `repo.upsert_global_listing`. This is the only source live in v1.

## At a glance

- **Site:** `https://www.wg-gesucht.de` (`BASE_URL` in [`../wg_agent/browser.py`](../wg_agent/browser.py)).
- **Transport:** anonymous `httpx.AsyncClient` + `BeautifulSoup`. **No Playwright at runtime.** Playwright code (`WGBrowser`, `launch_browser`, `ensure_logged_in`, `send_message`, `fetch_inbox`) lives in the same module but is dead code in v1 — the scraper loop never instantiates it.
- **Code:**
  - Search + parse + detail fetch: [`../wg_agent/browser.py`](../wg_agent/browser.py) (`build_search_url`, `parse_search_page`, `parse_listing_page`, `_parse_map_lat_lng`, `_anon_client`, `anonymous_search`, `anonymous_scrape_listing`).
  - Loop, dedup, deletion sweep: [`./agent.py`](./agent.py) (`ScraperAgent.run_once`, `_needs_scrape`, `_scrape_and_save`, `_sweep_deletions`).
  - Process entrypoint: [`./main.py`](./main.py) (`python -m app.scraper.main`).
- **Anti-bot:** real Chrome User-Agent + `Accept-Language: de-DE,de;q=0.9,en;q=0.8` (see `_anon_client`); captcha/Turnstile interstitials detected by `_looks_like_block_page` and returned as the unmodified stub instead of crashing; rate-limit constant `ANONYMOUS_PAGE_DELAY_SECONDS = 1.5` between search-page fetches. Detail fetches are paced indirectly by the loop interval.
- **Data freshness:** `SCRAPER_INTERVAL_SECONDS` (default 300s, between full passes) and `SCRAPER_REFRESH_HOURS` (default 24h, re-scrape threshold for full listings). Both read by `ScraperAgent.__init__`.

## Identifier mapping

- **External id format:** digit string, 5–9 digits (e.g. `12345678`). Source canonical URL is `https://www.wg-gesucht.de/<id>.html`.
- **Extraction sites:**
  - `_LISTING_ID_RE = re.compile(r"[./](\d{5,9})\.html")` — runs against every `<a href>` on the search-result card.
  - `data-id` attribute on `div.wgg_card.offer_list_item` — preferred when present (`parse_search_page` reads `card.get("data-id")` first).
- **`ListingRow.id` mapping (target):** `f"wg-gesucht:{external_id}"` (e.g. `"wg-gesucht:12345678"`).
- **Today's behavior (pre-refactor):** `parse_search_page` writes the bare numeric id into `Listing.id`, and `repo.upsert_global_listing` then stores it directly as `ListingRow.id`. The `wg-gesucht:` namespace prefix is **not yet applied**; see the TODO at the end of this file.

## Listing kind (`wg` vs `flat`)

- **WG (shared room) — wired today.** `build_search_url` hardcodes the slug template `/wg-zimmer-in-<City>.<cityId>.0.<rentType>.<page>.html`, where `0` is the WG-room category id. Every listing the scraper currently emits is therefore a WG room.
- **Full flat — not wired.** `wg-gesucht.de` exposes separate verticals for 1-Zimmer-Wohnungen, Wohnungen, and Häuser, but their numeric category ids are **not verified anywhere in this repo or in [`../../../docs/WG_GESUCHT.md`](../../../docs/WG_GESUCHT.md)** (the recon doc only confirms `categoryId=0` for WG rooms). Building a flat search URL requires confirming the right slug and category id against the live site first; until then we cannot honor `SearchProfile.mode = "flat"` or `"both"` on the scraper side.
- **How `kind` will be set (target):** the scraper passes `kind='wg'` when iterating the WG vertical and `kind='flat'` when iterating the flat vertical(s). The two passes share `parse_listing_page`; only the search-URL builder and the `kind` value differ.
- **Field mapping aside:** `SearchProfile.mode` exists today (`"wg" | "flat" | "both"` — see [`../wg_agent/models.py`](../wg_agent/models.py)) but does not influence `build_search_url`. The evaluator branches on it (e.g. `wg_size_fit` in `evaluator.py`); the scraper does not.

## Search URL parameters we use

We pass a small, safe filter set (`rMax`, `rMin`, `sMin`, `sMax`, `furnishedSea`) and rely on the scorecard evaluator for fine-grained matching. Full parameter table and the URL schema live in [`../../../docs/WG_GESUCHT.md`](../../../docs/WG_GESUCHT.md) — do not duplicate.

**Gotcha worth repeating:** `offer_filter=1` and `city_id` cause a malformed 301 redirect that 404s. `build_search_url` deliberately omits both. Don't add them.

## Per-listing data we extract

Source code: `parse_listing_page` and `_parse_map_lat_lng` in [`../wg_agent/browser.py`](../wg_agent/browser.py). Search-card stub fields are filled by `parse_search_page` first; the detail pass overwrites where the page provides better data.

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
| `description` | — | `#ad_description_text` is the wrapper that contains all 1–4 `#freitext_N` children (Zimmer / Lage / WG-Leben / Sonstiges) plus their `<h3>` headings. The parser walks the wrapper with `get_text("\n", strip=True)`, capturing every section in document order; scrubs `<script>`, `<iframe>`, `[id^="div-gpt-ad-"]`. Fallback to standalone `#freitext_*` selectors only fires if `#ad_description_text` is missing entirely. Never falls back to whole-page text. **⚠ Storage truncation:** `ListingRow.description` is `VARCHAR(255)` today (the SQLModel default for bare `Optional[str]`); a 2079-char description is silently chopped to 255 chars on `session.merge`. Fix is in [`../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md) step 1 (widen to `TEXT` + force re-scrape). |
| `languages` | — | WG-Details `<li>` matching `Sprache(n): …`; regex fallback on full text |
| `furnished` | — | WG-Details `<li>` or `div.utility_icons > div.text-center` quick-fact tile matching `möbliert` with no same-line negation (`nicht`/`un-`/`teilweise`) |
| `pets_allowed` | — | WG-Details `<li>` `Haustiere vorhanden: Ja|Nein`; full-text regex fallback |
| `smoking_ok` | — | WG-Details `<li>` `Rauchen (nicht) erwünscht`; full-text regex fallback |
| `lat`, `lng` | — | `_parse_map_lat_lng` reads the first marker out of the embedded `var map_config = { ... markers: [{"lat":…,"lng":…}] }` block. Falls back to `geocoder.geocode(address or "<district>, <city>")` only when the map block is absent or unparseable (in `anonymous_scrape_listing`). |
| `online_viewing` | substring `"Online-Besichtigung"` in card text | — |
| `photo_urls`, `cover_photo_url` | — | `_parse_photo_urls` walks `og:image`, `[data-full-image]`, `img[data-src/data-lazy/src]`, `source[srcset]`; filters out logos/avatars/icons/placeholder gallery elements; capped at 12. `cover_photo_url = photo_urls[0]`. |
| `posted_at` (transient — not in `ListingRow`; used only by `ScraperAgent._is_fresh_enough`) | `parse_search_page` finds the first `<span>` text matching `^\s*Online\s*:` inside the card, strips the `Online:` prefix, then `_parse_wgg_online_value` returns either `now - <n*unit>` for the relative form (`Online: 25 Minuten`, `Online: 1 Stunde`, `Online: 2 Tage`) or `datetime(y, m, d)` for the absolute form (`Online: 12.03.2026`). The regex is **anchored at the start of the string** so it doesn't catch `Online-Besichtigung` (the unrelated online-viewing flag, which can co-occur on the same card). The relative form fires for ads <24h old; the absolute form (`dd.mm.yyyy`) takes over from ≥24h. **Verified live 2026-04-19** against `wg-zimmer-in-Muenchen.90.0.1.0.html?rMax=2000` — all 27/27 page-1 cards exposed `Online: …` in the bare `<span>` inside `div.col-xs-9 > div.row.noprint > div.col-sm-12.flex_space_between`. The detail page also exposes the same value as `<b>` adjacent to `<span class="section_panel_detail">Online:</span>` (verified on listings 13302903 + 12282197), but the search card is sufficient. **Default sort is "most recently bumped" not strictly chronological**, so the agent does NOT do an early-stop heuristic on this source — it relies on the per-stub freshness gate in `_run_source` instead. | `<b>` next to `<span class="section_panel_detail">Online:</span>` inside a `div.row` — currently unused by the parser since the search-card value is always populated. |

Fields on the `Listing` domain model that this source **never** populates (filled later by the matcher per user, not by the scraper): `score`, `score_reason`, `match_reasons`, `mismatch_reasons`, `components`, `veto_reason`, `best_commute_minutes`.

## Anti-bot posture

- **Headers:** `_anon_client` sets `User-Agent` to a real Chrome string (`USER_AGENT` constant, Chrome 124 / macOS) and `Accept-Language: de-DE,de;q=0.9,en;q=0.8`. `follow_redirects=True`. No cookie jar persisted across passes.
- **Block detection:** `_looks_like_block_page(soup, full_text)` returns `True` when the page has no `#ad_description_text`, no `Kosten/Verfügbarkeit/Adresse` `<h2>`, no contact link, AND either ships a `data-sitekey` element, a `turnstile|captcha`-named iframe/script, or matches a German/English captcha-language regex (`captcha`, `turnstile`, `verify you are human`, `Sicherheitsüberprüfung`, `ungewöhnlichen Datenverkehr`, `automated requests`, `robot`, …). When `True`, `parse_listing_page` returns the stub unchanged so the loop persists what it has rather than crashing.
- **Pacing:** `ANONYMOUS_PAGE_DELAY_SECONDS = 1.5` between consecutive search-result page fetches inside one pass (`anonymous_search`). Detail fetches inside one pass run back-to-back; pacing between passes is `SCRAPER_INTERVAL_SECONDS` (default 300s).
- **Refresh:** `ScraperAgent._needs_scrape` skips re-scraping a listing whose `scrape_status == "full"` and whose `scraped_at` is newer than `now - SCRAPER_REFRESH_HOURS`. Stubs (`status != "full"`) and rows with `scraped_at is None` are always re-scraped.
- **Dedup:** automatic via `repo.upsert_global_listing` — it calls `session.get(ListingRow, listing.id)` and `session.merge(...)`, so two passes that surface the same `id` produce one row.

## Soft-delete sweep

`ScraperAgent._sweep_deletions` runs at the end of every `run_once`. It diffs the set of listing ids returned by the current search against `repo.list_active_listing_ids` (rows with `scrape_status == "full"` and `deleted_at IS NULL`). Listings missing from the search increment a per-id counter held in memory on the agent (`self._missing_passes`); once a counter reaches `SCRAPER_DELETION_PASSES` (default 2), `repo.mark_listing_deleted` stamps `deleted_at` and the counter is dropped. Reappearance in the search resets the counter. The two-pass threshold absorbs single-pass blips where a listing falls off page 1 transiently.

## TODOs (until the multi-source refactor lands)

- [ ] **Widen `ListingRow.description` (and other text columns) from `VARCHAR(255)` to `TEXT`.** Bare `Optional[str]` columns in [`../wg_agent/db_models.py`](../wg_agent/db_models.py) get `VARCHAR(255)` from SQLModel/SQLAlchemy by default, so the full 1–4-section description (verified at 2079 chars on listing `12557568`) is silently truncated by MySQL on write. The parser is already correct; only the schema and a one-shot UPDATE to force re-scrape are needed. Sequenced as step 1 of [`../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md).
- [ ] Switch the id produced by `parse_search_page` (and read in `_LISTING_ID_RE`-driven fallbacks) to `f"wg-gesucht:{numeric_id}"` so `ListingRow.id` is namespaced from the moment the stub is built.
- [ ] Add a `kind: Literal['wg', 'flat']` field to the domain `Listing` model and a matching column on `ListingRow`. Default to `'wg'` for the existing path; the scraper sets it from which vertical it iterated.
- [ ] Add a flat-vertical search URL builder (extension of `build_search_url` or a sibling `build_flat_search_url`) once the wg-gesucht category id for `Wohnungen` (and optionally `1-Zimmer-Wohnungen` / `Häuser`) is verified against the live site. The category id is **not** confirmed in this repo today.
- [ ] Wire `SearchProfile.mode` into the scraper loop: `'wg'` → WG vertical only, `'flat'` → flat vertical only, `'both'` → both verticals in sequence with their respective `kind` tags.
- [ ] Backfill migration: `UPDATE listingrow SET id = CONCAT('wg-gesucht:', id)` plus the same on every FK referencing `listingrow.id` (`photorow.listing_id`, `userlistingrow.listing_id`, `useractionrow.listing_id`). Coordinate with the multi-source ADR.

## See also

- [`./README.md`](./README.md) — multi-source contract (pending; this file documents the wg-gesucht-specific half).
- [`../../../docs/WG_GESUCHT.md`](../../../docs/WG_GESUCHT.md) — full site recon: URL schema, DOM selectors, login/messaging notes, anti-bot guidance.
- [`../../../docs/DECISIONS.md`](../../../docs/DECISIONS.md) — ADR log; multi-source ADRs pending.
