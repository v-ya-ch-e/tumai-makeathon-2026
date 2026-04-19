# Backend

The backend container hosts the FastAPI app + `wg_agent` package: JSON/SSE API, MySQL persistence, the matcher-only find-and-score loop (one asyncio task per user). A separate scraper container (`app/scraper/`) owns the shared `ListingRow` pool.

## File map

```text
backend/app/main.py              FastAPI app, lifespan (DB bootstrap + per-user agent resume), SPA mount, legacy /items routes
backend/app/scraper/
  __init__.py                    Package docstring; scraper is the sole writer of ListingRow + PhotoRow
  agent.py                       `ScraperAgent` async loop: per-source × per-kind iteration → skip-if-fresh → deep-scrape → upsert_global_listing → per-source deletion sweep
  main.py                        `python -m app.scraper.main` entrypoint: db.init_db + run_forever
  migrate_multi_source.py        `python -m app.scraper.migrate_multi_source` one-shot DB migration (widen TEXT columns, add `kind` + index, namespace existing wg-gesucht ids, force rescrape). Idempotent + transactional + `--dry-run`. See [`backend/app/scraper/README.md`](../backend/app/scraper/README.md)
  README.md                      Multi-source contract (id namespacing, kind, dedup) + deploy procedure
  SOURCE_*.md                    Per-site recon notes (one per source: wg-gesucht, tum-living, kleinanzeigen)
  sources/
    __init__.py                  Source registry; `build_sources()` reads `SCRAPER_ENABLED_SOURCES` (default `wg-gesucht`)
    base.py                      `Source` Protocol: `search(kind, profile)`, `scrape_detail(stub)`, `looks_like_block_page`, plus pacing constants per [MULTI_SOURCE_SCRAPER_PLAN D-5](./MULTI_SOURCE_SCRAPER_PLAN.md#open-design-decisions)
    wg_gesucht.py                Thin shim over `wg_agent/browser.anonymous_search` + `anonymous_scrape_listing`. `kind_supported = {'wg'}` (flat vertical TODO; see [SOURCE_WG_GESUCHT.md](../backend/app/scraper/SOURCE_WG_GESUCHT.md))
    tum_living.py                GraphQL + CSRF source (both kinds). Re-mints CSRF on `EBADCSRFTOKEN`
    kleinanzeigen.py             Anonymous httpx + bs4 source (both kinds). Munich-only locality catalogue for now
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

Defines the seven `*Row` tables: `UserRow`, `WgCredentialsRow`, `SearchProfileRow`, `ListingRow`, `PhotoRow`, `UserListingRow`, `UserActionRow`. Column-level documentation lives in [DATA_MODEL.md](./DATA_MODEL.md). `ListingRow` carries a `kind` column (`'wg'` | `'flat'`, indexed) per [ADR-021](./DECISIONS.md#adr-021-listing-kind-as-a-first-class-column) and explicit `Column(Text)` typing on every long-text column (`url`, `title`, `city`, `district`, `address`, `description`, `scrape_error`) per [MULTI_SOURCE_SCRAPER_PLAN D-11](./MULTI_SOURCE_SCRAPER_PLAN.md#open-design-decisions).

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
| `upsert_global_listing` | Merge `ListingRow` (scraper only); writes `kind`; bumps `scraped_at` + `scrape_status` + optional `scrape_error`; stamps `deleted_at` when `status="deleted"` |
| `save_user_match` | Upsert `UserListingRow` with optional `scored_against_scraped_at` (per-user matcher only) |
| `save_photos` | Replace `PhotoRow` rows for a listing (scraper only) |
| `list_user_listings` | `UserListingRow JOIN ListingRow` (excluding `deleted_at IS NOT NULL`) → matched `Listing` domain list for the user, ordered by score desc |
| `list_scorable_listings_for_user` | Global listings with the given `scrape_status` that this user has not yet scored, excluding soft-deleted rows. Optional `mode='wg'` / `'flat'` kwarg adds `WHERE kind = mode` (the matcher passes `sp.mode` so users with `mode='flat'` only see flats; per [ADR-021](./DECISIONS.md#adr-021-listing-kind-as-a-first-class-column)) |
| `list_stale_listings` | Listings whose `scraped_at < older_than` (scraper refresh input) |
| `append_user_action` | Insert `UserActionRow` |
| `list_actions_for_user` | Ordered `AgentAction` list (optional `limit`) |
| `mark_listing_deleted` | Tombstone a listing: `scrape_status='deleted'` + `deleted_at=now()` (scraper deletion sweep only) |
| `list_active_listing_ids` | Set of `ListingRow.id` where `scrape_status='full'` AND `deleted_at IS NULL`. Optional `source='wg-gesucht'` kwarg adds `WHERE id LIKE 'wg-gesucht:%'`; the per-source deletion sweep relies on this so a wg-gesucht-only pass never tombstones rows from the other sources (per [ADR-020](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing)) |
| `list_usernames_with_search_profile` | Every `SearchProfileRow.username`; used by `resume_user_agents` to decide which agents to respawn on boot |
| `row_to_domain_listing` | Rehydrate a global `ListingRow` into a domain `Listing` without a score (matcher uses this before evaluation) |

Internal helpers: `_listing_from_row`, `_components_from_row`, `_cover_photo_url`, `_best_commute_minutes`, `_kind_from_row` (safe row → domain coercion of the `kind` column), `_default_requirements`, `_user_row_to_profile`, `_parse_preference`.

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
| GET | `/api/users/{username}/stream` | SSE: replay the most-recent 200 persisted actions, then subscribe to the per-user broadcast channel (every active SSE connection for the same user gets every published action — fixes "matches show on only one device" when the same account is open twice). No end sentinel — continuous. | `ActionDTO` |
| POST | `/api/users/{username}/agent/start` | Spawn / refresh the per-user agent | 204 (400 if no search profile; 404 if user missing) |
| POST | `/api/users/{username}/agent/pause` | Cancel the per-user agent task | 204 |
| GET | `/api/users/{username}/agent` | `{ running: bool }` | inline JSON |
| GET | `/api/listings/{listing_id}` | Drawer payload | Query `username` required → `ListingDetailDTO` |

There is no hunt concept in v1: there is no `POST /users/{u}/hunts`, no `POST /hunts/{id}/stop`, no `GET /hunts/{id}`, no `GET /hunts/{id}/stream`. Listings, actions, and the SSE stream are all keyed by username.

## `periodic.py`

- **`UserAgent.run_match_pass`** — Loads `SearchProfile`, fetches candidates via `repo.list_scorable_listings_for_user(username, status='full', limit=max_listings)` (the shared pool minus listings already scored for this user, minus soft-deleted rows), emits one `search` action summarising "Matched N candidates from shared pool", then for each candidate: emits `new_listing`, calls [`commute.travel_times`](../backend/app/wg_agent/commute.py) against the user's `main_locations` in every profile-applicable mode (guarded: skipped when `listing.lat / lng` is `None`), calls [`places.nearby_places`](../backend/app/wg_agent/places.py) for the user's place-like preferences, passes both into [`evaluator.evaluate`](../backend/app/wg_agent/evaluator.py) (hard filter → deterministic components → single `brain.vibe_score` LLM call → composition), collapses the commute matrix into the fastest `(mode, minutes)` per location, and calls `repo.save_user_match(..., travel_minutes=..., nearby_places=..., components=..., veto_reason=..., scored_against_scraped_at=row.scraped_at)` for every listing (including vetoes). Emits either `"Rejected {id}: <veto_reason>"` or `"Scored {id}: 0.82"` with a `price 0.9 · size 1.0 · …` breakdown in `detail`. Every persisted action is also fanned out to every active SSE subscriber for that username via `_publish` (see "Registry" below). The matcher **never** calls `browser.anonymous_search`, `anonymous_scrape_listing`, `upsert_global_listing`, or `save_photos` — those belong to the scraper container.
- **`PeriodicUserMatcher`** — Async loop calling `run_match_pass`; sleeps `interval_minutes * 60` seconds between passes, optionally overridden by `WG_RESCAN_INTERVAL_MINUTES` when the env var parses to a positive int, emits `rescan` between passes. The loop has **no terminal state** — there is no `HuntStatus.done`; cancellation is the only exit. `asyncio.CancelledError` propagates so the registry can clean up. Rescans naturally pick up listings the scraper has added since the previous pass because `list_scorable_listings_for_user` reflects the live pool.
- **Registry** — `_ACTIVE_AGENTS` maps `username` → `Task`. `_SUBSCRIBERS` maps `username` → `list[asyncio.Queue]`, one queue per active SSE connection for that user. `_publish(username, action)` does a fire-and-forget `put_nowait` on every subscriber queue, so opening the same account on two devices results in *both* dashboards getting every event (a single shared queue would be unicast: each item goes to exactly one waiter). `spawn_user_agent(username, interval_minutes=...)` is idempotent — callers that pass through `PUT /search-profile` rely on that — and `cancel_user_agent(username)` cancels the task and returns `True` when it actually cancelled something. `subscribe(username) -> Queue` and `unsubscribe(username, queue)` are the SSE-side hooks (one queue per stream connection; `unsubscribe` runs in the SSE generator's `finally` so dropped clients don't leak). `is_agent_running(username)` reports the live-task state for `GET /agent`; `resume_user_agents()` is called from `lifespan` and spawns one agent per user with a `SearchProfileRow`.

## `app/scraper/agent.py`

- **`ScraperAgent.__init__`** — Builds a permissive `SearchProfile` template from env config (`SCRAPER_CITY`, `SCRAPER_MAX_RENT`) and resolves the active source list via `sources.build_sources()` (default just `wg-gesucht`; opt in to more via `SCRAPER_ENABLED_SOURCES=wg-gesucht,tum-living,kleinanzeigen`).
- **`ScraperAgent.run_once`** — Iterates `self._sources` sequentially. For each source, calls `_run_source(source)`, which iterates each `kind in source.kind_supported`, calls `source.search(kind, profile)` to get stubs, consults `ListingRow` via `session.get(ListingRow, stub.id)`, and gates the deep-scrape on `_needs_scrape(existing, source)`. `_needs_scrape` returns `True` when the row is absent, `scrape_status != "full"`, or `scraped_at` is older than `source.refresh_hours` (per-source, not global). On `True`, calls `_scrape_and_save_via(source, stub)`, which calls `source.scrape_detail(stub)`; on exception, upserts with `scrape_status='failed'` + `scrape_error`. On success, `_status_for(listing)` flips to `'full'` only when both description and coords are present (otherwise `'stub'`), then calls `repo.upsert_global_listing` + `repo.save_photos`. After all kinds for a source finish, `_sweep_deletions_for(source, seen_ids)` runs.
- **`ScraperAgent._sweep_deletions_for`** — Per-source-scoped sweep. Diffs `repo.list_active_listing_ids(source=source.name)` against the set of ids seen by that source this pass. A per-source `_missing_passes: dict[str, dict[str, int]]` counter (keyed by source name, then by listing id) is incremented for each active listing not in the search results; listings present in the search results have their counter reset. When the counter reaches `SCRAPER_DELETION_PASSES` (default `2`), `repo.mark_listing_deleted` stamps `scrape_status='deleted'` + `deleted_at=now()`. Counters are kept in memory only: a scraper restart effectively restarts each listing at zero passes. Per-source scoping (added with [ADR-020](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing)) means a wg-gesucht-only pass never tombstones Kleinanzeigen / TUM Living rows.
- **`ScraperAgent.run_forever`** — Wraps `run_once` in a `while True` that sleeps `SCRAPER_INTERVAL_SECONDS` between passes, logging and retrying on unexpected exceptions.
- **`app/scraper/main.py`** — Entrypoint for the scraper container (`python -m app.scraper.main`): calls `db.init_db()` (which bootstraps the schema via `SQLModel.metadata.create_all`), then `asyncio.run(ScraperAgent().run_forever())`.

## `app/scraper/sources/`

Per-source plugins consumed by `ScraperAgent` via `sources.build_sources()` ([ADR-020](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing) + [ADR-021](./DECISIONS.md#adr-021-listing-kind-as-a-first-class-column); design rationale in [MULTI_SOURCE_SCRAPER_PLAN.md D-4 / D-5](./MULTI_SOURCE_SCRAPER_PLAN.md#open-design-decisions)).

- **`base.py`** — `Source` Protocol with five class-level constants (`name`, `kind_supported`, `search_page_delay_seconds`, `detail_delay_seconds`, `max_pages`, `refresh_hours`) and three async methods (`search`, `scrape_detail`, `looks_like_block_page`). Stubs returned by `search` carry the namespaced `id` and final `kind` immutably.
- **`__init__.py`** — `_REGISTRY` maps source name → class; `build_sources(enabled=None)` reads `SCRAPER_ENABLED_SOURCES` (comma-separated; default `"wg-gesucht"`) and instantiates the matching plugins. Unknown names log a warning and are skipped; an empty result falls back to wg-gesucht so the existing deployment behavior is preserved.
- **`wg_gesucht.py`** — `WgGesuchtSource`. Thin shim over [`browser.anonymous_search`](../backend/app/wg_agent/browser.py) and `browser.anonymous_scrape_listing`. `kind_supported = {'wg'}` only — flat-vertical category id is unverified ([SOURCE_WG_GESUCHT.md](../backend/app/scraper/SOURCE_WG_GESUCHT.md) TODO #3). Pacing 1.5s search/detail, max 2 pages, 24h refresh.
- **`tum_living.py`** — `TumLivingSource`. GraphQL POSTs to `https://living.tum.de/graphql` with CSRF double-submit (mints `csrf-token` from `GET /api/me`; re-mints once on `EBADCSRFTOKEN`). Verbatim queries `LISTINGS_QUERY` / `DETAIL_QUERY` from [SOURCE_TUM_LIVING.md](../backend/app/scraper/SOURCE_TUM_LIVING.md). `kind_supported = {'wg', 'flat'}` (filter `type: SHARED_APARTMENT` vs `APARTMENT`). Pacing 2.5s, max 7 pages, 48h refresh.
- **`kleinanzeigen.py`** — `KleinanzeigenSource`. Anonymous httpx + bs4 with the verified `_BAD_CHARREF` patch for unterminated `&#8203;` charrefs. Munich-only (`KA_LOCALITY_BY_CITY = {"München": 6411, "Muenchen": 6411}`). `kind_supported = {'wg', 'flat'}` (URL slug `s-auf-zeit-wg` + `c199` vs `s-mietwohnung` + `c203`). Pacing 2.5s search / 3.5s detail, max 5 pages (robots.txt cap), 24h refresh.

