# Multi-source scraper — implementation plan

> Implementation plan for extending the WG Hunter scraper from single-source (wg-gesucht WG-only) to **three sources × two verticals** (`wg-gesucht`, `tum-living`, `kleinanzeigen` × `wg`/`flat`). The substrate is verified in [`../backend/app/scraper/README.md`](../backend/app/scraper/README.md) and the three `SOURCE_*.md` docs alongside it.
>
> **Repo facts callers should know before reading.** Persistence is **MySQL only** ([`../backend/app/wg_agent/db.py`](../backend/app/wg_agent/db.py)) and **Alembic was deleted** in [ADR-019](./DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all); schema is bootstrapped via `SQLModel.metadata.create_all` (additive only, drops require `DROP DATABASE`). The new ADRs in this plan are therefore numbered **ADR-020 / ADR-021**, not ADR-019 / ADR-020 as the original prompt suggested — ADR-019 is taken.
>
> **No source contradictions found** between the four scraper docs while preparing this plan; each per-site doc flags its own open questions (TUM Living `wg_size`, Kleinanzeigen `wg_size`, wg-gesucht flat-vertical category id) and this plan resolves them coherently in [Open design decisions](#open-design-decisions).

## TL;DR

- **Schema prerequisite (step 1):** widen `ListingRow.description` (and `url`/`title`/`city`/`district`/`address`/`scrape_error`) from the SQLModel-default `VARCHAR(255)` to `TEXT`, then `UPDATE listingrow SET scrape_status = 'stub' WHERE scrape_status = 'full'` to force the scraper to re-fetch every existing listing through the wider columns. Verified prerequisite — without it, wg-gesucht descriptions are silently chopped to 255 chars on write (verified on listing `12557568`: parser yields 2079 chars, DB stores 255), and TUM Living's `furtherEquipmentEn` and Kleinanzeigen's `#viewad-description-text` (1–5 KB each) would silently truncate from day one.
- Add a single `kind: Literal['wg', 'flat']` column to `ListingRow` (and field on domain `Listing`) so the matcher can honor `SearchProfile.mode` without parsing detail HTML.
- Switch `ListingRow.id` from a bare external id to a namespaced `f"{source}:{external_id}"` string (still a single `str` PK) so cross-source collisions are structurally impossible.
- Generalize `ScraperAgent` from a hard-coded wg-gesucht loop into a registry of `Source` plugins, one per site. wg-gesucht is the existing implementation rebadged; `tum-living` and `kleinanzeigen` are new modules that follow the verified end-to-end recipes already pinned in their per-site docs.
- Per-source scoping in the deletion sweep so a wg-gesucht pass cannot tombstone Kleinanzeigen rows it never tried to see.
- Frontend impact is one optional `kind?: 'wg' | 'flat'` field on the `Listing` TS type plus a one-chip badge; nothing else changes (URL routing, SSE payloads, drawer hooks all carry the longer string id transparently).
- **Out of scope:** landlord messaging, learned weights, Bedrock swap, frontend visual redesign, non-Munich locality catalogues for Kleinanzeigen / TUM Living (Munich-only at first cut).

## Goal & success criteria (verifiable)

Each criterion is phrased so a reviewer can run a command or SQL query and see "true" or "false":

- **G1.** After one `ScraperAgent.run_once()` cycle, every new `ListingRow.id` matches `^(wg-gesucht|tum-living|kleinanzeigen):.+$`. Verify: `SELECT id FROM listingrow WHERE id NOT REGEXP '^(wg-gesucht|tum-living|kleinanzeigen):' AND first_seen_at > <cycle_start>;` returns 0 rows.
- **G2.** Every `ListingRow` carries a non-null `kind ∈ {'wg', 'flat'}`. Verify: `SELECT count(*) FROM listingrow WHERE kind NOT IN ('wg','flat') OR kind IS NULL;` returns 0.
- **G3.** A user with `SearchProfile.mode = 'flat'` only gets `kind='flat'` listings in their matched view. Verify: `SELECT l.kind, count(*) FROM listingrow l JOIN userlistingrow u ON u.listing_id = l.id WHERE u.username = '<u>' GROUP BY l.kind;` returns one row, `flat`.
- **G4.** The deletion sweep does not tombstone listings from sources it didn't iterate this pass. Verify: with `SCRAPER_ENABLED_SOURCES=wg-gesucht`, after a clean pass, `SELECT count(*) FROM listingrow WHERE id LIKE 'tum-living:%' AND deleted_at IS NOT NULL AND deleted_at > <cycle_start>;` returns 0.
- **G5.** `GET /api/listings/wg-gesucht:12345678?username=alice` returns the same payload shape as `GET /api/listings/12345678?username=alice` did pre-migration (only the `id` differs). Verify with the committed [`docs/_generated/openapi.json`](./_generated/openapi.json) regenerated and a curl against a known id.
- **G6.** SSE stream over `GET /api/users/{u}/stream` continues to deliver `new_listing` / `evaluate` actions whose `listing_id` is the namespaced form, and the frontend drawer (`getListingDetail(listing.id, …)`) opens correctly. Verify: `EventSource` round-trip in browser DevTools, plus a manual click on a listing card in the dashboard.
- **G7.** Each new source has a fixture-driven unit test for `parse_search_page_X` and `parse_listing_page_X` (or the GraphQL equivalent) that is green in CI on a fresh checkout, with no network access. Verify: `pytest backend/tests/scraper/ -v` passes offline.
- **G8.** Live smoke tests succeed when manually run: `SCRAPER_LIVE_TESTS=1 pytest backend/tests/scraper/live/ -v` returns ≥1 listing per source.
- **G9.** No `ListingRow.description` is silently truncated at the database layer. Verify after step 1 has fully cycled: `SELECT count(*) FROM listingrow WHERE scrape_status = 'full' AND CHAR_LENGTH(description) = 255;` returns 0 (the verified wg-gesucht parser emits 800–5000-char descriptions; any row stuck at exactly 255 chars is a leftover from the pre-widen era that never re-scraped). Schema check: `SHOW COLUMNS FROM listingrow LIKE 'description';` reports `text` (not `varchar(255)`).

## Out of scope

- Landlord messaging in any direction (Kleinanzeigen has its own messaging API; not implemented).
- AWS Bedrock swap for the LLM vibe call (still OpenAI per [ADR-017](./DECISIONS.md#adr-017-consolidate-backend-location-intelligence-on-google-maps-platform) era).
- Learned composition weights or 👍/👎 user feedback.
- Non-Munich locality catalogues beyond what already exists in [`models.py CITY_CATALOGUE`](../backend/app/wg_agent/models.py) for wg-gesucht. Kleinanzeigen ships with Munich (`l6411`) only; TUM Living's filter has no per-city axis (the corpus is all-TUM cities).
- Frontend visual redesign: the change is a one-chip badge plus an optional type field, not a new screen.
- Refactoring the existing wg-gesucht parser. Surgical change: rebadge as a `Source` plugin; do not "improve" `parse_search_page` / `parse_listing_page`.
- Persisting the extra TUM Living fields (`rent`, `incidentalCosts`, `floor`, `housingType`, seven `seekingX` booleans, …) into a new `ListingRow.extras` JSON column. Out of scope for first cut; ignored at scrape time, harvestable in a follow-up.
- wg-gesucht **flat** vertical: the category id is **not verified** in this repo (see [`../backend/app/scraper/SOURCE_WG_GESUCHT.md`](../backend/app/scraper/SOURCE_WG_GESUCHT.md) TODO #3). The plan implements the source contract and registers it as `kind_supported = {'wg'}` only; flat support is queued as a follow-up that requires live recon (see [Known risks / unknowns](#known-risks--unknowns)).

## Open design decisions

> **D-1. Migration order.**
>
> Recommendation: land changes in this order (one PR per step):
>
> 1. **Widen text columns to `TEXT` and force-rescrape every existing row** (D-11 below). Pure schema + one-shot SQL; no Python changes. Hard prerequisite for any new source because the same column hosts wg-gesucht / tum-living / kleinanzeigen descriptions.
> 2. `kind` column on `ListingRow` + field on domain `Listing`, defaulting to `'wg'` (additive, indexed). wg-gesucht parser hardcodes `kind='wg'` for the existing path. Frontend stays unchanged.
> 3. Switch wg-gesucht id emission to `f"wg-gesucht:{numeric_id}"` and run the one-shot `UPDATE` SQL on the shared RDS (D-2). FK columns updated in the same transaction.
> 4. Add the `Source` protocol and three per-source modules (`wg-gesucht` extracted from existing `browser.py`, `tum-living` and `kleinanzeigen` new). wg-gesucht's plugin is a thin shim over the existing functions — surgical, no behavioral change.
> 5. Generalize `ScraperAgent` to iterate a list of `Source` instances. Plumb `SCRAPER_ENABLED_SOURCES` env var with default `wg-gesucht` so the existing prod cadence is unchanged unless explicitly opted in.
> 6. Per-source scoping for `repo.list_active_listing_ids(source=...)` and the deletion sweep.
> 7. Wire `SearchProfile.mode` filtering in `repo.list_scorable_listings_for_user`.
> 8. Frontend: add `kind?: 'wg' | 'flat'` to `Listing` TS type and one chip in `ListingList` / `ListingDrawer`.
> 9. Add the two ADRs (ADR-020 + ADR-021) to [`DECISIONS.md`](./DECISIONS.md).
>
> Alternatives considered: doing (2) and (3) in one PR; doing (4) before (3); deferring step 1 until after the new sources land. All rejected — (2) and (3) are independent transactions on the shared RDS and bisecting a regression is easier with one PR each; doing (4) before (3) means the new sources would write namespaced ids while the wg-gesucht path writes bare ids, creating an inconsistent pool that violates G1; deferring step 1 means the new sources' first writes would be silently truncated to 255 chars, requiring a second round-trip to fix and a second rescrape (worse).
>
> Why this order: at every step the system still serves the existing user. Step 1 is the only one that briefly changes existing behavior (status reset triggers re-scrape) and must come first because it relaxes a constraint downstream sources rely on. Step 2 is purely additive. Step 3 is a one-shot rewrite with no functional change to the wg-gesucht behavior. Step 4 is invisible until step 5 enables it. Reversing (2) and (3) would leave the new `kind` column unpopulated for wg-gesucht rows (default applies, but you can't tell from the column whether it was set deliberately), and reversing (5) and (6) would let the deletion sweep tombstone every Kleinanzeigen row on the first wg-gesucht-only pass that lands in prod.
>
> Affected files / interfaces: see per-step PR list in [Migration / rollout sequence](#migration--rollout-sequence).

> **D-2. Namespaced-id migration of existing rows.**
>
> Recommendation: **one-shot SQL** at cutover (option (i) in the prompt). Land step 3 of the migration with this exact transaction on the shared AWS RDS, run by hand by the deploying engineer (no Alembic — see [ADR-019](./DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)):
>
> ```sql
> START TRANSACTION;
>
> -- FKs reference listingrow.id, so update children first while parents are still
> -- unique, then update the parent. MySQL ON UPDATE CASCADE is NOT declared on
> -- these FKs (see db_models.py: foreign_key="listingrow.id" creates the
> -- constraint without an ON UPDATE clause), so we update by hand.
> UPDATE photorow       SET listing_id = CONCAT('wg-gesucht:', listing_id)
>   WHERE listing_id NOT LIKE '%:%';
> UPDATE userlistingrow SET listing_id = CONCAT('wg-gesucht:', listing_id)
>   WHERE listing_id NOT LIKE '%:%';
> UPDATE useractionrow  SET listing_id = CONCAT('wg-gesucht:', listing_id)
>   WHERE listing_id IS NOT NULL AND listing_id NOT LIKE '%:%';
> UPDATE listingrow     SET id         = CONCAT('wg-gesucht:', id)
>   WHERE id NOT LIKE '%:%';
>
> COMMIT;
> ```
>
> Cutover risk: the scraper writes `ListingRow` and the matcher writes `UserListingRow`. Stop both containers (`docker compose stop backend scraper`) before running the SQL; restart after. Window is one transaction (sub-second on a few thousand rows).
>
> Rollback: re-run the inverse `UPDATE … SET id = SUBSTRING(id, LENGTH('wg-gesucht:') + 1) WHERE id LIKE 'wg-gesucht:%';` (and the same on each child). Re-deploy the previous container image.
>
> Why option (i) over (ii) (code-side fallback): option (ii) sprinkles `if ":" not in id: id = f"wg-gesucht:{id}"` across `repo.py`, `api.py`, `evaluator.py`, the SSE payload builder, and every test fixture, and you have to keep that conditional alive forever. Option (i) is one transaction during a deploy and the code stays clean. The per-row count today is small (development-only RDS, hundreds of listings) so the cost of the table-scan UPDATE is negligible. Option (ii) would also break G1 (the verification query for namespacing).
>
> Alternatives considered: dropping and re-creating the database (the documented dev workflow per [ADR-019](./DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)). Acceptable in dev but not in any environment that has scored `UserListingRow`s a real user wants to keep — the one-shot UPDATE preserves them.
>
> Affected files / interfaces: pure SQL on `listingrow.id`, `photorow.listing_id`, `userlistingrow.listing_id`, `useractionrow.listing_id`. No Python edit needed for the migration itself.

> **D-3. `wg_size` semantics for cross-source parity.**
>
> Recommendation: **`wg_size = total flatmates including the new tenant`**, matching wg-gesucht's `(\d+)er WG` convention which is already what the evaluator's `wg_size_fit` was tuned against ([`evaluator.py`](../backend/app/wg_agent/evaluator.py) lines 261-…). Per source:
>
> - wg-gesucht: keep the existing `(\d+)er WG` regex result. No change.
> - Kleinanzeigen: `wg_size = mitbewohner + 1` (mitbewohner is "existing flatmates", from the `Anzahl Mitbewohner` row in the `addetailslist--detail` block per [`SOURCE_KLEINANZEIGEN.md`](../backend/app/scraper/SOURCE_KLEINANZEIGEN.md#how-to-read-one-listing-detail)). Implementer reads the integer and adds 1.
> - TUM Living: leave `wg_size = None` per [`SOURCE_TUM_LIVING.md`](../backend/app/scraper/SOURCE_TUM_LIVING.md#open-questions--todo). The GraphQL API does not expose flatmate count; `numberOfRooms` on a `SHARED_APARTMENT` is the rooms-in-the-offered-share, not the flatmate count. The `wg_size_fit` evaluator already returns `missing_data=True` when `wg_size is None`, so no scoring regression.
>
> Alternatives considered: (a) make `wg_size = mitbewohner` raw on Kleinanzeigen and bump the evaluator's bands by 1, (b) try to harvest TUM Living flatmate count from the `furtherEquipment` description with a regex. Both rejected: (a) splits semantics into two scales which is exactly what we're consolidating, (b) is unreliable and adds a parser for a field the source doesn't promise.
>
> Why: the evaluator already lives in domain-model space and is unit-tested against the `(\d+)er WG` convention; pulling Kleinanzeigen onto that scale at parse time is a one-line change at one site instead of a structural change to the evaluator.
>
> Affected files / interfaces: per-source `parse_listing_page_X` for Kleinanzeigen; no evaluator change.

> **D-4. Source dispatch in `ScraperAgent`.**
>
> Recommendation: **option (a) — per-source plugin classes implementing a `Source` protocol**. The `ScraperAgent` registers a list and iterates them sequentially per pass. See [Source contract (interface)](#source-contract-interface) for the exact shape.
>
> Alternatives considered:
>
> - **(b) Function-tuple registry.** Rejected: it forces every helper (`looks_like_block_page`, `pacing_seconds`, `kind_supported`) onto module-level constants, which makes per-source state (e.g. Kleinanzeigen's session-scoped cookie jar / TUM Living's CSRF token) awkward and forces global module imports for what is a per-source-instance concern.
> - **(c) Three separate top-level loops, one per source.** Rejected: triplicates `_needs_scrape` / `_sweep_deletions` / refresh logic, defeats the [`README.md`](../backend/app/scraper/README.md) "source-agnostic loop" contract, and makes coordinated pacing impossible.
>
> Why (a): the existing `ScraperAgent.run_once` is already 90% source-agnostic (search → diff → enrich → upsert → sweep). The per-site docs each spec their own search/detail/block-detect/pacing constants. A protocol with five attributes and three async methods covers all of them and is the smallest abstraction that maps 1:1 to what the docs already describe. Per-source `httpx.AsyncClient` (and TUM Living's CSRF cookie jar) lives naturally in instance state.
>
> Affected files / interfaces: new `backend/app/scraper/sources/__init__.py` (registry), new `backend/app/scraper/sources/base.py` (protocol), new `backend/app/scraper/sources/{wg_gesucht,tum_living,kleinanzeigen}.py`, refactor of `backend/app/scraper/agent.py` to consume the registry.

> **D-5. Per-source pacing & cadence.**
>
> Recommendation: each `Source` declares its own constants on the instance, consumed by the loop. Concretely, three numbers from the per-site docs:
>
> ```python
> # Source attributes
> search_page_delay_seconds: float   # sleep between search-result page fetches
> detail_delay_seconds: float        # sleep between detail-page fetches
> max_pages: int                     # search pagination ceiling per kind per pass
> refresh_hours: int                 # re-scrape threshold; overrides SCRAPER_REFRESH_HOURS
> ```
>
> Verified values straight from the docs:
>
> | Source         | search_page_delay | detail_delay | max_pages | refresh_hours |
> | -------------- | ----------------- | ------------ | --------- | ------------- |
> | wg-gesucht     | 1.5               | 1.5          | 2         | 24            |
> | tum-living     | 2.5               | 2.5          | 7 (one filter, 25/page × 167 corpus → 7) | 48 |
> | kleinanzeigen  | 2.5               | 3.5          | 5 (robots.txt cap) | 24 |
>
> The cross-source loop iterates sources sequentially (no interleaving within a pass), so per-source pacing is local. The full-pass cadence stays `SCRAPER_INTERVAL_SECONDS` (default 300s); per-source `refresh_hours` is read by `Source._needs_refresh(row, now)` instead of the global `SCRAPER_REFRESH_HOURS` — that's the smallest change that lets TUM Living tolerate 48h while wg-gesucht stays at 24h.
>
> Alternatives considered: a single `pacing_seconds = max(detail, search)` knob. Rejected because Kleinanzeigen's detail pacing (3.5s) is meaningfully slower than its search pacing (2.5s) and the docs explicitly call this out.
>
> Affected files / interfaces: `backend/app/scraper/sources/base.py` (the `Source` protocol fields), per-source modules (the constants), `backend/app/scraper/agent.py` (consumes them).

> **D-6. Block-page detector per source.**
>
> Recommendation: each `Source` exposes `looks_like_block_page(text: str, status: int) -> bool` and the loop calls it on **every** search page and **every** detail page. On `True`, the source returns the unmodified stub (or empty list) and the loop persists what it has — exactly mirroring `_looks_like_block_page` in `browser.py` today.
>
> Concrete detectors per source (lifted from each per-site doc):
>
> - **wg-gesucht** ([`browser._looks_like_block_page`](../backend/app/wg_agent/browser.py)): keep as-is. The plugin re-exports it.
> - **tum-living**: detect `EBADCSRFTOKEN` (re-mint CSRF and retry once before giving up), detect any GraphQL response with `"errors"` populated and `"data": null`, detect HTTP 5xx. The `/api/me` 404-with-body is the *intended* response and is not a block page (per [`SOURCE_TUM_LIVING.md`](../backend/app/scraper/SOURCE_TUM_LIVING.md#recon-summary-date-2026-04-18)).
> - **kleinanzeigen**: implementation lifted from [`SOURCE_KLEINANZEIGEN.md`](../backend/app/scraper/SOURCE_KLEINANZEIGEN.md#anti-bot-posture): block when none of the positive markers (`article.aditem[data-adid]`, `h1#viewad-title`, `og:url`-starts-with-canonical) are present **and** any negative signal fires (status 403/429, body-shorter-than-5KB on a search/detail URL, regex match on `datadome|please verify you are human|sicherheitsüberprüfung|…`, soft-redirect to homepage detected via `response.url`).
>
> Why per-page and per-detail: the wg-gesucht detector already runs per-detail today (in `parse_listing_page` via `_looks_like_block_page`). Adding a per-search-page check is cheap and lets the loop early-exit a paginated pass instead of grinding through five blocked pages.
>
> Alternatives considered: a single global block-page heuristic. Rejected — the GraphQL TUM Living signature has nothing in common with HTML interstitials.
>
> Affected files / interfaces: per-source modules implement the method; the loop in `agent.py` calls it.

> **D-7. Photos.**
>
> Recommendation: **`PhotoRow.url` accepts opaque URLs** — no schema change. Per source:
>
> - wg-gesucht: existing `_parse_photo_urls` walks `og:image`, `[data-full-image]`, `img[data-src/data-lazy/src]`, `source[srcset]` (cap 12). No change.
> - tum-living: build per-image URL `f"https://living.tum.de/api/image/{img['id']}/1280"`, sort `isPreview=True` first, cap 12. `cover_photo_url = f"https://living.tum.de/api/image/{preview_id}/1280"`. URLs are hot-linkable without auth — verified in [`SOURCE_TUM_LIVING.md`](../backend/app/scraper/SOURCE_TUM_LIVING.md).
> - kleinanzeigen: parse every `<script type="application/ld+json">` block, keep entries with `"@type": "ImageObject"`, dedup by `contentUrl`, cap 12. `cover_photo_url = <meta property="og:image">`. Verified in [`SOURCE_KLEINANZEIGEN.md`](../backend/app/scraper/SOURCE_KLEINANZEIGEN.md).
>
> Per-source filtering of logos/avatars/icons (from the existing `_normalized_photo_url` heuristic) is wg-gesucht-specific and not needed for the new sources — TUM Living's image API only serves listing images; Kleinanzeigen's JSON-LD `ImageObject` blocks are listing-only.
>
> Alternatives considered: shared photo-URL filtering helper. Rejected — premature abstraction. Each source's photo channel has different noise.
>
> Affected files / interfaces: `repo.save_photos` is unchanged; per-source modules build the URL list.

> **D-8. Coordinates.**
>
> Recommendation: each source returns `(lat, lng)` directly when its source layer exposes them; the existing `geocoder.geocode(address or "<district>, <city>")` fallback in `anonymous_scrape_listing` stays wired only for the wg-gesucht code path (where the map-config block can be absent on listings without an embedded map). Per source:
>
> - wg-gesucht: `_parse_map_lat_lng` from `var map_config = {…}`; `geocoder` fallback if absent. No change.
> - tum-living: `coordinates.x` is **latitude** and `coordinates.y` is **longitude** per [`SOURCE_TUM_LIVING.md`](../backend/app/scraper/SOURCE_TUM_LIVING.md#recon-summary-date-2026-04-18) (verified `(48.1184617, 11.5707928)` is Munich). No geocoder fallback needed; if the field is null on a listing, leave `lat=lng=None` and let the matcher's commute step skip it.
> - kleinanzeigen: `<meta property="og:latitude">` / `<meta property="og:longitude">`, street-level precision per the recon. No geocoder fallback needed; the meta tags are present on every listing the recon sampled.
>
> Why no geocoder fallback for the new sources: the per-site docs verified the coordinates are present on every sampled listing, and the geocoder is a paid Google API call we shouldn't invoke speculatively. If a future TUM Living or Kleinanzeigen listing is missing coords, leave them null — `commute_fit` already returns `missing_data=True` when `listing.lat is None`.
>
> Alternatives considered: invoking `geocoder.geocode` defensively for every source. Rejected — adds a paid API call to 100% of listings even though the sample rate of missing coords is 0% in the recon.
>
> Affected files / interfaces: per-source modules return `lat`/`lng` from the source-native field; `anonymous_scrape_listing`'s geocoder call stays wg-gesucht-only.

> **D-9. Frontend impact assessment.**
>
> Recommendation: minimum viable. The matcher already filters by `kind` once `repo.list_scorable_listings_for_user` honors `SearchProfile.mode` (D-3 above), so the user-visible behavior already matches what the wizard captured. The frontend then needs **two cosmetic additions**:
>
> 1. Add `kind?: 'wg' | 'flat'` to the `Listing` TS type ([`frontend/src/types.ts`](../frontend/src/types.ts) line ~80) and to `ListingDTO.kind` on the backend so it ships in JSON. Optional in TS to stay backwards-compatible with old payloads.
> 2. One chip in [`frontend/src/components/ListingList.tsx`](../frontend/src/components/ListingList.tsx) and the drawer header (`ListingDrawer.tsx`): `<Chip>{kind === 'flat' ? 'Whole flat' : 'WG room'}</Chip>`. Mirrors the existing wizard wording in `OnboardingRequirements.tsx` and `Dashboard.tsx`.
>
> No change to:
>
> - URL routing / `getListingDetail(listingId)` — `encodeURIComponent("wg-gesucht:12345")` percent-encodes the colon and FastAPI decodes it back. Verified by spec; no app change.
> - SSE payload structure — `Action.listingId` is already typed as `string | null`; the longer string flows through.
> - Wizard — `mode` is already collected.
>
> Today's gap (correctness, not cosmetics): the matcher does **not** filter by `kind` because the column doesn't exist. Once D-3 lands, `repo.list_scorable_listings_for_user` adds `WHERE kind = sp.mode OR sp.mode = 'both'` and the user-visible behavior matches the wizard's `mode` selection. This is the minimum frontend-relevant change required to honor the wizard contract.
>
> Alternatives considered: kind-driven layout switch (different card style for flats). Rejected — out of scope, and the existing card already handles a missing `wg_size` cleanly.
>
> Affected files / interfaces: `backend/app/wg_agent/dto.py` (`ListingDTO.kind`), `backend/app/wg_agent/repo.py` (the kind filter in `list_scorable_listings_for_user`), `frontend/src/types.ts`, `frontend/src/components/ListingList.tsx`, `frontend/src/components/ListingDrawer.tsx`.

> **D-10. Tests.**
>
> Recommendation: each source ships **two** unit tests against saved fixtures (offline, runs in CI) plus **one** smoke test (gated behind `SCRAPER_LIVE_TESTS=1`, runs against the live source, manual / nightly only).
>
> Test layout:
>
> ```
> backend/tests/scraper/
>   conftest.py                       # loads fixtures from ./fixtures/
>   fixtures/
>     wg_gesucht/
>       search_page_1.html
>       listing_12345678.html
>     tum_living/
>       get_listings.json             # full GraphQL response body
>       get_listing_<uuid>.json
>     kleinanzeigen/
>       search_wg_p1.html
>       search_flat_p1.html
>       detail_3362398693.html
>   test_parse_wg_gesucht.py          # asserts parse_search_page + parse_listing_page
>   test_parse_tum_living.py          # asserts parse_listings_response + parse_listing_response
>   test_parse_kleinanzeigen.py       # asserts parse_search_page_ka + parse_listing_page_ka
>   live/
>     conftest.py                     # skips unless SCRAPER_LIVE_TESTS=1
>     test_live_wg_gesucht.py
>     test_live_tum_living.py
>     test_live_kleinanzeigen.py
> ```
>
> Fixture-capture procedure per source:
>
> - **wg-gesucht.** Already covered by [`backend/tests/test_wg_parser.py`](../backend/tests/test_wg_parser.py) and the existing fixtures in `backend/tests/fixtures/`. Reuse those for the rebadged plugin.
> - **tum-living.** `curl -sS https://living.tum.de/api/me` to get the CSRF, then `curl -sS https://living.tum.de/graphql -H 'Content-Type: application/json' -H "csrf-token: <token>" -b "csrf-token=<cookie>" --data @body.json > backend/tests/scraper/fixtures/tum_living/get_listings.json` with `body.json` set to the verified `GetListings` query. Sample uuid harvested from the response, then re-issue with `GetListingByUUIDWithoutContactInfo`.
> - **kleinanzeigen.** `curl -sS -A "<Chrome UA>" -H "Accept-Language: de-DE,de;q=0.9,en;q=0.8" 'https://www.kleinanzeigen.de/s-auf-zeit-wg/muenchen/c199l6411' > backend/tests/scraper/fixtures/kleinanzeigen/search_wg_p1.html`. Same pattern for `c203l6411` (flat) and one detail URL pulled from the search results. Apply the `re.sub(r"&#(\d+)(?![\d;])", r"&#\1;", html)` patch from [`SOURCE_KLEINANZEIGEN.md`](../backend/app/scraper/SOURCE_KLEINANZEIGEN.md) before persisting if you want the fixture to be parser-clean (or leave it raw and have the test exercise the patch).
>
> Unit-test assertions per source: at least `id` namespacing, `kind`, `url`, `price_eur`, `lat`/`lng`, `description` (or stubbed `furtherEquipmentEn` for TUM Living), and `len(photo_urls) >= 1`. Use the verified sample values from each per-site doc as the expected values.
>
> Smoke tests assert: ≥1 listing returned, every returned listing has the expected `kind`, `id` matches the namespacing regex, `url` is reachable (HEAD request, follow_redirects). Why CI excludes them: live HTTP from a CI runner against external sites is flaky, expensive (rate-limit budget), and gives a false signal when the network is the failure mode.
>
> Alternatives considered: VCR-style replay via `vcrpy`. Rejected as premature — saved fixtures are simpler, easier to inspect, and a frozen-in-time snapshot is a feature for parser stability tests.
>
> Affected files / interfaces: new `backend/tests/scraper/` tree, no changes to existing tests beyond the wg-gesucht plugin rebadge.

> **D-11. Widen text columns from `VARCHAR(255)` to `TEXT`, then force a one-cycle rescrape of every existing row.**
>
> Recommendation: in a single Step-1 PR (sequenced ahead of every other change in the rollout), do **schema** + **forced-rescrape** + **verify** in that order. No Python logic change is needed for the rescrape — the existing `ScraperAgent._needs_scrape` already returns `True` whenever `scrape_status != 'full'` ([`../backend/app/scraper/agent.py`](../backend/app/scraper/agent.py) lines 77–85), so a one-shot SQL `UPDATE` that flips every `'full'` row to `'stub'` is enough to make the very next pass re-fetch every detail page through `parse_listing_page` and write the now-untruncated description.
>
> Schema change: in [`../backend/app/wg_agent/db_models.py`](../backend/app/wg_agent/db_models.py), give every long-text column an explicit `Text` SQL type:
>
> ```python
> from sqlalchemy import Column, JSON, LargeBinary, Text
>
> class ListingRow(SQLModel, table=True):
>     ...
>     url: str = Field(sa_column=Column(Text, nullable=False))
>     title: Optional[str] = Field(default=None, sa_column=Column(Text))
>     city: Optional[str] = Field(default=None, sa_column=Column(Text))
>     district: Optional[str] = Field(default=None, sa_column=Column(Text))
>     address: Optional[str] = Field(default=None, sa_column=Column(Text))
>     description: Optional[str] = Field(default=None, sa_column=Column(Text))
>     scrape_error: Optional[str] = Field(default=None, sa_column=Column(Text))
>     ...
> ```
>
> Why each column: bare `Optional[str]` columns get `VARCHAR(255)` by default from SQLModel/SQLAlchemy on MySQL. `description` is verified (listing `12557568` on wg-gesucht) to be 2079 chars after `parse_listing_page`; the `VARCHAR(255)` column silently truncates to exactly 255 chars on `session.merge` (no error raised under non-strict SQL mode). The same risk applies to TUM Living's `furtherEquipmentEn` (1–4 KB regularly) and Kleinanzeigen's `#viewad-description-text` (1–5 KB). `title`/`city`/`district`/`address`/`url`/`scrape_error` are at lower risk individually but cheap to widen alongside; for `scrape_error` we definitely want it wide enough to hold a Python traceback.
>
> Schema deploy: `SQLModel.metadata.create_all` does NOT alter existing columns ([ADR-019](./DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)) — `create_all` only creates missing tables/columns. Run hand-coded `ALTER TABLE` statements at cutover with the scraper + matcher containers stopped:
>
> ```sql
> -- Step 1a. Widen.
> ALTER TABLE listingrow
>   MODIFY url           TEXT NOT NULL,
>   MODIFY title         TEXT,
>   MODIFY city          TEXT,
>   MODIFY district      TEXT,
>   MODIFY address       TEXT,
>   MODIFY description   TEXT,
>   MODIFY scrape_error  TEXT;
>
> -- Step 1b. Force a one-cycle rescrape of every previously-full row.
> -- _needs_scrape returns True for any row whose status != 'full', so the
> -- next ScraperAgent pass will re-fetch the detail page and overwrite
> -- the truncated description with the untruncated one.
> UPDATE listingrow
>   SET scrape_status = 'stub'
>   WHERE scrape_status = 'full';
> ```
>
> Why `'stub'` (not `'failed'` or a new status): `'stub'` already means "search-card data only, detail not yet enriched" and is the natural state for a row whose description we want re-fetched. `_needs_scrape` short-circuits to `True` on `status != 'full'`, so the rescrape begins on the very next `run_once()` cycle without any code change. Using a new status would require a code change in `_needs_scrape`; using `'failed'` would conflate the rescrape with genuine fetch failures (which `_scrape_and_save` already handles separately).
>
> Cycle-time expectation: with `SCRAPER_INTERVAL_SECONDS=300` and a Munich pool of ~1500 active wg-gesucht listings, one `run_once` deep-scrapes only the listings the search returns each pass (default 2 search pages × ~30 cards = ~60 candidates). So the full rescrape spans **~25 passes ≈ 2 hours** for the wg-gesucht-only pool today. During this window the dashboard's `description` field for stale rows is still the truncated version (no functional regression — the existing user already sees the truncated text); rows are progressively replaced as the loop reaches them. If a faster cutover is needed, temporarily lower `SCRAPER_INTERVAL_SECONDS` and raise `SCRAPER_MAX_PAGES` for one or two passes.
>
> Verification (G9): `SHOW COLUMNS FROM listingrow LIKE 'description';` must report `text`. After ~2 hours of cycling: `SELECT count(*) FROM listingrow WHERE scrape_status = 'full' AND CHAR_LENGTH(description) = 255;` should be 0 (real wg-gesucht descriptions are 800–5000 chars; any row stuck at exactly 255 is a leftover truncated row that hasn't re-cycled yet). A single `description` value over 255 chars proves the widen worked; the count-zero query proves the rescrape completed.
>
> Alternatives considered:
> 1. **Drop & re-create the database** (the documented dev workflow per [ADR-019](./DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)). Acceptable in dev; rejected for prod because it loses every `UserListingRow` (per-user scoring history, including vetoes the user has dismissed).
> 2. **Widen schema only, skip the rescrape.** Rejected: existing rows would carry truncated `description` strings forever (or until `_needs_scrape`'s natural `SCRAPER_REFRESH_HOURS=24` threshold kicked in, but only as listings re-appear in the search; a listing pulled off the search-result first page after the schema change would never re-scrape and would carry the 255-char text indefinitely).
> 3. **`UPDATE listingrow SET scraped_at = NULL WHERE scraped_at IS NOT NULL`** instead of flipping `scrape_status`. Functionally equivalent (`_needs_scrape` checks both), but flipping `scrape_status` is more semantically correct: the row really is now equivalent to a search-card stub until re-enriched.
>
> Affected files / interfaces: [`../backend/app/wg_agent/db_models.py`](../backend/app/wg_agent/db_models.py) (column type changes only — same Python `str` type, just a wider SQL type), one-shot SQL run by hand on RDS. No change to `repo.py`, `parse_listing_page`, or any consumer.

## Schema changes

- **`ListingRow` text columns widened to `TEXT` (D-11).** First, before any other schema change, replace the SQLModel-default `VARCHAR(255)` on every long-text column (`url`, `title`, `city`, `district`, `address`, `description`, `scrape_error`) with an explicit `Column(Text)`. Run hand-coded `ALTER TABLE listingrow MODIFY <col> TEXT` on RDS at cutover (no Alembic per [ADR-019](./DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)), then `UPDATE listingrow SET scrape_status = 'stub' WHERE scrape_status = 'full'` to force the next ScraperAgent cycle to re-fetch every detail page through `parse_listing_page` and overwrite the truncated descriptions. Verified prerequisite: without this, wg-gesucht descriptions land at 255 chars (verified: listing `12557568`, parser yields 2079 chars, DB stores 255), and TUM Living's `furtherEquipmentEn` and Kleinanzeigen's `#viewad-description-text` (both 1–5 KB) would silently truncate from day one.

- **`ListingRow`:** add one indexed column `kind: str` (Pydantic `Literal['wg', 'flat']` at the domain layer, plain `str` in the SQL row). Default `'wg'` so existing rows are valid post-migration. Indexed because `repo.list_scorable_listings_for_user` will filter on it. **No `source` column** — the source is encoded in the `id` prefix and recoverable by `id.split(":", 1)[0]`. State this explicitly: do not add a `source` column. The `id` prefix is the source key.

  ```python
  # backend/app/wg_agent/db_models.py — additive change
  class ListingRow(SQLModel, table=True):
      ...
      kind: str = Field(default="wg", index=True)
      ...
  ```

- **`PhotoRow`, `UserListingRow`, `UserActionRow`:** **no shape change**. Each carries a `listing_id: str` FK that already accepts arbitrary strings. The namespaced id flows through transparently.

- **Migration of existing rows (D-2):** one-shot SQL transaction, executed by hand on the shared AWS RDS at cutover. SQL pseudocode is in D-2 above; risk is one transaction during a deploy window; rollback is the inverse `SUBSTRING` UPDATE; it requires the scraper and matcher containers to be stopped during the transaction (no parallel writes to `ListingRow.id` during the rewrite). This works on MySQL because the `foreign_key=` SQLModel annotations create FKs **without** `ON UPDATE CASCADE`; updates flow child-first, parent-last, all in one transaction so no FK constraint is violated mid-transaction.

- **Frontend `Listing` TS type:** add `kind?: 'wg' | 'flat'` per D-9. Optional to stay backwards-compatible with payloads that pre-date the column. Domain `Listing` field is `kind: Literal['wg', 'flat'] = 'wg'` (default `'wg'` so existing tests / fixtures don't break).

- **`ListingDTO.kind`:** add `kind: Literal['wg', 'flat'] | None = None`. Required so it serializes to JSON. The existing `listing_to_dto` helper passes it through.

## Source contract (interface)

```python
# backend/app/scraper/sources/base.py
"""Source plugin protocol for the multi-source scraper loop.

Each per-site module implements one Source. ScraperAgent registers a list
of Source instances and iterates them sequentially per pass.

Identity / kind invariants:
- Stubs returned by `search` carry the namespaced `id` and final `kind`.
- `scrape_detail` MUST NOT re-key the listing — `id` and `kind` are
  immutable from the moment the stub is built.
"""

from __future__ import annotations

from typing import Literal, Protocol

from ...wg_agent.models import Listing, SearchProfile

Kind = Literal["wg", "flat"]


class Source(Protocol):
    """One scraping source (e.g. wg-gesucht, tum-living, kleinanzeigen)."""

    name: str                          # source token, e.g. "wg-gesucht"
    kind_supported: frozenset[Kind]    # which verticals this source iterates
    search_page_delay_seconds: float   # sleep between search-result page fetches
    detail_delay_seconds: float        # sleep between detail-page fetches
    max_pages: int                     # search pagination ceiling per kind per pass
    refresh_hours: int                 # re-scrape threshold (overrides global)

    async def search(self, *, kind: Kind, profile: SearchProfile) -> list[Listing]:
        """Return one pass of stubs for `kind`. Stubs carry namespaced id + kind."""

    async def scrape_detail(self, stub: Listing) -> Listing:
        """Enrich `stub` with description / coords / photos. Never re-keys id/kind."""

    def looks_like_block_page(self, text: str, status: int) -> bool:
        """True when the response looks like an anti-bot interstitial."""
```

The agent never imports per-source classes by name; it consumes a list `sources: list[Source]` constructed in `backend/app/scraper/sources/__init__.py` from `SCRAPER_ENABLED_SOURCES`.

## Per-source modules

### `backend/app/scraper/sources/wg_gesucht.py`

- **Imports / depends on:** existing [`backend/app/wg_agent/browser.py`](../backend/app/wg_agent/browser.py) for all parsing, anonymous fetching, and the block-page detector. Existing [`models.CITY_CATALOGUE`](../backend/app/wg_agent/models.py).
- **Exposes:** a `WgGesuchtSource` class that satisfies `Source`. `search` delegates to `browser.anonymous_search` with `kind='wg'` only (flat support is queued — see D-1 / [Known risks / unknowns](#known-risks--unknowns)). `scrape_detail` delegates to `browser.anonymous_scrape_listing`. `looks_like_block_page` delegates to `browser._looks_like_block_page` (or its public wrapper).
- **Skeleton:** out of scope to paste — the existing functions are already implemented and verified. The plugin is a 30-line shim.
- **Verified recipe:** [`../backend/app/scraper/SOURCE_WG_GESUCHT.md`](../backend/app/scraper/SOURCE_WG_GESUCHT.md).
- **Constants:** `name="wg-gesucht"`, `kind_supported={'wg'}`, `search_page_delay_seconds=1.5`, `detail_delay_seconds=1.5`, `max_pages=2`, `refresh_hours=24`.

### `backend/app/scraper/sources/tum_living.py`

- **Imports / depends on:** `httpx`, the new GraphQL constants pinned in this module (`LISTINGS_QUERY` and `DETAIL_QUERY` from the verified end-to-end recipe), `from ...wg_agent.models import Listing`.
- **Exposes:** a `TumLivingSource` class. Internal state: one long-lived `httpx.AsyncClient` with the CSRF cookie jar and the `csrf-token` header set after the first `GET /api/me`. Re-mints CSRF on `EBADCSRFTOKEN` and retries once.
- **Skeleton:**

  ```python
  # backend/app/scraper/sources/tum_living.py
  """TUM Living GraphQL scraper plugin (Source protocol implementation)."""

  from __future__ import annotations

  from typing import Literal

  import httpx

  from ...wg_agent.models import Listing, SearchProfile
  from .base import Kind, Source

  BASE_URL = "https://living.tum.de"
  USER_AGENT = (
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
  )
  LISTINGS_QUERY = "..."   # verbatim from SOURCE_TUM_LIVING.md
  DETAIL_QUERY = "..."     # verbatim from SOURCE_TUM_LIVING.md


  class TumLivingSource:
      """GraphQL-backed source. Mints a CSRF pair lazily; re-mints on EBADCSRFTOKEN."""

      name = "tum-living"
      kind_supported = frozenset({"wg", "flat"})
      search_page_delay_seconds = 2.5
      detail_delay_seconds = 2.5
      max_pages = 7
      refresh_hours = 48

      def __init__(self) -> None:
          self._client: httpx.AsyncClient | None = None
          self._csrf: str | None = None

      async def search(self, *, kind: Kind, profile: SearchProfile) -> list[Listing]:
          """One paginated GraphQL search filtered by `type`."""

      async def scrape_detail(self, stub: Listing) -> Listing:
          """POST GetListingByUUIDWithoutContactInfo and merge response into stub."""

      def looks_like_block_page(self, text: str, status: int) -> bool:
          """True on EBADCSRFTOKEN body, GraphQL errors-with-null-data, or HTTP 5xx."""
  ```

- **Verified recipe:** [`../backend/app/scraper/SOURCE_TUM_LIVING.md`](../backend/app/scraper/SOURCE_TUM_LIVING.md) (search the `## Verified end-to-end recipe` section — copy-paste-ready).
- **`kind` mapping:** GraphQL `type == "SHARED_APARTMENT"` → `kind='wg'`; `type ∈ {"APARTMENT", "HOUSE"}` → `kind='flat'`. Iterate two filtered passes per cycle (`filter: {"type": "SHARED_APARTMENT"}` and `filter: {"type": "APARTMENT"}` — `HOUSE` is rare and folded into the `flat` pass via a second filter or surfaced opportunistically; implementer's choice).

### `backend/app/scraper/sources/kleinanzeigen.py`

- **Imports / depends on:** `httpx`, `bs4`, `re`, `json`, `from ...wg_agent.models import Listing`. Mirrors the style of `_anon_client` / `parse_search_page` / `parse_listing_page` in [`browser.py`](../backend/app/wg_agent/browser.py).
- **Exposes:** a `KleinanzeigenSource` class with one `httpx.AsyncClient` per pass (cookie jar persisted across search and detail fetches). Implements the homepage warm-up, the `&#8203` charref patch, and the JSON-LD photo walk.
- **Skeleton:**

  ```python
  # backend/app/scraper/sources/kleinanzeigen.py
  """kleinanzeigen.de scraper plugin (Source protocol implementation)."""

  from __future__ import annotations

  import re

  import httpx

  from ...wg_agent.models import Listing, SearchProfile
  from .base import Kind, Source

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
  _BAD_CHARREF = re.compile(r"&#(\d+)(?![\d;])")  # patches the &#8203 zero-width space
  KA_LOCALITY_BY_CITY = {"München": 6411, "Muenchen": 6411}  # extend as needed


  class KleinanzeigenSource:
      """Anonymous httpx + bs4 source for Kleinanzeigen."""

      name = "kleinanzeigen"
      kind_supported = frozenset({"wg", "flat"})
      search_page_delay_seconds = 2.5
      detail_delay_seconds = 3.5
      max_pages = 5
      refresh_hours = 24

      async def search(self, *, kind: Kind, profile: SearchProfile) -> list[Listing]:
          """Paginated search of the WG (c199) or flat (c203) vertical for the city."""

      async def scrape_detail(self, stub: Listing) -> Listing:
          """Fetch the detail page, parse all verified selectors, fill the listing."""

      def looks_like_block_page(self, text: str, status: int) -> bool:
          """True on Akamai/DataDome interstitial, soft-redirect to homepage, or 4xx."""
  ```

- **Verified recipe:** [`../backend/app/scraper/SOURCE_KLEINANZEIGEN.md`](../backend/app/scraper/SOURCE_KLEINANZEIGEN.md) (search the `## Verified end-to-end recipe` section — copy-paste-ready, including the `_BAD_CHARREF` patch and the JSON-LD photo walk).
- **`kind` mapping:** scraper sets `kind` from the URL vertical it iterated (`c199` → `wg`, `c203` → `flat`). The detail page is not parsed for kind; it carries the kind from the search stub.

## Loop generalization (`ScraperAgent`)

Diff to [`backend/app/scraper/agent.py`](../backend/app/scraper/agent.py), in prose:

- Replace `self._city / self._max_rent / self._max_pages` with a list `self._sources: list[Source]` constructed from `SCRAPER_ENABLED_SOURCES` (env-driven).
- Replace `self._search_profile()` with one `SearchProfile` per source, built from the same env vars (the per-source `max_pages` and `refresh_hours` come from the source itself, not env).
- `run_once()` now iterates `self._sources` sequentially. For each source, for each `kind in source.kind_supported`, call `source.search(kind=kind, profile=sp)` → get stubs → for each stub, `_needs_scrape(existing)` → `_scrape_and_save_via(source, stub)`. After all sources complete, the deletion sweep runs once per source (not once globally) — see below.
- `_scrape_and_save` becomes `_scrape_and_save_via(source, stub)` and calls `source.scrape_detail(stub)` instead of the wg-gesucht-specific `browser.anonymous_scrape_listing`. Block-page detection is delegated: `source.looks_like_block_page(text, status)` returns `True` → persist the stub with `scrape_status='stub'`, no enriched fields. The `kind` propagates from `stub.kind` into the row.
- `_needs_scrape` consults `source.refresh_hours` instead of the global `SCRAPER_REFRESH_HOURS` for the cutoff. Existing semantics otherwise unchanged.
- `_sweep_deletions` is **per-source-scoped**. Today it diffs `repo.list_active_listing_ids()` (global) against the seen set from one search pass, which would tombstone every other-source listing that wasn't visited this pass. The fix: extend `repo.list_active_listing_ids(source: str)` to filter by `id LIKE f"{source}:%"`, and the sweep diffs that source's actives against that source's seen set. The miss-counter dict (`self._missing_passes`) becomes per-source-keyed: `dict[str, dict[str, int]]` keyed by `source.name`.

Env-var changes:

- **New:** `SCRAPER_ENABLED_SOURCES` (comma-separated; default `wg-gesucht`). Plumbed into `ScraperAgent.__init__` and used to build the registry.
- **No change** to `SCRAPER_INTERVAL_SECONDS`, `SCRAPER_DELETION_PASSES`, `SCRAPER_CITY`, `SCRAPER_MAX_RENT`. `SCRAPER_REFRESH_HOURS` becomes a per-source default override (kept for backwards compat with deployments that already set it).

Backwards compatibility: with the env-var default `SCRAPER_ENABLED_SOURCES=wg-gesucht`, today's deployment runs exactly the wg-gesucht-only loop it runs now (one source iterated, one kind, one search). Adding `tum-living,kleinanzeigen` to the env var opts in.

## Repo / API / SSE impact

- **`repo.upsert_global_listing`:** add a `kind: str` keyword arg. Defaults to `'wg'` so the existing wg-gesucht call site (which doesn't pass it yet) keeps working through the migration. After D-1 step 4, callers always pass it.
- **`repo.list_scorable_listings_for_user`:** add `WHERE kind = sp.mode` filter when `sp.mode != 'both'`. Needs the `SearchProfile.mode` argument or it has to look up the profile internally. Recommendation: pass the `SearchProfile` (or just `mode`) as a kw arg so the caller (`UserAgent.run_match_pass` in [`periodic.py`](../backend/app/wg_agent/periodic.py)) is explicit. This is the minimum change that delivers G3.
- **`repo.list_active_listing_ids`:** add `source: str | None = None` arg. When set, filter by `id LIKE f"{source}:%"`. Default `None` preserves the global behavior so the function stays usable for non-sweep callers.
- **`api.GET /api/listings/{listing_id}`:** **no change.** The path param is a string; the percent-encoded colon in `wg-gesucht:12345` is decoded transparently by FastAPI and matched against `session.get(ListingRow, listing_id)`. Confirm at G5 verification time with a manual curl.
- **SSE payloads:** add `kind` to the listing payload that surfaces in `Action.detail` if the frontend wants to chip it from the action log. The `listing_id` field stays the same shape (now a longer string). The `Action` type already accepts an opaque `listingId: string | null`.

## Frontend impact

- **[`frontend/src/types.ts`](../frontend/src/types.ts):** add `kind?: 'wg' | 'flat'` to the `Listing` type (line ~80, alongside `wgSize`).
- **[`frontend/src/components/ListingList.tsx`](../frontend/src/components/ListingList.tsx) + `ListingDrawer.tsx`:** one chip rendering `{kind === 'flat' ? 'Whole flat' : 'WG room'}`. Use the existing `<Chip>` primitive (per [`docs/DESIGN.md`](./DESIGN.md)). Hide the chip when `kind` is undefined to stay backwards-compatible.
- **Wizard:** no change. `mode` is already collected; the new matcher filter (D-9) consumes it.
- **`useDrawer(listingId: string)` / URL building:** no change. The drawer already opens via `getListingDetail(listing.id, username)` ([`api.ts`](../frontend/src/lib/api.ts) line 297) which uses `encodeURIComponent` — works for any string id.

## Migration / rollout sequence

Numbered, dependency-ordered. At every step the system still serves the existing user.

1. **Widen `ListingRow` text columns to `TEXT` and force-rescrape every existing row (D-11).** Edit [`../backend/app/wg_agent/db_models.py`](../backend/app/wg_agent/db_models.py) to give `url`, `title`, `city`, `district`, `address`, `description`, `scrape_error` an explicit `Column(Text)` SQL type. Run on RDS at cutover (scraper + matcher containers stopped):
   ```sql
   ALTER TABLE listingrow
     MODIFY url TEXT NOT NULL, MODIFY title TEXT, MODIFY city TEXT,
     MODIFY district TEXT, MODIFY address TEXT, MODIFY description TEXT,
     MODIFY scrape_error TEXT;
   UPDATE listingrow SET scrape_status = 'stub' WHERE scrape_status = 'full';
   ```
   - Verify by (G9): `SHOW COLUMNS FROM listingrow LIKE 'description';` reports `text`. After ~2 hours of cycling (~25 passes at default cadence): `SELECT count(*) FROM listingrow WHERE scrape_status = 'full' AND CHAR_LENGTH(description) = 255;` returns 0; `SELECT max(CHAR_LENGTH(description)) FROM listingrow WHERE scrape_status = 'full';` returns >> 255 (real wg-gesucht descriptions are 800–5000 chars).
   - Depends on: nothing. **Hard prerequisite for steps 2–10 because adding new sources to a `VARCHAR(255)`-bound `description` column is what we're trying to avoid.**

2. **Add `kind` to domain `Listing` and `ListingRow` (default `'wg'`, indexed).**
   - Verify by: `python -c "from backend.app.wg_agent.db_models import ListingRow; print(ListingRow.__fields__['kind'])"`; on the shared RDS, `SHOW INDEX FROM listingrow WHERE Column_name = 'kind'` returns 1 row.
   - Depends on: step 1 (so we don't run two `ALTER TABLE` cutovers back-to-back; combine the `kind` column add into the same maintenance window).

3. **One-shot SQL migration (D-2): namespace existing rows.**
   - Verify by: `SELECT count(*) FROM listingrow WHERE id NOT LIKE 'wg-gesucht:%';` returns 0 (G1 passes immediately for the wg-gesucht-only existing pool); same for the three FK tables.
   - Depends on: step 2 (so the `kind` column exists when the post-migration scraper writes new rows). Cutover requires `docker compose stop backend scraper`, run SQL, `docker compose up -d`.

4. **Switch wg-gesucht parser to emit namespaced ids; rebadge as a `Source` plugin.**
   - Verify by: `pytest backend/tests/test_wg_parser.py` (existing fixture tests get updated to the namespaced form); fresh `ScraperAgent.run_once()` writes ids like `wg-gesucht:12345678`.
   - Depends on: steps 2 + 3.

5. **Add the `Source` protocol + new modules `tum_living.py` and `kleinanzeigen.py` (parsers only, not yet registered).**
   - Verify by: `pytest backend/tests/scraper/ -v` (offline fixtures only) is green.
   - Depends on: step 4 (so the wg-gesucht plugin shape is canonical to mirror).

6. **Generalize `ScraperAgent` to consume the registry; add `SCRAPER_ENABLED_SOURCES` (default `wg-gesucht`).**
   - Verify by: with the default env, `ScraperAgent.run_once()` writes only `wg-gesucht:%` ids; with `SCRAPER_ENABLED_SOURCES=wg-gesucht,tum-living,kleinanzeigen`, the next pass writes ids prefixed by all three.
   - Depends on: steps 4 + 5.

7. **Per-source scoping: `repo.list_active_listing_ids(source=...)` and per-source `_missing_passes`.**
   - Verify by: G4 (a wg-gesucht-only pass does not tombstone Kleinanzeigen rows).
   - Depends on: step 6.

8. **Wire `SearchProfile.mode` into `repo.list_scorable_listings_for_user`.**
   - Verify by: G3 (`SELECT kind, count(*) FROM listingrow JOIN userlistingrow USING (id) WHERE userlistingrow.username = '<u>' GROUP BY kind` for a user with `mode='flat'` returns one row).
   - Depends on: step 2 (the column must exist) and step 7 (so a flat user doesn't see soft-deleted Kleinanzeigen rows).

9. **Frontend: `kind?` in TS types + one chip in `ListingList` / `ListingDrawer`. `ListingDTO.kind` on the backend.**
   - Verify by: open the dashboard, see the chip on at least one wg-gesucht-tagged card.
   - Depends on: step 6 (so the column is populated and the DTO can serialize it).

10. **Add ADR-020 + ADR-021 to [`DECISIONS.md`](./DECISIONS.md). Regenerate [`docs/_generated/openapi.json`](./_generated/openapi.json) (since `ListingDTO` gained a field).**
    - Verify by: `grep -c "ADR-02[01]" docs/DECISIONS.md` returns 2; `git diff docs/_generated/openapi.json` shows the `kind` field on `ListingDTO`.
    - Depends on: step 9.

11. **Enable the new sources in production.** Set `SCRAPER_ENABLED_SOURCES=wg-gesucht,tum-living,kleinanzeigen` on the deployed `scraper` container; restart.
    - Verify by: G1 + G2 (after one cycle, every new row has `kind` set and a namespaced id from one of the three sources).
    - Depends on: every previous step. This is the only step that changes user-visible behavior in prod.

## Test plan

**Unit fixtures per source.** Capture procedure detailed in D-10 above. Files under `backend/tests/scraper/fixtures/<source>/`. Assertions per source:

- **wg-gesucht** (`test_parse_wg_gesucht.py`): existing assertions in [`test_wg_parser.py`](../backend/tests/test_wg_parser.py) plus one new check: every parsed `id` matches `^wg-gesucht:\d{5,9}$`, every parsed listing has `kind == 'wg'`.
- **tum-living** (`test_parse_tum_living.py`): every stub from `parse_listings_response` has `id` matching `^tum-living:[0-9a-f-]{36}$`, `kind ∈ {'wg', 'flat'}` mapped from `type`, `lat / lng` from `coordinates.x / y`, `url` matches `https://living.tum.de/listings/<uuid>/view`. Detail pass: `description` from `furtherEquipmentEn`, `photo_urls` count > 0.
- **kleinanzeigen** (`test_parse_kleinanzeigen.py`): every search-card stub matches `^kleinanzeigen:\d+$`, `lat`/`lng` from `og:latitude` / `og:longitude` meta tags on the detail page, `cover_photo_url` from `og:image`, JSON-LD `ImageObject` walk yields ≥1 photo. Block-page detector test: pass a synthetic 403 + datadome HTML, assert `True`.

**Smoke tests** (`backend/tests/scraper/live/`, gated behind `SCRAPER_LIVE_TESTS=1`). One per source. Each test:

1. Instantiates the source.
2. Calls `await source.search(kind='wg', profile=SearchProfile(city='München', max_rent_eur=2000))`.
3. Asserts ≥1 stub returned, every stub's id is namespaced, every stub's `kind == 'wg'`.
4. Calls `source.scrape_detail(stubs[0])` and asserts at least `description is not None and lat is not None`.
5. For Kleinanzeigen and TUM Living: also exercises `kind='flat'`.

Why CI excludes them: live HTTP from CI runners against external sites is flaky (rate-limited GitHub Actions IP ranges), expensive (uses up our daily budget), and gives false signals when the network is the failure mode rather than the parser. Manual / nightly cadence is the documented contract.

**Pre-merge manual checklist for the reviewer:**

- [ ] **Step 1 done (text columns widened + rescrape complete):** `SHOW COLUMNS FROM listingrow LIKE 'description';` reports `text`. `SELECT count(*) FROM listingrow WHERE scrape_status = 'full' AND CHAR_LENGTH(description) = 255;` returns 0 (G9). Spot-check one previously-truncated wg-gesucht listing has its full description back.
- [ ] `pytest backend/tests/scraper/ -v` green (offline).
- [ ] `SCRAPER_LIVE_TESTS=1 pytest backend/tests/scraper/live/ -v` green (manual run, document timestamp).
- [ ] `python -m app.scraper.main` starts cleanly with `SCRAPER_ENABLED_SOURCES=wg-gesucht,tum-living,kleinanzeigen` (manually `Ctrl-C` after one cycle; check the logs show three sources iterated).
- [ ] Manual SQL spot-check: `SELECT id, kind FROM listingrow ORDER BY first_seen_at DESC LIMIT 30;` shows ids from all three prefixes and `kind` set.
- [ ] Frontend dashboard: at least one card per source shows the kind chip; clicking opens the drawer correctly (URL contains the percent-encoded colon `%3A`).
- [ ] [`docs/_generated/openapi.json`](./_generated/openapi.json) regenerated and committed.

## ADR drafts

> **Note on numbering.** The repo's existing ADR log goes up to **ADR-019: Drop Alembic, use `SQLModel.metadata.create_all`** ([`./DECISIONS.md`](./DECISIONS.md)). The original prompt suggested ADR-019 / ADR-020 for these new entries; that collides. The drafts below use **ADR-020** and **ADR-021** instead.

### ADR-020: Multi-source listing identifiers via string namespacing

- **Date:** TBD (commit date)
- **Status:** Accepted (pending merge)

**Context:** WG Hunter is moving from one scraper source (`wg-gesucht`) to three (`wg-gesucht`, `tum-living`, `kleinanzeigen`). Each source has its own external id namespace: wg-gesucht uses 5–9 digit numbers, TUM Living uses UUIDs, Kleinanzeigen uses ~10 digit numbers. The id namespaces don't structurally collide today (different lengths, different alphabets) but nothing prevents a future Kleinanzeigen id from also being a valid wg-gesucht id, and the existing single-column `ListingRow.id: str` PK has no way to distinguish them. We needed an identifier that (a) makes cross-source collisions structurally impossible, (b) lets `repo.upsert_global_listing` keep its `session.get(ListingRow, id)` then `session.merge(row)` shape, (c) avoids changing every API URL, SSE payload, and frontend `listingId` reference.

**Decision:** Encode the source as a prefix on the existing string PK: `ListingRow.id = f"{source}:{external_id}"` where `source ∈ {wg-gesucht, tum-living, kleinanzeigen}`. The PK stays a single `str` column. The source is recoverable from any code path via `id.split(":", 1)[0]`. Existing wg-gesucht rows are migrated by a one-shot SQL `UPDATE … SET id = CONCAT('wg-gesucht:', id)` plus matching FK column updates on `photorow.listing_id`, `userlistingrow.listing_id`, `useractionrow.listing_id`, executed by hand at cutover (no Alembic, per [ADR-019](#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)). New sources emit the namespaced form from day one.

**Consequences:** Zero schema change beyond the migration UPDATE — the `id: str` column stays put. Zero change to API URLs (`/api/listings/{listing_id}` accepts the longer string after percent-encoding the colon, which `encodeURIComponent` does automatically and FastAPI decodes back transparently). Zero change to SSE payload structure — `Action.listingId` is already an opaque string. Zero change to `repo.upsert_global_listing`'s dedup logic — the longer string dedups the same way. Trade-off: we lose the ability to query "all listings from source X" without a `LIKE 'X:%'` scan; if that ever becomes hot, a partial-index workaround or a derived `source` column is one additive migration away. We considered (and rejected) a composite `(source, external_id)` PK — it would force changes to every API route signature, every SSE payload, every frontend type.

### ADR-021: Listing kind as a first-class column

- **Date:** TBD (commit date)
- **Status:** Accepted (pending merge)

**Context:** WG Hunter scrapes both shared rooms (WG) and full apartments. The existing `SearchProfile.mode: Literal['wg', 'flat', 'both']` was wired in the wizard months ago, but the matcher could never honor it because nothing on `ListingRow` told us what kind the listing was. Two options: infer at read time from the listing's source URL pattern (`/wg-zimmer-in-…` vs `/s-mietwohnung/…`), or persist the kind explicitly. Inferring at read time is fragile (each source has its own URL pattern, the regex would have to live in `repo.py` and stay in sync with three scraper modules), forces a per-source URL parser into a layer that doesn't otherwise know about sources, and runs a regex on every listing on every read.

**Decision:** Add `kind: Literal['wg', 'flat']` as an indexed column on `ListingRow` (default `'wg'` for the existing wg-gesucht-only pool) and as a field on the domain `Listing` model. Each per-source scraper sets `kind` from the search vertical it iterated — the listing-detail page does not need to be parsed to determine kind. The matcher's `repo.list_scorable_listings_for_user` filters by `kind = sp.mode` when `sp.mode != 'both'`, finally honoring the wizard's `mode` selection. Frontend gets one optional `kind?: 'wg' | 'flat'` field on the TS `Listing` type and one chip in the listing card / drawer.

**Consequences:** The matcher honors `SearchProfile.mode` for the first time. Indexed lookup for the `WHERE kind = sp.mode` filter means the read cost is essentially free. Schema change is one additive column on one table — existing rows default to `'wg'` so the migration is invisible. Trade-off: every per-source scraper has to remember to set `kind` correctly; the protocol enforces it by making `kind` part of the search-stub return value (immutable from stub creation through `scrape_detail`, per the `Source` protocol). We considered (and rejected) inferring kind from `id` prefix at read time — it doesn't work for sources like Kleinanzeigen that serve both kinds under the same id namespace.

## Known risks / unknowns

- **Step 1 rescrape window: stale truncated descriptions persist for ~2 hours.** The `UPDATE listingrow SET scrape_status = 'stub'` in step 1 takes effect immediately, but `_scrape_and_save` only re-fetches listings the next search pass returns. With `SCRAPER_INTERVAL_SECONDS=300` and ~60 listings re-scraped per pass, the full pool refreshes in ~25 passes ≈ 2 hours. During this window, the dashboard's `description` field for not-yet-recycled rows is still the truncated text — no functional regression vs today, but G9 will report a non-zero count of 255-char rows during the window. **Mitigation:** if a faster cutover is needed, temporarily lower `SCRAPER_INTERVAL_SECONDS` to 60 and raise `SCRAPER_MAX_PAGES` to 10 for one or two hours, then revert.
- **wg-gesucht flat-vertical category id is unverified.** [`SOURCE_WG_GESUCHT.md`](../backend/app/scraper/SOURCE_WG_GESUCHT.md) TODO #3 explicitly says the `Wohnungen` / `1-Zimmer-Wohnungen` / `Häuser` category ids are not confirmed against the live site. The plan implements `WgGesuchtSource.kind_supported = {'wg'}` only; flat support requires a live recon (browse the live wg-gesucht filters with the cursor-ide-browser MCP, harvest the right slug + category id, add a `build_flat_search_url` helper). **Mitigation:** call this out in the rollout — Kleinanzeigen and TUM Living cover the flat vertical; users with `mode='flat'` will see flats from those two sources only. Recon task is queued in [`docs/ROADMAP.md`](./ROADMAP.md) (a follow-up, not part of this plan).
- **Anti-bot escalation on Kleinanzeigen.** [`SOURCE_KLEINANZEIGEN.md`](../backend/app/scraper/SOURCE_KLEINANZEIGEN.md) verified clean responses across 5 paginated requests + 3 detail requests today, but warned Akamai can escalate. **Mitigation:** the documented `curl_cffi` warm-up fallback (TLS+HTTP2 fingerprint impersonation) is the next escalation rung, plus exponential backoff on 4xx. Monitor first 4xx after rollout; revisit if the per-pass success rate drops below 95%.
- **TUM Living CSRF lifetime is unmeasured.** [`SOURCE_TUM_LIVING.md`](../backend/app/scraper/SOURCE_TUM_LIVING.md) mints a fresh CSRF pair via `GET /api/me` per `httpx.AsyncClient` lifetime; whether the same pair survives many minutes / hours of POSTs is not measured. **Mitigation:** the `looks_like_block_page` detector returns `True` on `EBADCSRFTOKEN`; the loop catches that and re-mints once before giving up. The `/api/me` round-trip is cheap.
- **TUM Living GraphQL schema can change without notice.** It's an active TUM project. **Mitigation:** pin the verified `LISTINGS_QUERY` and `DETAIL_QUERY` strings as constants in the source module; the smoke test asserts the response keys (`uuid`, `type`, `coordinates.x`, …) are present so a schema break fails fast at scrape time rather than at evaluation time.
- **MySQL one-shot UPDATE during cutover requires the scraper + matcher to be stopped.** Otherwise concurrent writes to `listingrow.id` race the migration. **Mitigation:** documented in D-2's rollback story; takes a sub-second transaction window. Run the SQL during a planned deploy.
- **No ADR collision check exists in CI.** The plan's ADR drafts re-number to ADR-020 / ADR-021 because ADR-019 is taken. **Mitigation:** reviewer checks the ADR number against the latest entry in [`DECISIONS.md`](./DECISIONS.md) before merging.

## See also

- [`../backend/app/scraper/README.md`](../backend/app/scraper/README.md) — multi-source contract: id namespacing, `kind` column, dedup invariant.
- [`../backend/app/scraper/SOURCE_WG_GESUCHT.md`](../backend/app/scraper/SOURCE_WG_GESUCHT.md) — wg-gesucht recon + wired-today notes + TODOs.
- [`../backend/app/scraper/SOURCE_TUM_LIVING.md`](../backend/app/scraper/SOURCE_TUM_LIVING.md) — TUM Living verified GraphQL schema + end-to-end recipe.
- [`../backend/app/scraper/SOURCE_KLEINANZEIGEN.md`](../backend/app/scraper/SOURCE_KLEINANZEIGEN.md) — Kleinanzeigen verified DOM selectors + end-to-end recipe.
- [`./DATA_MODEL.md`](./DATA_MODEL.md) — every table, the three-layer rule (UI ↔ DTO ↔ domain ↔ row).
- [`./ARCHITECTURE.md`](./ARCHITECTURE.md) — runtime shape of the scraper container alongside the FastAPI matcher.
- [`./DECISIONS.md`](./DECISIONS.md) — ADR log; ADR-020 + ADR-021 land here once this plan is approved.
- [`../backend/app/wg_agent/repo.py`](../backend/app/wg_agent/repo.py) — `upsert_global_listing` (sole writer of `ListingRow.id`), `list_scorable_listings_for_user` (will gain the `kind` filter).
- [`../backend/app/wg_agent/db_models.py`](../backend/app/wg_agent/db_models.py) — `ListingRow` (gains `kind`), `PhotoRow` / `UserListingRow` / `UserActionRow` (FK columns updated by D-2 SQL).
- [`../backend/app/wg_agent/browser.py`](../backend/app/wg_agent/browser.py) — reference implementation that the new sources mirror in style (`_anon_client`, `parse_search_page`, `parse_listing_page`, `_looks_like_block_page`).
