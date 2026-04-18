# Architecture Decision Records

ADR index for WG Hunter. Each entry lists context, decision, consequences, and the introducing commit where applicable. See also [ARCHITECTURE.md](./ARCHITECTURE.md), [DATA_MODEL.md](./DATA_MODEL.md), and [DESIGN.md](./DESIGN.md).

---

## ADR-001: SQLite + SQLModel + Alembic for persistence

- **Date:** 2026-04-18
- **Status:** Superseded by ADR-018 (MySQL-only) and ADR-019 (no Alembic)

**Context:** Hackathon demos need zero external infra but still benefit from ACID transactions; we may later point the same code at Postgres for a “real” deployment.

**Decision:** Ship with default `sqlite:///~/.wg_hunter/app.db` (overridable via `WG_DB_URL`), model tables in SQLModel, and treat Alembic as the sole schema authority (`0001_initial` onward).

**Consequences:** Fast local setup and easy tarball backups; WAL mode is required so API requests and asyncio hunt tasks can write concurrently ([`db.py`](../backend/app/wg_agent/db.py)). Alembic adds a small startup cost on every process boot ([`main.py`](../backend/app/main.py)).

**Introduced in:** `8ca9fe2`

---

## ADR-002: Vite + React (no Next.js)

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** The UI is a desktop-first SPA with no SEO requirement; the backend already serves HTTP and can host static assets.

**Decision:** Use Vite 8 + React 19 + React Router 7 for the frontend, and let FastAPI serve `frontend/dist/` with a catch-all SPA fallback ([`main.py`](../backend/app/main.py)).

**Consequences:** One deployable service, no SSR/edge complexity, straightforward `fetch` + `EventSource` integration. We give up built-in metadata/OG tags per route.

**Introduced in:** `8d3f6fd`

---

## ADR-003: Aesop warm-cream palette with one accent

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** The product brief called for a warm, editorial feel distinct from typical SaaS blues.

**Decision:** Encode the palette as CSS variables in `:root`, map them through Tailwind (`tailwind.config.ts`), use terracotta as the single accent, and sage/amber/rust for semantic states ([`index.css`](../frontend/src/index.css)).

**Consequences:** Re-skinning is centralized; review rules are written down in [DESIGN.md](./DESIGN.md) to keep contributions disciplined.

**Introduced in:** `a4f858f`

---

## ADR-004: Per-hunt listings (composite primary key)

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** Two users (or two hunts) can target the same wg-gesucht numeric listing id; a global listing table would collide or leak scores across hunts.

**Decision:** Model `ListingRow` (and related score/photo keys) with composite PK `(id, hunt_id)` ([`db_models.py`](../backend/app/wg_agent/db_models.py), [DATA_MODEL.md](./DATA_MODEL.md)).

**Consequences:** Listings and scores are naturally scoped; revisiting the same external id in a later hunt is OK. API calls must always supply `hunt_id` when addressing a listing.

**Introduced in:** `8ca9fe2`

---

## ADR-005: Alembic from day 1

- **Date:** 2026-04-18
- **Status:** Superseded by ADR-019

**Context:** SQLite tempts teams to rely on `create_all()` and skip migration history, which breaks as soon as collaborators diverge.

**Decision:** Check in Alembic (`backend/alembic/`) and run `upgrade head` during FastAPI lifespan before serving ([`main.py`](../backend/app/main.py)).

**Consequences:** Schema changes require an Alembic revision (usually autogenerate + human review); startup is marginally slower but reproducible.

**Introduced in:** `8ca9fe2`

---

## ADR-006: HTTPX anonymous search, Playwright reserved for auth flows

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** Launching Chromium is slow and operationally heavy for a demo loop; wg-gesucht listing pages are public HTML.

**Decision:** Implement `anonymous_search` + `anonymous_scrape_listing` with httpx + parsers ([`browser.py`](../backend/app/wg_agent/browser.py)); keep `WGBrowser` / `launch_browser` for future authenticated messaging.

**Consequences:** Cold hunts start faster; fewer moving parts for basic scoring demos; Playwright install remains optional for v1 happy paths ([SETUP.md](./SETUP.md)).

**Introduced in:** `2993f37`

---

## ADR-007: SSE hybrid queue + DB poll

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** The dashboard wants near-live updates, but in-process queues alone would miss actions after a reload or if producers/consumers differ.

**Decision:** `/api/hunts/{id}/stream` drains a per-hunt `asyncio.Queue` with a **1s** timeout, then **always** re-reads actions via `repo.get_hunt` on a fresh session ([`api.py`](../backend/app/wg_agent/api.py)).

**Consequences:** Low latency when the queue is hot; resilient replay after restarts; one extra SQLite read per poll tick.

**Introduced in:** `2839f1b` (JSON/SSE surface) and `9a964fe` (periodic hunter wiring)

---

## ADR-008: Fernet-only credential-at-rest encryption

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** Optional wg credentials must not live plaintext on disk, but we are not building enterprise KMS integration for a hackathon scope.

**Decision:** Encrypt the JSON credential blob with Fernet; resolve keys from `WG_SECRET_KEY` or auto-generate `~/.wg_hunter/secret.key` with mode `600` ([`crypto.py`](../backend/app/wg_agent/crypto.py)).