## `app/scraper/migrate_multi_source.py`

One-shot DB migration for the multi-source rollout. Idempotent + transactional + supports `--dry-run`. Three steps:

1. Widen `listingrow.{url,title,city,district,address,description,scrape_error}` from `VARCHAR(255)` to `TEXT` and add `kind VARCHAR(255) NOT NULL DEFAULT 'wg'` + its index ([MULTI_SOURCE_SCRAPER_PLAN D-11](./MULTI_SOURCE_SCRAPER_PLAN.md#open-design-decisions)).
2. Namespace existing wg-gesucht ids in one transaction across `listingrow.id` + the three FK columns (D-2). Children first, parent last (FKs declare no `ON UPDATE CASCADE`).
3. Flip every `'full'` row to `'stub'` so the next scraper pass overwrites the previously-truncated descriptions through the now-wider `description TEXT` column.

Verifies G1 / G2 / G9 at the end. Run from `cd backend && venv/bin/python -m app.scraper.migrate_multi_source`. See [`backend/app/scraper/README.md`](../backend/app/scraper/README.md) for the full deploy procedure (containers stop → migrate → containers up → optionally flip `SCRAPER_ENABLED_SOURCES`).

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
| [`test_scraper.py`](../backend/tests/test_scraper.py) | `ScraperAgent` with the `wg-gesucht` source plugin + mocked `browser.*`: status/scrape_error branches, refresh-TTL skip, stale-refresh, deletion sweep (`mark_listing_deleted` after N missing passes, counter reset when a listing returns), and the per-source-scoped sweep (a wg-gesucht-only pass cannot tombstone Kleinanzeigen rows) | `cd backend && pytest tests/test_scraper.py` |
| [`tests/scraper/test_parse_tum_living.py`](../backend/tests/scraper/test_parse_tum_living.py) | Offline parser tests for the TUM Living source against captured GraphQL fixtures (`tests/scraper/fixtures/tum_living/`); plus block-page detector cases for `EBADCSRFTOKEN`, GraphQL-errors-with-null-data, and HTTP 5xx | `cd backend && pytest tests/scraper/test_parse_tum_living.py` |
| [`tests/scraper/test_parse_kleinanzeigen.py`](../backend/tests/scraper/test_parse_kleinanzeigen.py) | Offline parser tests for the Kleinanzeigen source against captured HTML fixtures (`tests/scraper/fixtures/kleinanzeigen/`); covers WG + flat search pages, one detail page, and the block-page detector | `cd backend && pytest tests/scraper/test_parse_kleinanzeigen.py` |
| [`tests/scraper/live/test_live_tum_living.py`](../backend/tests/scraper/live/test_live_tum_living.py) | Smoke test against the live TUM Living GraphQL endpoint. Skipped unless `SCRAPER_LIVE_TESTS=1`; run manually before a deploy | `SCRAPER_LIVE_TESTS=1 cd backend && pytest tests/scraper/live/` |
| [`test_migrate_multi_source.py`](../backend/tests/test_migrate_multi_source.py) | Steps 2 + 3 of the migration script against an in-memory SQLite engine: namespacing rewrites + FK rewrites, idempotent re-run, dry-run is non-mutating, force-rescrape flag flip | `cd backend && pytest tests/test_migrate_multi_source.py` |
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
