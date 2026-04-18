# Backend

The backend container hosts the FastAPI app + `wg_agent` package: JSON/SSE API, MySQL persistence, the matcher-only find-and-score loop (one asyncio task per user). A separate scraper container (`app/scraper/`) owns the shared `ListingRow` pool.

## File map

```text
backend/app/main.py              FastAPI app, lifespan (DB bootstrap + per-user agent resume), SPA mount, legacy /items routes
backend/app/scraper/
  __init__.py                    Package docstring; scraper is the sole writer of ListingRow + PhotoRow
  agent.py                       `ScraperAgent` async loop: search → skip-if-fresh → deep-scrape → upsert_global_listing → deletion sweep (mark_listing_deleted after N missing passes)
  main.py                        `python -m app.scraper.main` entrypoint: db.init_db + run_forever
backend/app/wg_agent/
  __init__.py                    Package docstring; points contributors to WG recon notes
  api.py                         `/api` router: users, search profile, credentials, per-user listings/actions/stream, agent start/pause/status, listing detail
  brain.py                       OpenAI chat calls: `vibe_score` (evaluator component); legacy `score_listing` / `draft_message` / `classify_reply` / `reply_to_landlord` kept as dead code for future messaging work
  browser.py                     URL builders, HTML parsers, httpx anonymous path, Playwright `WGBrowser` + factory
  commute.py                     Google Distance Matrix client (`travel_times`, `modes_for`); called from the per-user matcher before scoring
  crypto.py                      Fernet key resolution + encrypt/decrypt for credential blobs
  db.py                          SQLModel engine on MySQL (DSN assembled from DB_HOST/PORT/USER/PASSWORD/NAME, pool_pre_ping + pool_recycle), `init_db`, `get_session` dependency
  db_models.py                   `*Row` SQLModel table classes (see [DATA_MODEL.md](./DATA_MODEL.md))
  dto.py                         Pydantic DTOs + `*_to_dto` / `upsert_body_to_search_profile` converters
  evaluator.py                   Scorecard: `hard_filter` + deterministic components (price/size/wg_size/availability/commute/preferences) + `vibe_fit` + `compose`
  google_maps.py                 Shared async rate gate for backend Google Maps clients; keeps aggregate traffic below the configured cap across concurrent per-user agents
  geocoder.py                    Server-side Google Geocoding client with an in-process cache; used by `browser.anonymous_scrape_listing`
  models.py                      Domain Pydantic models + enums + `CITY_CATALOGUE`
  places.py                      Google Places API client for nearby amenity distances on user preferences
  periodic.py                    `UserAgent` matcher, `PeriodicUserMatcher` loop, per-user task registry, `spawn_user_agent` / `cancel_user_agent` / `is_agent_running` / `resume_user_agents`
  repo.py                        Domain ↔ `*Row` conversions; narrow CRUD surface for users, per-user matches, actions, and the global listing pool
```

## `main.py`

`FastAPI` is constructed with `lifespan=lifespan`. On startup the async context runs, in order: `wg_db.init_db()` (ensures Fernet key material and calls `SQLModel.metadata.create_all(engine)` to create any missing tables on MySQL), logs a password-free database identifier via `wg_db.describe_database()`, then `await wg_periodic.resume_user_agents()`. The API router from [`api.py`](../backend/app/wg_agent/api.py) is included under `/api`. Two sibling health probes are defined at the app level: `/health` and `/api/health` (both return `{"status": "ok"}`). When `frontend/dist/assets` exists, `/assets` is mounted; the catch-all `GET /{full_path:path}` returns `index.html` for non-`api/` and non-`assets/` paths (503 if the bundle is missing).

```20:29:backend/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .wg_agent import db as wg_db

    wg_db.init_db()
    logger.info("WG database: %s", wg_db.describe_database())
    from .wg_agent import periodic as wg_periodic

    await wg_periodic.resume_user_agents()
    yield
```

## `models.py`

