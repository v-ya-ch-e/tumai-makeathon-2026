# Roadmap

What's next for WG Hunter — ranked by demo impact and scoped so a teammate can pick any item up cold. If you see something you want to own, ping the team channel, then edit this file when the PR lands.

Background reading for each item is linked inline. The evaluator pipeline is defined in [ADR-015](./DECISIONS.md) and the implementation in `[evaluator.py](../backend/app/wg_agent/evaluator.py)`.

## Queued (clearly scoped, ready to pick up)

### wg-gesucht flat-vertical scraping (close the `mode='flat'` gap on wg-gesucht)

**Why:** Per [SCRAPER.md "Source: wg-gesucht"](./SCRAPER.md#url-schema-wg-gesucht), the wg-gesucht plugin currently advertises `kind_supported = {'wg'}` because the URL slug + numeric category id for the flat verticals (`Wohnungen`, `1-Zimmer-Wohnungen`, `Häuser`) are not verified anywhere in this repo. Users with `mode='flat'` already get flats from Kleinanzeigen + TUM Living, but wg-gesucht is the largest pool in Munich, so closing this gap roughly doubles the flat candidate count.

**Shape of the change:**

- Live recon (~30 min, interactive): open `https://www.wg-gesucht.de/` in the cursor-ide-browser MCP, pick "Munich", select "Wohnungen", harvest the resulting URL pattern (slug + category id digit). Confirm against one detail page that the existing `parse_listing_page` selectors still apply (we expect them to — the per-listing DOM is shared across verticals, only the search URL differs).
- Add `_FLAT_CATEGORY_ID` constant + `build_flat_search_url(req, page_index=0)` helper in [`browser.py`](../backend/app/wg_agent/browser.py), mirroring `build_search_url`.
- Extend `WgGesuchtSource` ([`backend/app/scraper/sources/wg_gesucht.py`](../backend/app/scraper/sources/wg_gesucht.py)) to `kind_supported = frozenset({"wg", "flat"})` and dispatch on `kind` to either `build_search_url` or `build_flat_search_url` inside `search`.
- Add a `parse_search_page` test fixture for one captured flat-vertical search page; assert id namespacing + `kind == "flat"`.

**Touches:** `backend/app/wg_agent/browser.py`, `backend/app/scraper/sources/wg_gesucht.py`, [`docs/SCRAPER.md`](./SCRAPER.md) (update the wg-gesucht flat-vertical note), `backend/tests/test_wg_parser.py` (or a sibling fixture-driven test).

### Deterministic pre-filter on search results

**Why:** Right now every new listing id is deep-scraped + geocoded + commute-routed before `[evaluator.hard_filter](../backend/app/wg_agent/evaluator.py)` can veto it. That's wasted work for listings that `anonymous_search` already returns with enough data to reject (e.g. price exceeds `max_rent_eur`, WG size outside range, availability clearly outside the move-in window). Cutting this step saves one HTTP round-trip to wg-gesucht + one Routes API call per rejected listing, which matters when the top results page is dominated by over-budget listings during peak season.

**Shape of the change:**

- Add a new `evaluator.can_search_filter(stub_listing, profile) -> Optional[VetoResult]` that runs **only** the vetoes that don't need the scraped description or coords (price, WG size, move-in date). Keep the full `hard_filter` for the post-scrape pass.
- In the scraper (`[ScraperAgent.run_once](../backend/app/scraper/agent.py)`), run this cheaper filter on the `new_stubs` list produced by `anonymous_search` before the deep-scrape step, so stubs clearly outside the team-wide budget never hit wg-gesucht again. For per-user vetoes, run the same filter inside `[UserAgent.run_match_pass](../backend/app/wg_agent/periodic.py)` before the `new_listing` action and log a compact `Skipped pre-filter: <reason>` action so the UI still hears about vetoed candidates.
- Persist the per-user skip to `UserListingRow` (with `score=0.0`, `veto_reason=<reason>`, `components=null`) so the user can still see the rejected listing in the drawer and understand why, mirroring today's post-scrape veto path.

**Touches:** `evaluator.py`, `periodic.py`, `scraper/agent.py`, `test_evaluator.py` (add pre-filter cases), `test_periodic.py` (assert the skipped candidate still persists a `UserListingRow`).

### Surface rejections in the dashboard list

**Why:** `ListingList` currently sorts by score so vetoed listings sink to the bottom unsegregated. Teammates and users can't easily answer "did the agent see listing X and reject it?" vs. "is it still being evaluated?"

**Shape of the change:** in `[ListingList.tsx](../frontend/src/components/ListingList.tsx)`, split the sorted list into two sections — "Matched" (no `vetoReason`) and "Rejected" (has `vetoReason`). Rejected cards collapse to a single-line row showing the veto reason in `text-bad` without the score pill.

**Touches:** `ListingList.tsx`, maybe a small tweak to `ListingDrawer.tsx` if we want to keep the breakdown hidden for vetoed listings.

## Later (design work needed first)



### LLM-as-judge per fuzzy component (with self-consistency)

Instead of one `vibe_fit` LLM call, ask the LLM to rate each soft component (`preference_fit` keywords that can't be resolved deterministically, vibe, district fit) independently with structured output per component. Add a self-consistency pass: two calls at `temperature=0.2`, reject if they disagree by > ε. Roughly 2–3× the LLM cost of today's path, so hold until we see the single-call variance become a real problem during demos.

## Done recently

Track what's shipped so reviewers and demo judges can spot-check the history without spelunking git.

- **2026-04-18** — ADR-020 + ADR-021: multi-source scraper. Generalized `ScraperAgent` from a hard-coded wg-gesucht loop into a `Source` plugin registry under [`backend/app/scraper/sources/`](../backend/app/scraper/sources/) (wg-gesucht, TUM Living, Kleinanzeigen — selectable via `SCRAPER_ENABLED_SOURCES`). Every `ListingRow.id` is now namespaced (`f"{source}:{external_id}"`); every row carries a `kind` (`'wg'` | `'flat'`); the matcher honors `SearchProfile.mode` for the first time. Per-source-scoped deletion sweep so a wg-gesucht-only pass cannot tombstone Kleinanzeigen rows. New [`backend/app/scraper/migrate_multi_source.py`](../backend/app/scraper/migrate_multi_source.py) one-shot DB migration (idempotent, transactional, `--dry-run`). Recon + contract: [`docs/SCRAPER.md`](./SCRAPER.md).
- **2026-04-18** — ADR-019: dropped Alembic in favour of `SQLModel.metadata.create_all` on startup; deleted `backend/alembic/` + `backend/alembic.ini` + the `alembic` dependency. See [BACKEND.md "Schema evolution"](./BACKEND.md#schema-evolution).
- **2026-04-18** — ADR-018: split scraper into its own container, global `ListingRow` pool, MySQL-only persistence. See [DECISIONS.md](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only).
- **2026-04-18** — ADR-015: scorecard evaluator with deterministic components + narrow LLM vibe. Replaces the single-LLM-call scoring path. 50 new tests in `[test_evaluator.py](../backend/tests/test_evaluator.py)`; component-breakdown bars in `[ListingDrawer](../frontend/src/components/ListingDrawer.tsx)`.
- **2026-04-18** — ADR-014: structured DOM selectors + `map_config.markers` coords in `parse_listing_page`; zeroed out the cookie-banner-text-in-prompt regression and cut Geocoding API calls to near-zero for listings that render a map.
- **2026-04-18** — ADR-013: weighted preferences + per-location commute budgets. UI: 1–5 weight slider on each preference tile + per-location ideal-commute input.
- **2026-04-18** — ADR-012: commute-aware scoring via Routes API's `computeRouteMatrix`, LLM-only composition (superseded by ADR-015).
- **2026-04-18** — ADR-010 + ADR-011: structured `main_locations` with client-side Places Autocomplete and server-side Geocoding for listing addresses.

---

*This file supersedes the old `ISSUES_TO_FIX.md` scratchpad. When you ship something from "Queued" or "Later", move it under "Done recently" with a one-line summary + ADR/commit reference.*