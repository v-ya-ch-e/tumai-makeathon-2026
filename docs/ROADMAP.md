# Roadmap

What's next for WG Hunter — ranked by demo impact and scoped so a teammate can pick any item up cold. If you see something you want to own, ping the team channel, then edit this file when the PR lands.

Background reading for each item is linked inline. The evaluator pipeline is defined in [ADR-015](./DECISIONS.md) and the implementation in `[evaluator.py](../backend/app/wg_agent/evaluator.py)`.

## Queued (clearly scoped, ready to pick up)

### Deterministic pre-filter on search results

**Why:** Right now every new listing id is deep-scraped + geocoded + commute-routed before `[evaluator.hard_filter](../backend/app/wg_agent/evaluator.py)` can veto it. That's wasted work for listings that `anonymous_search` already returns with enough data to reject (e.g. price exceeds `max_rent_eur`, WG size outside range, availability clearly outside the move-in window). Cutting this step saves one HTTP round-trip to wg-gesucht + one Routes API call per rejected listing, which matters when the top results page is dominated by over-budget listings during peak season.

**Shape of the change:**

- Add a new `evaluator.can_search_filter(stub_listing, profile) -> Optional[VetoResult]` that runs **only** the vetoes that don't need the scraped description or coords (price, WG size, move-in date). Keep the full `hard_filter` for the post-scrape pass.
- In the scraper (`[ScraperAgent.run_once](../backend/app/scraper/agent.py)`), run this cheaper filter on the `new_stubs` list produced by `anonymous_search` before the deep-scrape step, so stubs clearly outside the team-wide budget never hit wg-gesucht again. For per-user vetoes, run the same filter inside `[UserAgent.run_match_pass](../backend/app/wg_agent/periodic.py)` before the `new_listing` action and log a compact `Skipped pre-filter: <reason>` action so the UI still hears about vetoed candidates.
- Persist the per-user skip to `UserListingRow` (with `score=0.0`, `veto_reason=<reason>`, `components=null`) so the user can still see the rejected listing in the drawer and understand why, mirroring today's post-scrape veto path.

**Touches:** `evaluator.py`, `periodic.py`, `scraper/agent.py`, `test_evaluator.py` (add pre-filter cases), `test_periodic.py` (assert the skipped candidate still persists a `UserListingRow`).

### Wizard catch-up for matcher v2

**Why:** Matcher v2 ([`docs/MATCHER.md`](./MATCHER.md), ADR-NN below) reads three new wizard inputs that the engine currently degrades around: `desired_min_months` (drives `tenancy_fit` — short-vs-long-term intent), `flatmate_self_gender` and `flatmate_self_age` (resolve the `wg_gender` / `wg_age_band` LLM keys; today they always come back unknown). Onboarding also still ships the old wizard tile names that v2's structured-pref family fixed inside the engine: the renamed `furnished` / `pet_friendly` / `non_smoking` tiles work end-to-end, but a future visual pass should add the `wg_gender` / `wg_age_band` tiles so the demographics axis stops being effectively dormant.

**Shape of the change:**

- `OnboardingRequirements`: add a `tenancy_intent` dropdown (`Any | ~3 months | ~6 months | 12+ months`) and a `flatmate_self_age` numeric input. Map `tenancy_intent` to `desired_min_months` server-side (Any → null, ~3 months → 3, etc.).
- `OnboardingProfile`: surface `gender` (already collected on the user record) so the user can opt-in to using it as a flatmate preference; pipe it into `SearchProfile.flatmate_self_gender` on save.
- `OnboardingPreferences`: add the `wg_gender` and `wg_age_band` tiles to the "Living style" group (already wired in the engine; just needs UI).

**Touches:** `frontend/src/pages/OnboardingRequirements.tsx`, `OnboardingProfile.tsx`, `OnboardingPreferences.tsx`, `frontend/src/types.ts` (already typed), `backend/tests/test_evaluator_resolvers.py` (extend the `wg_gender` / `wg_age_band` cases once the wizard sends real values).

### Surface rejections in the dashboard list

**Why:** `ListingList` currently sorts by score so vetoed listings sink to the bottom unsegregated. Teammates and users can't easily answer "did the agent see listing X and reject it?" vs. "is it still being evaluated?"

**Shape of the change:** in `[ListingList.tsx](../frontend/src/components/ListingList.tsx)`, split the sorted list into two sections — "Matched" (no `vetoReason`) and "Rejected" (has `vetoReason`). Rejected cards collapse to a single-line row showing the veto reason in `text-bad` without the score pill.

**Touches:** `ListingList.tsx`, maybe a small tweak to `ListingDrawer.tsx` if we want to keep the breakdown hidden for vetoed listings.

## Later (design work needed first)



### LLM-as-judge per fuzzy component (with self-consistency)