- **`UserProfile`** — Local account (username, optional `email`, age, gender, `created_at`). Written by `repo.create_user` / `repo.update_user`; read by `repo.get_user`, `repo.get_user_by_email`, and API guards.
- **`ContactInfo`** — Student contact block for drafted messages (`brain.draft_message`). Kept as dead code for future messaging work, not the v1 JSON path.
- **`WGCredentials`** — wg-gesucht login or storage-state path. Encrypted via `repo.upsert_credentials`; optional for the v1 matcher (which never logs in).
- **`SearchProfile`** — Full requirement object for search URLs, scoring prompts, and schedules. Read/written through `repo` after DTO conversion. **Legacy / transitional fields** not stored in `SearchProfileRow` but still used by `browser.build_search_url` and `brain._requirements_summary` (sizes, rent type, districts, languages, notes, caps) are documented in [DATA_MODEL.md](./DATA_MODEL.md) and defaulted in [`dto.upsert_body_to_search_profile`](../backend/app/wg_agent/dto.py) / [`repo.get_search_profile`](../backend/app/wg_agent/repo.py).
- **`Listing`** — Normalized listing + evaluator output fields (`score`, `score_reason`, `match_reasons`, `mismatch_reasons`, `components`, `veto_reason`). Produced by `browser` parsers, mutated by [`evaluator.evaluate`](../backend/app/wg_agent/evaluator.py), persisted via `repo.upsert_global_listing` / `save_user_match`.
- **`NearbyPlace`** — One nearby real-world match for a place-like user preference (`gym`, `park`, `supermarket`, ...), including the nearest distance in meters when Google Places found one. Produced by [`places.py`](../backend/app/wg_agent/places.py), consumed by `preference_fit`, persisted on `UserListingRow.nearby_places`, and exposed in the drawer payload.
- **`ComponentScore`** — One row of the scorecard breakdown (`key`, `score`, `weight`, `evidence`, `hard_cap`, `missing_data`). Produced by the component functions in `evaluator.py`; serialized into `UserListingRow.components`.
- **`Message`**, **`ReplyAnalysis`**, **`ReplyIntent`** — Messaging and inbox semantics reserved for future work.
- **`ActionKind`** / **`AgentAction`** — Append-only log line kinds and payload. Written by API agent-control paths and `periodic.UserAgent` / `PeriodicUserMatcher`.
- **`HuntStatus`** / **`Hunt`** — Legacy aggregate types kept for import compatibility; no code path constructs them in v1.

Enums: **`Gender`**, **`RentType`**, **`MessageDirection`**, **`ReplyIntent`** — constrain domain fields and API string patterns (`CreateUserBody.gender`).

## `dto.py`

DTOs: `UserDTO`, `CreateUserBody`, `UpdateUserBody`, `SearchProfileDTO`, `UpsertSearchProfileBody`, `CredentialsBody`, `CredentialsStatusDTO`, `ActionDTO`, `ComponentDTO`, `ListingDTO`, `NearbyPlaceDTO`, `UserMatchesDTO`, `ListingDetailDTO`.

`UserDTO` / `CreateUserBody` / `UpdateUserBody` carry an optional `email: EmailStr` (unique at the DB level). `ListingDTO` carries `username: Optional[str]` as the owner scope (previously `hunt_id`), so listings are addressed per-user end-to-end.

Conversion helpers: `user_to_dto`, `search_profile_to_dto`, `upsert_body_to_search_profile`, `action_to_dto`, `component_to_dto`, `listing_to_dto`, `nearby_place_to_dto`.

**Three-layer rule:** HTTP handlers in [`api.py`](../backend/app/wg_agent/api.py) accept/return DTOs and call these helpers (or `upsert_body_to_search_profile`) to cross into [`models.py`](../backend/app/wg_agent/models.py) domain types. Handlers must not construct SQLModel rows. The documented exception is `_get_listing_detail`, which reads `*Row` tables directly to assemble `ListingDetailDTO` (including rehydrating `components` via `_components_dto_from_row`) — see [DATA_MODEL.md](./DATA_MODEL.md). `repo.py` remains the routine domain ↔ row boundary for mutations.

## `db.py`