**Consequences:** Simple local security story; **not** sufficient for multi-tenant SaaS (single symmetric key per machine).

**Introduced in:** `8ca9fe2`

---

## ADR-009: snake_case on the wire, camelCase in the UI

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** Python/Pydantic idioms use snake_case JSON; TypeScript/React ergonomics favor camelCase fields in components.

**Decision:** Keep backend DTO field names snake_case; normalize at the client edge with `toCamel` / `toSnake` in [`frontend/src/lib/api.ts`](../frontend/src/lib/api.ts) and mirror shapes in [`types.ts`](../frontend/src/types.ts).

**Consequences:** One obvious conversion layer; grep-friendly distinction between transport and UI types; Vitest covers parsing edge cases (`1a3af89`).

**Introduced in:** `afdf8cf` (client scaffolding) with tests in `1a3af89`

---

## ADR-010: Structured `main_locations` via client-side Google Places Autocomplete

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** Free-text `main_locations: list[str]` could not feed commute-aware scoring — the LLM got a token like `"TUM"` with no coordinate. We also wanted the user to pick a concrete place (building, S-Bahn, district) rather than spell out a string.

**Decision:** Collect main locations as structured `PlaceLocation { label, place_id, lat, lng }` via Google Places Autocomplete (New). Load the Maps JavaScript API client-side with [`@vis.gl/react-google-maps`](https://github.com/visgl/react-google-maps); the `VITE_GOOGLE_MAPS_API_KEY` ships in the bundle but is referrer + API restricted per Google's documented pattern. Store the structured shape end-to-end through DTOs, domain model, and the existing `JSON` column; derive the legacy wg-gesucht `city` from `main_locations[0].label`.

**Consequences:** One repo-root `.env` now owns the Maps key (Vite reads it via [`envDir: '..'`](../frontend/vite.config.ts)). No backend proxy is needed, so the FastAPI surface stays unchanged. Existing dev rows are wiped by [`alembic/0002_places_main_locations.py`](../backend/alembic/versions/0002_places_main_locations.py); pre-demo users re-pick locations. Listing addresses are not yet geocoded — that's the next piece needed before the Routes API call that commute scoring will depend on.

**Introduced in:** this commit

---

## ADR-011: Server-side Geocoding API call inside `anonymous_scrape_listing`

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** Main locations carry coordinates (ADR-010), but the other side of the commute equation — the listing's address — was still free text. Commute-aware scoring needs `(lat, lng)` on *both* origin and destination. We also didn't want a second API call path later (e.g. a frontend-side geocode triggered from a map UI) because it would diverge from what the scorer sees.

**Decision:** Call the Google Geocoding API server-side from [`geocoder.py`](../backend/app/wg_agent/geocoder.py) immediately after `parse_listing_page` inside [`browser.anonymous_scrape_listing`](../backend/app/wg_agent/browser.py). Store the result on `ListingRow.lat` / `ListingRow.lng` via the existing `repo.upsert_listing` path (schema widened in [`0003_listing_coords.py`](../backend/alembic/versions/0003_listing_coords.py)) and expose it on `ListingDTO` for future map UIs. Key material is a separate `GOOGLE_MAPS_SERVER_KEY` (no `VITE_` prefix, never shipped to the browser), IP-restricted and scoped to the Geocoding API only in Google Cloud Console.

**Consequences:** Listings get coordinates exactly once per scrape, cached in-process so rescans of the same string don't re-bill the free-tier quota. Missing key / HTTP errors / `ZERO_RESULTS` all degrade gracefully to `None` instead of raising, so the scrape pipeline keeps working without the key in dev. A second key is one more secret to manage, but keeping the browser and server keys separate lets us restrict each to the smallest-possible API set. No scoring logic changes yet — commute-aware scoring is tracked separately as a follow-up that reads `listing.lat/lng` plus `SearchProfile.main_locations[].lat/lng` to call the Routes API.

**Introduced in:** this commit

---

## ADR-012: Commute-aware scoring via Routes API, LLM-only composition

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** With listing coordinates (ADR-011) and main-location coordinates (ADR-010) both in hand, we can now measure per-mode commute times and let them influence scoring. The product question was how to combine a deterministic commute term with the existing LLM score — blend them numerically, add a secondary ranking pass, or feed everything through the prompt and let the LLM decide.

**Decision:** Call the Google Routes API's `computeRouteMatrix` from [`commute.py`](../backend/app/wg_agent/commute.py) inside `HuntEngine.run_find_only` (one POST per mode, guarded by `listing.lat is not None`), feed the resulting `{(place_id, mode): seconds}` matrix into `brain.score_listing` as a "Commute times" block in the user prompt, and leave the composition entirely to the LLM. Persist only the collapsed `{place_id: {mode, minutes}}` (fastest mode per location) on [`ListingScoreRow.travel_minutes`](../backend/app/wg_agent/db_models.py) so the listing drawer can render per-location minutes without re-calling Routes. Modes are picked from the user's profile: always `TRANSIT`, plus `BICYCLE` when `has_bike`, plus `DRIVE` when `has_car`. The prompt instructs the LLM to treat commutes over 40 minutes as strong negatives and under 20 minutes as positives.

**Consequences:** Smallest possible diff — scoring stays in one place (the LLM), and the prompt additions are bounded (a few lines per location). No new sliders, weights, or per-location caps in the onboarding UI. Trading off: the LLM's commute reasoning isn't audited by a deterministic check, so edge cases (e.g. a 70-minute transit commute praised because the listing is cheap) depend on prompt discipline rather than hard guardrails; if this turns noisy, a follow-up can add a deterministic commute term that blends with the LLM score. Free-tier economics are comfortable: a typical user with 2 main locations × 2 modes = 4 elements per listing, well inside the Routes API's element quota. The API call is the last network hop before scoring, so listings without coordinates (or users with no `main_locations`) fall straight through to the pre-plan behaviour without an extra branch in the SSE path.

**Introduced in:** this commit

---

## ADR-013: Weighted preferences + per-location commute budgets, LLM composition

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** ADR-012 wired the Routes API into scoring with a single rule ("over 40 min = negative, under 20 min = positive") and left preferences as flat string tags. In practice, two users with the same `["gym", "park"]` preference list have very different priorities: one may treat the gym as non-negotiable, the other as a mild bonus. Likewise, the "fine" commute for someone cycling to TUM differs from what's "fine" for someone visiting their partner in Sendling twice a week. Both needs pointed to the same answer: let the user express importance, and give each main location its own budget.

**Decision:** Encode preferences as `PreferenceWeight { key, weight: 1..5 }` and extend `PlaceLocation` with an optional `max_commute_minutes` (5..240). Persist both inside the existing `SearchProfileRow` JSON columns (no schema change; [`0005_weighted_prefs.py`](../backend/alembic/versions/0005_weighted_prefs.py) resets pre-demo rows, mirroring the [`0002` reset](../backend/alembic/versions/0002_places_main_locations.py)). In the UI, collect weights via a reusable [`WeightSlider`](../frontend/src/components/ui/WeightSlider.tsx) that expands under each selected preference tile in `OnboardingPreferences`, and collect budgets as a per-location minutes field inside the `PlaceAutocomplete` row stack in `OnboardingRequirements`. Keep composition LLM-only (per ADR-012): extend `_requirements_summary` with a `Preferences (1=nice, 5=must-have)` line and extend `_commute_block` to render `(max N min)` beside each location; update `SCORE_USER_TEMPLATE` to cap score at 0.4 when a weight-5 preference is clearly missing and to treat fastest-mode times above a location's budget as strong negatives.

**Consequences:** The three-layer pipeline changes in one coherent way — `models.py` + `dto.py` + `db_models.py` + `repo.py` all reshape the same two JSON payloads — so the grep-level footprint for "how weights flow" is small. `repo.get_search_profile` parses both new `{key, weight}` dicts and legacy bare strings (weight-3 fallback), so dev DBs that already hold pre-0005 rows don't break during migration. We add no deterministic cap on the LLM score; behaviour still depends on prompt discipline. If hackathon testing shows the LLM disregarding weight-5 items or budgets, a follow-up can add a deterministic veto on top of the current score (a natural extension of ADR-012's "follow-up if noisy" escape hatch).