Instead of one `vibe_fit` LLM call, ask the LLM to rate each soft component (`preference_fit` keywords that can't be resolved deterministically, vibe, district fit) independently with structured output per component. Add a self-consistency pass: two calls at `temperature=0.2`, reject if they disagree by > ε. Roughly 2–3× the LLM cost of today's path, so hold until we see the single-call variance become a real problem during demos.

## Done recently

Track what's shipped so reviewers and demo judges can spot-check the history without spelunking git.

- **2026-04-19** — ADR-028: Matcher v2 — full evaluator rewrite per [`docs/MATCHER.md`](./MATCHER.md). Splits final score into a weighted-mean `match_score` plus an absolute `quality_score` blended at `0.85·match + 0.15·quality`; switches commute aggregator to `0.7·min + 0.3·mean` so one bad anchor pulls hard; closes the v1 `quality_fit` double-count and the weight-5-unknown cap escape; adds `tenancy_fit` (with LLM `tenancy_label` fallback when `available_to` is missing), `upfront_cost_fit` (deposit + Ablöse), and a four-family preference resolver (structured booleans / Google Places nearby / regex with word boundaries / LLM soft signals). New `Listing` fields `price_basis` / `deposit_months` / `furniture_buyout_eur` and `SearchProfile` fields `desired_min_months` / `flatmate_self_*`; one-shot DB migration in [`migrate_matcher_v2.py`](../backend/app/scraper/migrate_matcher_v2.py). 106 new tests across `test_evaluator.py` (rewritten), `test_evaluator_resolvers.py` (new), `test_evaluator_integration.py` (new); live A/B smoke at [`backend/scripts/check_engine.py`](../backend/scripts/check_engine.py).
- **2026-04-19** — ADR-027: capped pagination at `SCRAPER_MAX_PAGES` (default 6) per `(source, kind)` and changed stale-stub semantics from halt-the-walk (ADR-026) to skip-and-continue. Stale stubs are dropped without persisting; the loop continues with the next stub up to the page cap. The post-`scrape_detail` freshness check now drops the stale ad **before** persisting (kleinanzeigen previously wrote the row, then halted). Also closed the `mode='flat'` gap on wg-gesucht: `WgGesuchtSource.kind_supported = {'wg', 'flat'}`, with `flat` hitting `/wohnungen-in-…` (category `2`) — recon performed against `https://www.wg-gesucht.de/` on 2026-04-19 (homepage type-selector confirms `WG-Zimmer=0, 1-Zimmer-Wohnung=1, Wohnung=2, Haus=3`). New tests in `test_scraper.py`: `test_skips_stale_stubs_and_continues`, `test_kleinanzeigen_drops_stale_detail_and_continues`, `test_max_pages_caps_per_source_kind`, `test_max_pages_applies_independently_per_kind`.
- **2026-04-19** — ADR-026: dropped the per-source deletion sweep (it was tombstoning live listings whenever a search transiently failed, e.g. on a wg-gesucht captcha redirect). Pagination stops on the first stale stub, leaning on newest-first sort params added to every source URL (`sort_column=0&sort_order=0` for wg-gesucht, `/sortierung:neuste/` for kleinanzeigen, `orderBy: MOST_RECENT` already in tum-living). New `SCRAPER_KIND` env var (`wg` | `flat` | `both`, default `both`) restricts which verticals each source iterates. `SCRAPER_DELETION_PASSES` is gone, `SCRAPER_MAX_AGE_DAYS` defaults to 4 (was 7), `repo.mark_listing_deleted` + `repo.list_active_listing_ids` deleted, `ListingRow.deleted_at` kept on the schema only for backward compatibility with already-tombstoned rows. (Stale-stop behavior subsequently relaxed by ADR-027.)
- **2026-04-18** — ADR-020 + ADR-021: multi-source scraper. Generalized `ScraperAgent` from a hard-coded wg-gesucht loop into a `Source` plugin registry under [`backend/app/scraper/sources/`](../backend/app/scraper/sources/) (wg-gesucht, TUM Living, Kleinanzeigen — selectable via `SCRAPER_ENABLED_SOURCES`). Every `ListingRow.id` is now namespaced (`f"{source}:{external_id}"`); every row carries a `kind` (`'wg'` | `'flat'`); the matcher honors `SearchProfile.mode` for the first time. Per-source-scoped deletion sweep so a wg-gesucht-only pass cannot tombstone Kleinanzeigen rows (later removed by ADR-026). New [`backend/app/scraper/migrate_multi_source.py`](../backend/app/scraper/migrate_multi_source.py) one-shot DB migration (idempotent, transactional, `--dry-run`). Recon + contract: [`docs/SCRAPER.md`](./SCRAPER.md).
- **2026-04-18** — ADR-019: dropped Alembic in favour of `SQLModel.metadata.create_all` on startup; deleted `backend/alembic/` + `backend/alembic.ini` + the `alembic` dependency. See [BACKEND.md "Schema evolution"](./BACKEND.md#schema-evolution).
- **2026-04-18** — ADR-018: split scraper into its own container, global `ListingRow` pool, MySQL-only persistence. See [DECISIONS.md](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only).
- **2026-04-18** — ADR-015: scorecard evaluator with deterministic components + narrow LLM vibe. Replaces the single-LLM-call scoring path. 50 new tests in `[test_evaluator.py](../backend/tests/test_evaluator.py)`; component-breakdown bars in `[ListingDrawer](../frontend/src/components/ListingDrawer.tsx)`.
- **2026-04-18** — ADR-014: structured DOM selectors + `map_config.markers` coords in `parse_listing_page`; zeroed out the cookie-banner-text-in-prompt regression and cut Geocoding API calls to near-zero for listings that render a map.
- **2026-04-18** — ADR-013: weighted preferences + per-location commute budgets. UI: 1–5 weight slider on each preference tile + per-location ideal-commute input.
- **2026-04-18** — ADR-012: commute-aware scoring via Routes API's `computeRouteMatrix`, LLM-only composition (superseded by ADR-015).
- **2026-04-18** — ADR-010 + ADR-011: structured `main_locations` with client-side Places Autocomplete and server-side Geocoding for listing addresses.

---

*This file supersedes the old `ISSUES_TO_FIX.md` scratchpad. When you ship something from "Queued" or "Later", move it under "Done recently" with a one-line summary + ADR/commit reference.*