- Requires five env vars: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`. `_resolve_database_url()` assembles the MySQL DSN (`mysql+pymysql://user:password@host:port/name?charset=utf8mb4`) at import time, URL-encoding user + password. Any missing / empty var → a single `RuntimeError` listing every missing name so contributors see all fixups at once.
- `create_engine(..., pool_pre_ping=True, pool_recycle=1800)` — standard MySQL hygiene for long-lived RDS connections.
- `describe_database()` returns a password-free `user@host:port/name` string; entrypoints use it for startup logs instead of printing the raw DSN.
- `init_db()` calls `crypto.ensure_key()` and then `SQLModel.metadata.create_all(engine)`, which creates any missing tables on first boot and silently no-ops once the schema is already up to date. No Alembic — destructive schema changes require a `DROP DATABASE; CREATE DATABASE` (see [SETUP.md](./SETUP.md#reset-the-database)).
- `get_session()` is a FastAPI dependency yielding a `Session` context manager.
- Tests bypass this module's engine: [`backend/tests/conftest.py`](../backend/tests/conftest.py) sets inert `DB_*` placeholders before imports (so `db.py` can construct a phantom engine without crashing), and individual tests build their own in-memory SQLite engine + monkey-patch `db_module.engine`.

## `db_models.py`

Defines the eight `*Row` tables: `UserRow`, `WgCredentialsRow`, `SearchProfileRow`, `ListingRow`, `PhotoRow`, `UserListingRow`, `UserActionRow`. Column-level documentation lives in [DATA_MODEL.md](./DATA_MODEL.md).

## `crypto.py`

Key order: `WG_SECRET_KEY` (must be a valid Fernet key string) else read `~/.wg_hunter/secret.key`; if missing, generate a key, write the file with mode `600`, parent dir `700`. `encrypt` / `decrypt` wrap `Fernet` and UTF-8 strings.

## `repo.py`

Narrow surface (domain in/out unless noted). Write ownership is split: the scraper calls `upsert_global_listing` + `save_photos` + `mark_listing_deleted`; the per-user matcher calls `save_user_match` + `append_user_action`.

| Function | Purpose |
| --- | --- |
| `create_user` | Insert `UserRow` from `UserProfile` (including `email`) |
| `get_user` | `UserRow` → `UserProfile` or `None` |
| `get_user_by_email` | Unique-email lookup; returns `UserProfile` or `None` (used by `POST` / `PUT` for 409 detection) |
| `update_user` | Mutate `UserRow.email` / `age` / `gender` from a `UserProfile`; returns the refreshed domain user (used by `PUT /api/users/{username}`) |
| `upsert_search_profile` | Merge `SearchProfile` into `SearchProfileRow` |
| `get_search_profile` | Row → `SearchProfile`, deriving `city` from `main_locations[0].label` and `max_rent_eur` from `price_max_eur` when absent (parses `main_locations` via `PlaceLocation.model_validate`) |
| `upsert_credentials` | JSON-encode `WGCredentials`, Fernet-encrypt, upsert `WgCredentialsRow` |
| `delete_credentials` | Remove credential row |
| `credentials_status` | `(connected, saved_at)` tuple |
| `upsert_global_listing` | Merge `ListingRow` (scraper only); bumps `scraped_at` + `scrape_status` + optional `scrape_error`; stamps `deleted_at` when `status="deleted"` |
| `save_user_match` | Upsert `UserListingRow` with optional `scored_against_scraped_at` (per-user matcher only) |
| `save_photos` | Replace `PhotoRow` rows for a listing (scraper only) |
| `list_user_listings` | `UserListingRow JOIN ListingRow` (excluding `deleted_at IS NOT NULL`) → matched `Listing` domain list for the user, ordered by score desc |
| `list_scorable_listings_for_user` | Global listings with the given `scrape_status` that this user has not yet scored, excluding soft-deleted rows (matcher input) |
| `list_stale_listings` | Listings whose `scraped_at < older_than` (scraper refresh input) |
| `append_user_action` | Insert `UserActionRow` |
| `list_actions_for_user` | Ordered `AgentAction` list (optional `limit`) |
| `mark_listing_deleted` | Tombstone a listing: `scrape_status='deleted'` + `deleted_at=now()` (scraper deletion sweep only) |
| `list_active_listing_ids` | Set of `ListingRow.id` where `scrape_status='full'` AND `deleted_at IS NULL` (scraper deletion sweep input) |
| `list_usernames_with_search_profile` | Every `SearchProfileRow.username`; used by `resume_user_agents` to decide which agents to respawn on boot |
| `row_to_domain_listing` | Rehydrate a global `ListingRow` into a domain `Listing` without a score (matcher uses this before evaluation) |

Internal helpers: `_listing_from_row`, `_components_from_row`, `_cover_photo_url`, `_best_commute_minutes`, `_default_requirements`, `_user_row_to_profile`, `_parse_preference`.

## `api.py`

| Method | Path | Purpose | Bodies / models |
| --- | --- | --- | --- |
| POST | `/api/users` | Create local user (409 on duplicate username or email) | `CreateUserBody` → `UserDTO` |
| GET | `/api/users/{username}` | Fetch user | `UserDTO` |
| PUT | `/api/users/{username}` | Update `email` / `age` / `gender` on an existing user (username stays immutable) | `UpdateUserBody` → `UserDTO` |
| PUT | `/api/users/{username}/search-profile` | Upsert wizard profile; side effect: spawns the per-user agent (idempotent) | `UpsertSearchProfileBody` → `SearchProfileDTO` |
| GET | `/api/users/{username}/search-profile` | Fetch profile | `SearchProfileDTO` |
| PUT | `/api/users/{username}/credentials` | Store encrypted creds | `CredentialsBody` → 204 |
| DELETE | `/api/users/{username}/credentials` | Remove creds | 204 |
| GET | `/api/users/{username}/credentials` | Connection metadata | `CredentialsStatusDTO` |
| GET | `/api/users/{username}/listings` | Ranked matched listings for the user | `ListingDTO[]` |
| GET | `/api/users/{username}/actions?limit=` | Paginated action log | `ActionDTO[]` |
| GET | `/api/users/{username}/stream` | SSE: replay persisted actions, then live queue + DB poll (no end sentinel — continuous) | `ActionDTO` |
| POST | `/api/users/{username}/agent/start` | Spawn / refresh the per-user agent | 204 (400 if no search profile; 404 if user missing) |
| POST | `/api/users/{username}/agent/pause` | Cancel the per-user agent task | 204 |
| GET | `/api/users/{username}/agent` | `{ running: bool }` | inline JSON |
| GET | `/api/listings/{listing_id}` | Drawer payload | Query `username` required → `ListingDetailDTO` |

There is no hunt concept in v1: there is no `POST /users/{u}/hunts`, no `POST /hunts/{id}/stop`, no `GET /hunts/{id}`, no `GET /hunts/{id}/stream`. Listings, actions, and the SSE stream are all keyed by username.

## `periodic.py`

- **`UserAgent.run_match_pass`** — Loads `SearchProfile`, fetches candidates via `repo.list_scorable_listings_for_user(username, status='full', limit=max_listings)` (the shared pool minus listings already scored for this user, minus soft-deleted rows), emits one `search` action summarising "Matched N candidates from shared pool", then for each candidate: emits `new_listing`, calls [`commute.travel_times`](../backend/app/wg_agent/commute.py) against the user's `main_locations` in every profile-applicable mode (guarded: skipped when `listing.lat / lng` is `None`), calls [`places.nearby_places`](../backend/app/wg_agent/places.py) for the user's place-like preferences, passes both into [`evaluator.evaluate`](../backend/app/wg_agent/evaluator.py) (hard filter → deterministic components → single `brain.vibe_score` LLM call → composition), collapses the commute matrix into the fastest `(mode, minutes)` per location, and calls `repo.save_user_match(..., travel_minutes=..., nearby_places=..., components=..., veto_reason=..., scored_against_scraped_at=row.scraped_at)` for every listing (including vetoes). Emits either `"Rejected {id}: <veto_reason>"` or `"Scored {id}: 0.82"` with a `price 0.9 · size 1.0 · …` breakdown in `detail`. Every persisted action also lands on the per-user asyncio queue for SSE (`_safe_put`). The matcher **never** calls `browser.anonymous_search`, `anonymous_scrape_listing`, `upsert_global_listing`, or `save_photos` — those belong to the scraper container.
- **`PeriodicUserMatcher`** — Async loop calling `run_match_pass`; sleeps `interval_minutes * 60` seconds between passes, optionally overridden by `WG_RESCAN_INTERVAL_MINUTES` when the env var parses to a positive int, emits `rescan` between passes. The loop has **no terminal state** — there is no `HuntStatus.done`; cancellation is the only exit. `asyncio.CancelledError` propagates so the registry can clean up. Rescans naturally pick up listings the scraper has added since the previous pass because `list_scorable_listings_for_user` reflects the live pool.
- **Registry** — `_ACTIVE_AGENTS` maps `username` → `Task`, `_EVENT_QUEUES` maps `username` → `Queue`. `spawn_user_agent(username, interval_minutes=...)` creates the queue + task if one isn't already running (it is idempotent — callers that pass through `PUT /search-profile` rely on that); `cancel_user_agent(username)` cancels the task and returns `True` when it actually cancelled something; `event_queue_for(username)` exposes the queue to SSE; `is_agent_running(username)` reports the live-task state for `GET /agent`; `resume_user_agents()` is called from `lifespan` and spawns one agent per user with a `SearchProfileRow`.

## `app/scraper/agent.py`

- **`ScraperAgent.run_once`** — Builds a permissive `SearchProfile` from env config (`SCRAPER_CITY`, `SCRAPER_MAX_RENT`, `SCRAPER_MAX_PAGES`), calls `browser.anonymous_search`, and for each returned stub: consults `ListingRow` via `session.get(ListingRow, stub.id)`. `_needs_scrape` returns `True` when the row is absent, when `scrape_status != "full"`, or when `scraped_at` is older than `SCRAPER_REFRESH_HOURS`. On `True`, calls `browser.anonymous_scrape_listing`; on exception, upserts with `scrape_status='failed'` + `scrape_error`. On success, `_status_for(listing)` flips to `'full'` only when both description and coords are present (otherwise `'stub'`), then calls `repo.upsert_global_listing` + `repo.save_photos`. After the per-listing loop finishes, `_sweep_deletions` runs.
- **`ScraperAgent._sweep_deletions`** — Diffs `repo.list_active_listing_ids()` against the set of ids returned by the just-completed search. A process-local `_missing_passes: dict[str, int]` counter is incremented for each active listing not in the search results; listings present in the search results have their counter reset. When the counter reaches `SCRAPER_DELETION_PASSES` (default `2`), `repo.mark_listing_deleted` stamps `scrape_status='deleted'` + `deleted_at=now()`. Counters are kept in memory only: a scraper restart effectively restarts each listing at zero passes.
- **`ScraperAgent.run_forever`** — Wraps `run_once` in a `while True` that sleeps `SCRAPER_INTERVAL_SECONDS` between passes, logging and retrying on unexpected exceptions.
- **`app/scraper/main.py`** — Entrypoint for the scraper container (`python -m app.scraper.main`): calls `db.init_db()` (which bootstraps the schema via `SQLModel.metadata.create_all`), then `asyncio.run(ScraperAgent().run_forever())`.

## `browser.py`

1. **Pure parsing** — `build_search_url`, `parse_search_page`, `parse_listing_page` (unit-tested via fixtures and `test_wg_parser`). The detail parser prefers scoped DOM selectors over `get_text` regex: `_section_pairs` walks forward from a section `<h2>` until the next `<h2>` to collect `{label: value}` rows (Kosten, Verfügbarkeit), `_wg_details_lines` returns the WG-Details `<li>`s for languages/pets/smoking, `_parse_address_panel` splits the Adresse detail into `(street, postal_code, city, district)`, and the description comes from `#ad_description_text` with embedded `<script>`/`<iframe>`/`div-gpt-ad-*` stripped. Every DOM path falls back to the original full-text regex so a DOM shift degrades gracefully instead of nulling fields. `_parse_map_lat_lng` extracts the listing's own map pin from the `map_config.markers` script block, giving `(lat, lng)` for free (see ADR-014).
2. **Anonymous httpx** — `anonymous_search`, `anonymous_scrape_listing` using shared headers, timeouts, and polite delays (`ANONYMOUS_PAGE_DELAY_SECONDS`). `anonymous_scrape_listing` trusts the map-pin coordinates produced by `parse_listing_page` when present and only calls [`geocoder.geocode`](../backend/app/wg_agent/geocoder.py) as a fallback (best string: `listing.address` → `"{district}, {city or req_city}"`), so `listing.lat` / `listing.lng` are populated before `repo.upsert_global_listing` persists the row.
3. **Playwright driver** — `WGBrowser` (`search`, `scrape_listing`, `send_message`, `fetch_inbox`) plus `launch_browser` for authenticated flows retained for future messaging.

## `geocoder.py`

Thin async client around Google Geocoding, used only as a fallback when `browser._parse_map_lat_lng` didn't find a map pin on the detail page (ADR-014, ADR-017). `geocode(address)` returns `(lat, lng)` or `None` and never raises. Reads `GOOGLE_MAPS_SERVER_KEY` from the environment; if unset, returns `None` without touching the network so local dev works without the key. An in-process dict caches results keyed on `address.strip().lower()` (cleared when it passes 1024 entries) so rescans of the same listing don't re-bill the same string. Every outbound call first waits on the shared `google_maps.wait_turn()` gate, which defaults to `8 req/s` process-wide and can be tuned with `GOOGLE_MAPS_MAX_RPS`.

## `commute.py`

Thin async client around Google Distance Matrix. `travel_times(origin, destinations, modes)` returns `{(place_id, mode): seconds}` for reachable pairs only — absent entries mean "no route" or "API failed", so callers treat the returned dict as authoritative. Issues one GET per travel mode with a one-origin/many-destinations shape; malformed rows and per-pair non-`OK` elements are skipped silently. Reuses the same `GOOGLE_MAPS_SERVER_KEY` as [`geocoder.py`](../backend/app/wg_agent/geocoder.py); without the key, the function short-circuits to `{}` so dev flows stay offline-friendly. Each request also waits on the shared `google_maps.wait_turn()` gate so concurrent per-user agents do not burst above the configured provider cap. `modes_for(sp)` derives the mode list straight from the search profile: always `TRANSIT`, plus `BICYCLE` when `sp.has_bike`, plus `DRIVE` when `sp.has_car`.

## `places.py`

Thin async client around Google Places API (New). `nearby_places(origin, preferences)` returns `{pref_key: NearbyPlace}` for the subset of user preferences that map cleanly to real nearby amenities (`gym`, `park`, `supermarket`, `cafe`, `bars`, `library`, `coworking`, `nightlife`, `green_space`, `public_transport`). Type-backed preferences use Nearby Search (New); `coworking` uses Text Search (New) with a distance-biased circle. Distances are computed from the returned place coordinates, cached in-process, and fail-soft: missing key or HTTP issues degrade to `{}` so the evaluator can fall back to keyword evidence instead of crashing. Requests pass through the same shared Google throttle as geocoding and routing.

## `brain.py`

- `vibe_score(listing, requirements, *, nearby_places=None) -> VibeScore` — Narrow Chat Completions JSON-object call used by [`evaluator.vibe_fit`](../backend/app/wg_agent/evaluator.py). The prompt is explicitly told **not** to judge price, size, WG size, or commute — only how well `listing.description` + `listing.district` match the user's free-form `notes`, `preferred_districts` / `avoid_districts`, and the nearby-place/lifestyle context that matters to those preferences. Returns a validated Pydantic model `{score: float, evidence: list[str]}`; `ValidationError` bubbles up so the evaluator can mark the vibe component `missing_data`.
- `score_listing`, `draft_message`, `classify_reply`, `reply_to_landlord` — Legacy LLM helpers kept in-tree but no longer wired to any route in v1. They are reserved for a future messaging loop.

## `evaluator.py`

The scorecard pipeline (ADR-015). Public functions:

- `hard_filter(listing, profile) -> VetoResult | None` — Deterministic vetoes: over `max_rent_eur`, city mismatch (with Muenchen/München normalization), district in `avoid_districts`, `available_from > move_in_until`, weight-5 structured preference directly contradicted.
- Component functions (pure): `price_fit`, `size_fit`, `wg_size_fit`, `availability_fit`, `commute_fit`, `preference_fit`. Each returns a `ComponentScore` with a score in `[0, 1]`, composition `weight`, `evidence` list, optional `hard_cap`, and `missing_data` flag. `preference_fit` now prefers `nearby_places` distance facts for place-like preferences before falling back to description keywords.
- `vibe_fit(listing, profile, *, nearby_places=None) -> ComponentScore` — async wrapper around `brain.vibe_score` that degrades to `missing_data=True` on `ValidationError` or any exception.
- `compose(components, *, veto=None) -> EvaluationResult` — Weighted mean across non-`missing_data` components, take the minimum of every `hard_cap` present, clamp to `[0, 1]`; veto short-circuits to `score=0.0`.
- `evaluate(listing, profile, *, travel_times=None) -> EvaluationResult` — End-to-end facade; what `UserAgent.run_match_pass` calls.

Curve tuning (weights and thresholds) lives at the top of the module in `COMPONENT_WEIGHTS`, `DEFAULT_COMMUTE_BUDGET_MIN`, and `PREFERENCE_KEYWORDS`; unit tests pin each curve's boundaries in [`test_evaluator.py`](../backend/tests/test_evaluator.py).

## Tests

| File | Role | Command |
| --- | --- | --- |
| [`test_wg_parser.py`](../backend/tests/test_wg_parser.py) | Cached HTML fixtures under `tests/fixtures/`; asserts parser output shape and locks down the structured fields the scorer relies on (address split, available-from/to, languages, pets/smoking, description-doesn't-leak-page-chrome, map-pin lat/lng) | `cd backend && python tests/test_wg_parser.py` (or `pytest tests/test_wg_parser.py`) |
| [`test_repo.py`](../backend/tests/test_repo.py) | In-memory SQLite round-trip for `repo` + crypto (user/email, search profile, credentials, per-user match rows, soft-delete exclusion, per-user action log) | `cd backend && pytest tests/test_repo.py` |
| [`test_periodic.py`](../backend/tests/test_periodic.py) | `UserAgent` / `PeriodicUserMatcher` with pre-seeded global pool + mocked I/O (commute-reaches-evaluator, nearby-places-persist, lat-missing guard, `PeriodicUserMatcher` cancellation clears registry) | `cd backend && pytest tests/test_periodic.py` |
| [`test_scraper.py`](../backend/tests/test_scraper.py) | `ScraperAgent` with mocked `browser.*`: status/scrape_error branches, refresh-TTL skip, stale-refresh, and the deletion sweep (`mark_listing_deleted` after N missing passes, counter reset when a listing returns) | `cd backend && pytest tests/test_scraper.py` |
| [`test_commute.py`](../backend/tests/test_commute.py) | Route Matrix client with monkey-patched `httpx.post` | `cd backend && pytest tests/test_commute.py` |
| [`test_places.py`](../backend/tests/test_places.py) | Nearby-place client with mocked `httpx` (cache + fail-soft paths) | `cd backend && pytest tests/test_places.py` |
| [`test_brain.py`](../backend/tests/test_brain.py) | `_listing_summary` commute-block formatting (no LLM) | `cd backend && pytest tests/test_brain.py` |
| [`test_evaluator.py`](../backend/tests/test_evaluator.py) | Scorecard evaluator: `hard_filter` paths, per-component curves, `compose` arithmetic + caps, `vibe_fit` graceful degradation (no LLM, no DB) | `cd backend && pytest tests/test_evaluator.py` |
| [`test_geocoder.py`](../backend/tests/test_geocoder.py) | Geocoding client with mocked `httpx` (cache + fail-soft paths) | `cd backend && pytest tests/test_geocoder.py` |
| [`test_dto.py`](../backend/tests/test_dto.py) | `UpsertSearchProfileBody` → `SearchProfile` conversion rules (e.g. München normalization for commute anchors); `CreateUserBody` accepts optional `email` | `cd backend && pytest tests/test_dto.py` |
| [`test_google_maps.py`](../backend/tests/test_google_maps.py) | Shared async throttle in [`google_maps.py`](../backend/app/wg_agent/google_maps.py) — verifies rate-limit spacing across concurrent callers | `cd backend && pytest tests/test_google_maps.py` |

Run the whole suite with `cd backend && pytest` after activating the venv.

## Schema evolution

There is no Alembic in the tree. Both entrypoints call `db.init_db()` on startup, which in turn calls `SQLModel.metadata.create_all(engine)`. Behaviour:

- **First boot against an empty DB** — all tables + FKs + indexes declared in [`db_models.py`](../backend/app/wg_agent/db_models.py) get created.
- **Subsequent boots** — no-op (SQLAlchemy checks `information_schema`, skips existing tables).
- **Adding a column to an existing table** — **not done by `create_all`.** You must `DROP DATABASE wg_hunter; CREATE DATABASE wg_hunter;` and restart the backend to pick up the new schema. See [SETUP.md "Reset the database"](./SETUP.md#reset-the-database).

This trade-off matches the project's dev workflow: schema changes are frequent and incompatible with pre-existing rows, and the team shares one AWS RDS instance that we reset as a whole when schema moves. If the project outgrows that assumption, [the first commit that adds Alembic back in](https://github.com/sqlalchemy/alembic) is ten lines of `alembic init` plus one `--autogenerate` run.