**Introduced in:** this commit

---

## ADR-014: Structured DOM selectors + `map_config.markers` coords in `parse_listing_page`

- **Date:** 2026-04-18  
- **Status:** Accepted  

**Context:** The original `parse_listing_page` ran `re.search` over `soup.get_text()` for every field, and the description fallback was `full_text[:4000]`. Three problems showed up while bringing up the scorer: (a) `furnished` flipped to `True` on any listing that said "nicht möbliert" in the description (the negation lives 40+ chars before the keyword, outside the regex's reach); (b) `languages` and `pets_allowed` misfired whenever a free-text paragraph contained the label words; (c) the 4000-char fallback dumped cookie-consent markup, login-modal copy, and footer navigation into the LLM prompt. Separately, the geocoder step sat on the critical path for every listing even though the detail page already ships the landlord's own map pin inside a `map_config.markers` script block.

**Decision:** Refactor `parse_listing_page` to prefer scoped DOM lookups with explicit fallbacks to the original full-text regexes. Add three helpers in [`browser.py`](../backend/app/wg_agent/browser.py): `_section_pairs` (walks forward from a section `<h2>` until the next `<h2>` to collect label/value rows — scoped enough to separate Kosten from Verfügbarkeit even though they share a `div.panel`); `_wg_details_lines` (returns the WG-Details `<li>` text in order for languages/pets/smoking); `_parse_address_panel` (splits the Adresse detail into `(street, postal_code, city, district)`); `_parse_map_lat_lng` (extracts `(lat, lng)` from the `map_config.markers` script via a narrow regex). Pull the description from `#ad_description_text` with embedded `<script>`/`<iframe>`/`div-gpt-ad-*` stripped, and never fall back to the full-page text dump. Have `anonymous_scrape_listing` trust the map-pin coordinates when present and only call the Geocoding API when they're missing. Lock every new assertion down in [`test_wg_parser.py`](../backend/tests/test_wg_parser.py) against the committed fixtures.

**Consequences:** The scoring prompt now sees clean listing fields instead of menu chrome, so `brain.score_listing` has less noise to filter. `furnished` / `pets_allowed` / `smoking_ok` become trustworthy enough that a future deterministic pre-filter (see ADR-013 escape hatch) can rely on them. `listing.lat` / `listing.lng` come for free on every listing that renders a map (≈all of them), reducing Geocoding API calls to near-zero in typical hunts — the geocoder stays wired as a fallback, not a hot-path dependency. The parser still degrades gracefully when wg-gesucht tweaks a selector because each DOM path preserves its pre-existing regex fallback. No schema change, no dependency change, no new prompts or scoring logic.

**Introduced in:** this commit

---

## ADR-015: Scorecard evaluator with deterministic components + narrow LLM vibe

- **Date:** 2026-04-18  
- **Status:** Accepted  
- **Supersedes:** the "follow-up if noisy" escape hatch in ADR-012 and ADR-013

**Context:** ADR-012 put commute-aware scoring behind a single `brain.score_listing` LLM call; ADR-013 added weighted preferences and per-location commute budgets to the same prompt. Both explicitly flagged that the LLM composes everything — hard budget caps, must-have preferences, commute thresholds — as prose rules rather than deterministic checks, and noted a follow-up "if this turns noisy." Observed problems: (a) listings well over `max_rent_eur` still came back with 0.6+ scores when the description read well; (b) weight-5 "must-haves" were honor-system (the model decided both whether a tile was missing and whether to obey the cap); (c) two listings scored in different runs weren't comparable because the scale drifted with prompt and model version; (d) every new listing cost one LLM call, including obvious rejects (wrong city, 3x the rent); (e) the single-sentence `score_reason` wasn't auditable — we couldn't grep "why exactly did listing X beat Y."

**Decision:** Replace the single-LLM-call path with a **scorecard evaluator** in new module [`evaluator.py`](../backend/app/wg_agent/evaluator.py). The pipeline is:

1. **Hard filter** — deterministic vetoes for anything that can't possibly match: `price_eur > max_rent_eur`, city mismatch (with a Muenchen/München normalizer), district in `avoid_districts`, `available_from` after `move_in_until`, and weight-5 preferences on structured booleans (`furnished`, `pets_allowed`, `smoking_ok`) directly contradicted by the listing. Vetoes short-circuit: no components computed, no LLM call, `ListingScoreRow.score = 0.0`, action log emits `Rejected {id}: <reason>`.
2. **Component functions** — six pure-Python components, each returning `ComponentScore(key, score, weight, evidence, hard_cap?, missing_data?)`. Curves:
   - `price_fit`: 1.0 inside `[min_rent, 0.85 * max_rent]`, linear down to 0 at `max_rent`, 0 above.
   - `size_fit`: trapezoid — 0 below `min_size_m2`, ramps to 1 over the next 5 m², stays 1 up to `max_size_m2`, back to 0 at `max_size_m2 * 1.25`.
   - `wg_size_fit`: 1 inside `[min_wg_size, max_wg_size]`, 0.5 one off, 0 further. Skipped (`missing_data`) when `mode == "flat"`.
   - `availability_fit`: 1 inside the move-in window; linear down to 0 over 14 days either side; `missing_data` when either the listing date or the window is missing.
   - `commute_fit`: per `main_location`, the fastest-mode time `m` vs. `budget = max_commute_minutes or 40` — 1.0 at `m ≤ 0.5 * budget`, 0.5 at `m = budget`, 0.0 at `m ≥ 1.5 * budget`. Beyond `1.5 * budget` also sets `hard_cap = 0.3`. Averaged across locations.
   - `preference_fit`: iterate `PreferenceWeight`s; structured booleans resolve against `Listing` fields, soft tags scan `description.lower()` with a synonym table (`PREFERENCE_KEYWORDS`). Score is `sum(weight * present) / sum(weight)`; weight-5 clearly-absent sets `hard_cap = 0.4`. Unknown tags get neutral half credit so "can't tell" isn't a straight negative.
3. **`vibe_fit`** — the one remaining LLM call, through a new narrow function `brain.vibe_score(listing, profile) -> VibeScore` with `response_format=json_object` + Pydantic validation. The prompt is explicitly told **not** to judge price, size, WG size, or commute; it only rates `listing.description` + `listing.district` against `profile.notes`, `preferred_districts`, and `avoid_districts`. On `ValidationError` or any exception the component degrades to `missing_data=True`, no fallback score.
4. **`compose`** — weighted mean across components with `missing_data == False` using `COMPONENT_WEIGHTS` (price 2.0, commute 2.0, preferences 1.5, size/availability/vibe 1.0, wg_size 0.5), then apply the minimum of every non-null `hard_cap`, then clamp to `[0, 1]`. Derives `score_reason` from the strongest positive and weakest component so the existing drawer copy still reads naturally; fills `match_reasons` / `mismatch_reasons` from component evidence for back-compat with pre-migration rows.

Persistence: one additive Alembic revision [`0006_scorecard_components.py`](../backend/alembic/versions/0006_scorecard_components.py) adds `components: JSON` and `veto_reason: str | None` to `ListingScoreRow`. [`repo._listing_from_row`](../backend/app/wg_agent/repo.py) rehydrates both with NULL-safe fallbacks, so old hunts keep rendering via the legacy `score_reason` block. [`HuntEngine.run_find_only`](../backend/app/wg_agent/periodic.py) now calls `await evaluator.evaluate(...)` instead of `brain.score_listing(...)`; the old entry point stays exported for [`orchestrator.py`](../backend/app/wg_agent/orchestrator.py) (the non-v1 path). On the UI side, [`ListingDrawer`](../frontend/src/components/ListingDrawer.tsx) renders one bar per component with `evidence` underneath (greyed when `missing_data`), plus a red "Rejected" banner when `vetoReason` is set.

**Consequences:** Every numeric judgment is code we can unit-test against fixtures — [`test_evaluator.py`](../backend/tests/test_evaluator.py) pins each curve at its boundaries and verifies `compose`'s arithmetic, `hard_cap` minimum, and veto short-circuit. Obvious rejects never hit the LLM (one network round-trip saved per vetoed listing; the `Rejected {id}: over budget` action gives the user a defensible reason). Scores are now comparable across runs because the curves and weights live in one file; changing them is a diff, not a prompt rewrite. The vibe prompt is small enough that `gpt-4o-mini` output is more consistent, and a `ValidationError` degrades to `missing_data` instead of corrupting the composite score. Trade-offs: (1) the curves and `COMPONENT_WEIGHTS` are currently hand-picked — ADR-015 is the substrate a later ADR can sit on if we want to fit weights from user feedback (thumbs up/down in the UI), but that requires UI work first and is explicitly out of scope; (2) `preference_fit`'s keyword table ([`PREFERENCE_KEYWORDS`](../backend/app/wg_agent/evaluator.py)) is a small German/English synonym list and will miss creative phrasings — listings with no description fall to the neutral half-credit path on purpose, matching the "don't invent features" rule from ADR-013; (3) `brain.score_listing` is still exported (delegates to the same prompt as before) so the older orchestrator path doesn't break, but all **v1 hunts go through the evaluator** — the legacy function is a compatibility shim, not the default.

**Introduced in:** this commit

---

## ADR-016: Keep Google only for frontend autocomplete; move backend location intelligence to Geoapify

- **Date:** 2026-04-18  
- **Status:** Superseded by ADR-017  
- **Supersedes:** the backend provider choices in ADR-011 and ADR-012

**Context:** The product requirement changed: Google Maps must no longer be used on the backend. We still need three capabilities server-side: (1) fallback geocoding when a listing detail page lacks `map_config.markers`, (2) per-mode travel time to the student's important places, and (3) real-world nearby amenity lookup for place-like user preferences such as `gym`, `park`, and `supermarket`. The previous implementation handled only (1) and (2), both via Google, and preference scoring still relied mostly on description keywords.

**Decision:** Keep ADR-010's client-side Google Places Autocomplete for picking `main_locations`, but replace every backend Google Maps call with Geoapify. Specifically:

1. [`geocoder.py`](../backend/app/wg_agent/geocoder.py) now uses Geoapify forward geocoding with `GEOAPIFY_API_KEY` as the fallback when the listing page provides no map pin.
2. [`commute.py`](../backend/app/wg_agent/commute.py) now uses Geoapify Route Matrix for `TRANSIT`, `BICYCLE`, and `DRIVE`, preserving the existing `{(place_id, mode): seconds}` contract so the evaluator and drawer logic stay stable.
3. New module [`places.py`](../backend/app/wg_agent/places.py) queries Geoapify Places for the nearest real-world match to place-like preferences (`supermarket`, `gym`, `park`, `cafe`, `bars`, `library`, `coworking`, `nightlife`, `green_space`) within a 2 km radius around the listing.
4. [`HuntEngine.run_find_only`](../backend/app/wg_agent/periodic.py) now enriches each listing with both `travel_times` and `nearby_places`, passes both into [`evaluator.evaluate`](../backend/app/wg_agent/evaluator.py), persists both via [`repo.save_score`](../backend/app/wg_agent/repo.py), and includes nearby-place distances in the `evaluate` action detail.
5. [`preference_fit`](../backend/app/wg_agent/evaluator.py) now prefers real nearby-place distances over substring guesses for supported place-like preferences, using a distance curve (1.0 when genuinely close, down to 0.0 at the search-radius boundary). Weight-5 place preferences now cap the score when the nearest matching place is too far away or absent.
6. [`brain.vibe_score`](../backend/app/wg_agent/brain.py) and the legacy `score_listing` prompt builder now receive a "Nearby preference places" block so the LLM sees the same neighborhood context the deterministic scorer uses.
7. Persistence grows by one additive column: [`ListingScoreRow.nearby_places`](../backend/app/wg_agent/db_models.py), added in [`0007_nearby_places.py`](../backend/alembic/versions/0007_nearby_places.py), and exposed to the UI as `ListingDetailDTO.nearby_preference_places`.

**Consequences:** The backend now depends on one location provider key instead of a second Google key, while the browser-side Google autocomplete remains unchanged. Commute scoring keeps the same downstream interfaces, so the migration is mostly isolated to provider clients plus the new nearby-places path. Preference scoring becomes materially better for neighborhood preferences because the agent can say "nearest gym is 240 m away" instead of guessing from listing prose. The free-tier economics remain workable for demos (Geoapify documents 3,000 free requests/day across these APIs), but place enrichment does add more API calls per listing than the previous keyword-only path; in-process caches and radius-bounded `limit=1` lookups keep that bounded.

**Introduced in:** this commit

---

## ADR-017: Consolidate backend location intelligence on Google Maps Platform

- **Date:** 2026-04-18  
- **Status:** Accepted  
- **Supersedes:** ADR-016

**Context:** We have access to the relevant Google Maps Platform services and want to use them consistently on the backend instead of splitting browser autocomplete on Google and backend enrichment on Geoapify. The backend still needs the same three capabilities: (1) fallback geocoding when a listing lacks `map_config.markers`, (2) commute times to user-declared important places, and (3) nearby amenity lookup for place-like preferences such as `gym`, `park`, `supermarket`, and `coworking`. We also want to keep explicit request throttling so background hunts do not spike beyond a reasonable QPS ceiling.

**Decision:** Keep the existing frontend Google Places Autocomplete path and move backend enrichment to Google Maps Platform as well:

1. [`geocoder.py`](../backend/app/wg_agent/geocoder.py) now uses the Google Geocoding API with `GOOGLE_MAPS_SERVER_KEY` as the fallback when the listing page provides no map pin.
2. [`commute.py`](../backend/app/wg_agent/commute.py) now uses the Google Distance Matrix API for `TRANSIT`, `BICYCLE`, and `DRIVE`, preserving the existing `{(place_id, mode): seconds}` contract so the evaluator and drawer logic stay stable.
3. [`places.py`](../backend/app/wg_agent/places.py) now uses Places API (New): Nearby Search (New) for typed categories and Text Search (New) for `coworking`, still returning the nearest `NearbyPlace` per supported preference inside the 2 km search radius.
4. New module [`google_maps.py`](../backend/app/wg_agent/google_maps.py) provides a shared async throttle across geocoding, commute, and nearby-place calls. It defaults to `8 req/s` and can be tuned via `GOOGLE_MAPS_MAX_RPS`.
5. The browser key remains `VITE_GOOGLE_MAPS_API_KEY`, while the backend key is separate as `GOOGLE_MAPS_SERVER_KEY` so it can be IP-restricted and never shipped to the browser bundle.

**Consequences:** Setup becomes simpler again for teams already provisioned in Google Cloud: the APIs to enable are `Geocoding API`, `Distance Matrix API`, and `Places API (New)` for the backend, plus `Maps JavaScript API` and `Places API (New)` for the browser autocomplete. The downstream evaluator, repo persistence, and UI drawer stay unchanged because the provider clients preserve the existing contracts. We deliberately keep explicit throttling even though Google quotas can be higher than Geoapify's free tier, because concurrent hunts can still create avoidable burst traffic. Trade-off: the backend uses one legacy Google service, `Distance Matrix API`, because it preserves the smallest and most reliable matrix-shaped diff across `DRIVE`, `BICYCLE`, and `TRANSIT` in the current codebase.

**Introduced in:** this commit

---

## ADR-018: Separate scraper container + global ListingRow, MySQL-only

- **Date:** 2026-04-18
- **Status:** Accepted
- **Supersedes:** ADR-004 (per-hunt composite PK) and the SQLite parts of ADR-001

**Context:** Every hunt re-scraped the same wg-gesucht listings, redoing work already done by a concurrent hunt for the same city. `ListingRow` used a composite `(id, hunt_id)` PK (ADR-004) so two users watching Munich stored the same listing HTML twice — and paid the bandwidth + parse cost twice. The product also needed a scraper that keeps running when no one has pressed *Start hunt*, so fresh inventory exists the moment a user wants to match. Finally, SQLite under `~/.wg_hunter/app.db` (ADR-001) was fine for single-developer demos but awkward for a team: no shared view of the pool, no referential integrity for v2 messaging, no multi-writer story.

**Decision:** Split scraping from matching and move to MySQL.

1. **Scraping** lives in a separate `scraper` container ([`app/scraper/{agent.py, main.py}`](../backend/app/scraper/agent.py)). It runs an asyncio loop that calls `browser.anonymous_search` + `anonymous_scrape_listing` against a permissive env-driven `SearchProfile` and writes to a global `ListingRow` pool via `repo.upsert_global_listing`. It refreshes listings whose `scraped_at` is older than `SCRAPER_REFRESH_HOURS` (default 24h), records partial results with `scrape_status='stub'`, and records scrape exceptions with `scrape_status='failed'` + `scrape_error`. Scraper writes `PhotoRow` too.
2. **`ListingRow` becomes global.** `id` is the sole primary key; `hunt_id` is dropped. Added columns: `scrape_status` (`stub` | `full` | `failed`, indexed), `scraped_at` (indexed), `scrape_error`. `PhotoRow` loses `hunt_id` too; its PK is `(listing_id, ordinal)`, FK to `listingrow.id`.
3. **Hunts become pure matchers.** [`HuntEngine.run_find_only`](../backend/app/wg_agent/periodic.py) no longer calls `browser.*`; it iterates `repo.list_scorable_listings(hunt_id, status='full')` (global listings this hunt has not yet scored) and writes one `ListingScoreRow` per candidate — including vetoed listings with `score=0.0`. `ListingScoreRow` grows one new column, `scored_against_scraped_at`, which records the `ListingRow.scraped_at` at score time so the UI can show staleness and future rescores can detect stale rows.
4. **`ListingScoreRow` is the hunt ↔ listing membership record.** [`list_listings_for_hunt`](../backend/app/wg_agent/repo.py) joins `ListingScoreRow JOIN ListingRow` on the hunt id, which preserves the frontend's `HuntDTO.listings` contract without introducing a new table. The matcher's invariant is: every listing it evaluates gets a `ListingScoreRow` written, or the listing disappears from the UI view.
5. **MySQL-only persistence.** [`db.py`](../backend/app/wg_agent/db.py) assembles its DSN from five required env vars (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`) at import time. Any missing / empty var → a single `RuntimeError` listing all of them, so misconfigured environments fail loud instead of writing to a phantom DB. The engine uses `pool_pre_ping=True` + `pool_recycle=1800` for AWS RDS hygiene, and schema is materialised by `SQLModel.metadata.create_all(engine)` on startup (see [ADR-019](#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)). `AgentActionRow.listing_id` and `MessageRow.{listing_id, hunt_id}` now carry real FKs that MySQL enforces (they were undeclared under the SQLite-era setup because the composite listing key made that awkward). The `docker-compose.yml` adds a `scraper` service that reuses the `backend` image and is configured via the same `.env`; developers point at the team-shared AWS RDS, so there's no local `mysql` service.
6. **Tests stay zero-infra.** [`backend/tests/conftest.py`](../backend/tests/conftest.py) sets inert `DB_*` placeholders before any test module imports — enough for `db.py` to construct its (unused) production engine without crashing. Each test then builds its own in-memory SQLite engine and monkey-patches `db_module.engine`. SQLModel metadata works against both dialects, and the MySQL-specific engine options live only on the production builder.

**Consequences:** One listing is scraped once per refresh cycle regardless of how many users match against it, which cuts outbound traffic to wg-gesucht linearly in the number of concurrent hunts. Hunts start instantly once the scraper has warmed the pool (no synchronous scrape on the request path). The scraper can be stopped, scaled, or replaced without touching the backend. Referential integrity is now enforced everywhere on MySQL. Trade-offs: (1) `start hunt` on an empty pool surfaces zero candidates until the scraper runs — that's the design, but teams should warm the pool before demos; (2) the composite-PK invariant from ADR-004 is explicitly dropped, so any future code that reads `ListingRow` must use `listing_id` alone and route `hunt_id` through `ListingScoreRow` (documented in [DATA_MODEL.md](./DATA_MODEL.md)); (3) a SQLite-free production requires every developer to have all five `DB_*` vars configured — tests bypass this via `conftest.py`, local dev reads from the shared RDS.

**Introduced in:** this commit

---

## ADR-019: Drop Alembic, use `SQLModel.metadata.create_all`

- **Date:** 2026-04-18
- **Status:** Accepted
- **Supersedes:** ADR-005 (Alembic from day 1), and the migration-tooling part of ADR-001

**Context:** Immediately after the MySQL move (ADR-018) the Alembic tree held exactly one migration — `0001_initial_mysql.py`, the consolidated initial schema. There were no rename, backfill, or data-migration scripts anywhere in `backend/alembic/versions/`, and none of the planned near-term schema changes need preservation semantics: the dev workflow is already `DROP DATABASE wg_hunter; CREATE DATABASE wg_hunter;` before a schema change lands (see [SETUP.md "Reset the database"](./SETUP.md#reset-the-database)), because the team shares one AWS RDS instance and treats its contents as disposable during the hackathon. Against that backdrop, Alembic was pure overhead — a dependency, a `backend/alembic/` directory, a second place to keep in sync with `db_models.py`, and two `command.upgrade(cfg, "head")` calls (one per container) that race on every startup.

**Decision:** Delete Alembic. Both [`backend/app/main.py`](../backend/app/main.py) and [`backend/app/scraper/main.py`](../backend/app/scraper/main.py) call `db.init_db()` on startup, which in turn calls `SQLModel.metadata.create_all(engine)`. That single function creates any missing tables on first boot (including all FKs and indexes declared via SQLModel `Field(...)` annotations in [`db_models.py`](../backend/app/wg_agent/db_models.py)), and is a no-op on subsequent boots. Removed: `backend/alembic/` (env.py, script.py.mako, versions/), `backend/alembic.ini`, and the `alembic>=1.13` line from `backend/requirements.txt`.

**Consequences:** One fewer dependency, one fewer directory, one fewer "keep the migration file in sync with `db_models.py`" failure mode. Startup is measurably faster (Alembic's context load was ~300 ms per container). The trade-off is explicit and documented: **`create_all` does not add columns to existing tables.** Any non-additive schema change requires dropping the database (see [SETUP.md](./SETUP.md) + [BACKEND.md "Schema evolution"](./BACKEND.md#schema-evolution)). That matches our stated dev workflow, but it is strictly worse than Alembic for any future "preserve this data across a column rename" scenario. When such a scenario arises, running `alembic init` and `--autogenerate` re-establishes the plumbing in ten minutes — we just don't carry its weight before we need it.

**Introduced in:** this commit

---

## ADR-020: Multi-source listing identifiers via string namespacing

- **Date:** 2026-04-18
- **Status:** Accepted

**Context:** WG Hunter is moving from one scraper source (`wg-gesucht`) to three (`wg-gesucht`, `tum-living`, `kleinanzeigen`). Each source has its own external id namespace: wg-gesucht uses 5–9 digit numbers, TUM Living uses UUIDs, Kleinanzeigen uses ~10 digit numbers. The id namespaces don't structurally collide today (different lengths, different alphabets) but nothing prevents a future Kleinanzeigen id from also being a valid wg-gesucht id, and the existing single-column `ListingRow.id: str` PK has no way to distinguish them. We needed an identifier that (a) makes cross-source collisions structurally impossible, (b) lets `repo.upsert_global_listing` keep its `session.get(ListingRow, id)` then `session.merge(row)` shape, (c) avoids changing every API URL, SSE payload, and frontend `listingId` reference.

**Decision:** Encode the source as a prefix on the existing string PK: `ListingRow.id = f"{source}:{external_id}"` where `source ∈ {wg-gesucht, tum-living, kleinanzeigen}`. The PK stays a single `str` column. The source is recoverable from any code path via `id.split(":", 1)[0]`. Existing wg-gesucht rows are migrated by a one-shot SQL `UPDATE … SET id = CONCAT('wg-gesucht:', id)` plus matching FK column updates on `photorow.listing_id`, `userlistingrow.listing_id`, `useractionrow.listing_id`, executed by hand at cutover (no Alembic, per [ADR-019](#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)). New sources emit the namespaced form from day one. The deletion sweep also gains per-source scoping (`repo.list_active_listing_ids(source=...)` filters by `id LIKE 'wg-gesucht:%'`) so a wg-gesucht-only pass cannot tombstone Kleinanzeigen / TUM Living rows.

**Consequences:** Zero schema change beyond the migration UPDATE — the `id: str` column stays put. Zero change to API URLs (`/api/listings/{listing_id}` accepts the longer string after percent-encoding the colon, which `encodeURIComponent` does automatically and FastAPI decodes back transparently). Zero change to SSE payload structure — `Action.listingId` is already an opaque string. Zero change to `repo.upsert_global_listing`'s dedup logic — the longer string dedups the same way. Trade-off: we lose the ability to query "all listings from source X" without a `LIKE 'X:%'` scan; if that ever becomes hot, a partial-index workaround or a derived `source` column is one additive migration away. We considered (and rejected) a composite `(source, external_id)` PK — it would force changes to every API route signature, every SSE payload, every frontend type.

**Introduced in:** this commit

---

## ADR-021: Listing kind as a first-class column

- **Date:** 2026-04-18
- **Status:** Accepted

**Context:** WG Hunter scrapes both shared rooms (WG) and full apartments. The existing `SearchProfile.mode: Literal['wg', 'flat', 'both']` was wired in the wizard months ago, but the matcher could never honor it because nothing on `ListingRow` told us what kind the listing was. Two options: infer at read time from the listing's source URL pattern (`/wg-zimmer-in-…` vs `/s-mietwohnung/…`), or persist the kind explicitly. Inferring at read time is fragile (each source has its own URL pattern, the regex would have to live in `repo.py` and stay in sync with three scraper modules), forces a per-source URL parser into a layer that doesn't otherwise know about sources, and runs a regex on every listing on every read.

**Decision:** Add `kind: Literal['wg', 'flat']` as an indexed column on `ListingRow` (default `'wg'` for the existing wg-gesucht-only pool) and as a field on the domain `Listing` model. Each per-source scraper sets `kind` from the search vertical it iterated — the listing-detail page does not need to be parsed to determine kind. The matcher's `repo.list_scorable_listings_for_user` now accepts a `mode` kwarg and filters by `kind = mode` when `mode != 'both'`, finally honoring the wizard's `mode` selection. Frontend gets one optional `kind?: 'wg' | 'flat'` field on the TS `Listing` type and one neutral `<StatusPill>` in the listing card / drawer (`{kind === 'flat' ? 'Whole flat' : 'WG room'}`).

**Consequences:** The matcher honors `SearchProfile.mode` for the first time. Indexed lookup for the `WHERE kind = sp.mode` filter means the read cost is essentially free. Schema change is one additive column on one table — existing rows default to `'wg'` so the migration is invisible. Trade-off: every per-source scraper has to remember to set `kind` correctly; the protocol enforces it by making `kind` part of the search-stub return value (immutable from stub creation through `scrape_detail`, per the `Source` protocol). We considered (and rejected) inferring kind from `id` prefix at read time — it doesn't work for sources like Kleinanzeigen that serve both kinds under the same id namespace.

**Introduced in:** this commit
