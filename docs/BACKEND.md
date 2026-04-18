# Backend

FastAPI entrypoint plus the `wg_agent` package: JSON/SSE API, SQLite persistence, anonymous wg-gesucht scraping, and the v1 find-and-score loop.

## File map

```text
backend/app/main.py              FastAPI app, lifespan (DB + Alembic + hunt resume), SPA mount, legacy /items routes
backend/app/wg_agent/
  __init__.py                    Package docstring; points contributors to WG recon notes
  api.py                         `/api` router: users, search profile, credentials, hunts, SSE stream, listing detail
  brain.py                       OpenAI chat calls: `vibe_score` (evaluator component), `draft_message`, `classify_reply`; legacy `score_listing` kept for orchestrator
  browser.py                     URL builders, HTML parsers, httpx anonymous path, Playwright `WGBrowser` + factory
  commute.py                     Google Distance Matrix client (`travel_times`, `modes_for`); called from the hunter before scoring
  crypto.py                      Fernet key resolution + encrypt/decrypt for credential blobs
  db.py                          SQLModel engine, WAL pragma, `init_db`, `get_session` dependency
  db_models.py                   `*Row` SQLModel table classes (see [DATA_MODEL.md](./DATA_MODEL.md))
  dto.py                         Pydantic DTOs + `*_to_dto` / `upsert_body_to_search_profile` converters
  evaluator.py                   Scorecard: `hard_filter` + deterministic components (price/size/wg_size/availability/commute/preferences) + `vibe_fit` + `compose`
  google_maps.py                 Shared async rate gate for backend Google Maps clients; keeps aggregate traffic below the configured cap across concurrent hunts
  geocoder.py                    Server-side Google Geocoding client with an in-process cache; used by `browser.anonymous_scrape_listing`
  models.py                      Domain Pydantic models + enums + `CITY_CATALOGUE`
  orchestrator.py                Legacy `HuntOrchestrator` (Playwright messaging loop) for tests / future work
  places.py                      Google Places API client for nearby amenity distances on user preferences
  periodic.py                    `HuntEngine`, `PeriodicHunter`, hunter task registry, `resume_running_hunts`
  repo.py                        Domain ↔ `*Row` conversions; narrow CRUD surface for hunts, listings, actions, users
```

## `main.py`

`FastAPI` is constructed with `lifespan=lifespan`. On startup the async context runs, in order: `wg_db.init_db()` (ensures Fernet key material and touches the engine), logs `DATABASE_URL`, loads Alembic `Config` from `backend/alembic.ini` with `script_location` pointing at `backend/alembic`, runs `command.upgrade(cfg, "head")`, then `await wg_periodic.resume_running_hunts()`. The API router from [`api.py`](../backend/app/wg_agent/api.py) is included under `/api`. Two sibling health probes are defined at the app level: `/health` and `/api/health` (both return `{"status": "ok"}`). When `frontend/dist/assets` exists, `/assets` is mounted; the catch-all `GET /{full_path:path}` returns `index.html` for non-`api/` and non-`assets/` paths (503 if the bundle is missing).

```24:37:backend/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .wg_agent import db as wg_db

    wg_db.init_db()
    logger.info("WG database URL: %s", wg_db.DATABASE_URL)
    alembic_ini = BACKEND_DIR / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    from .wg_agent import periodic as wg_periodic

    await wg_periodic.resume_running_hunts()
    yield
```

## `models.py`

- **`UserProfile`** — Local account (username, age, gender, `created_at`). Written by `repo.create_user`; read by `repo.get_user` and API guards.
- **`ContactInfo`** — Student contact block for drafted messages (`brain.draft_message`). Used by the legacy orchestrator tests, not the v1 JSON hunt path.
- **`WGCredentials`** — wg-gesucht login or storage-state path. Encrypted via `repo.upsert_credentials`; optional for v1 hunting.
- **`SearchProfile`** — Full requirement object for search URLs, scoring prompts, and schedules. Read/written through `repo` after DTO conversion. **Legacy / transitional fields** not stored in `SearchProfileRow` but still used by `browser.build_search_url` and `brain._requirements_summary` (sizes, rent type, districts, languages, notes, caps) are documented in [DATA_MODEL.md](./DATA_MODEL.md) and defaulted in [`dto.upsert_body_to_search_profile`](../backend/app/wg_agent/dto.py) / [`repo.get_search_profile`](../backend/app/wg_agent/repo.py).
- **`Listing`** — Normalized listing + evaluator output fields (`score`, `score_reason`, `match_reasons`, `mismatch_reasons`, `components`, `veto_reason`). Produced by `browser` parsers, mutated by [`evaluator.evaluate`](../backend/app/wg_agent/evaluator.py), persisted via `repo.upsert_listing` / `save_score`.
- **`NearbyPlace`** — One nearby real-world match for a place-like user preference (`gym`, `park`, `supermarket`, ...), including the nearest distance in meters when Google Places found one. Produced by [`places.py`](../backend/app/wg_agent/places.py), consumed by `preference_fit`, persisted on `ListingScoreRow.nearby_places`, and exposed in the drawer payload.
- **`ComponentScore`** — One row of the scorecard breakdown (`key`, `score`, `weight`, `evidence`, `hard_cap`, `missing_data`). Produced by the component functions in `evaluator.py`; serialized into `ListingScoreRow.components`.
- **`Message`**, **`ReplyAnalysis`**, **`ReplyIntent`** — Messaging and inbox semantics for future orchestrator flows.
- **`ActionKind`** / **`AgentAction`** — Append-only log line kinds and payload. Written by API boot/stop paths and `periodic.HuntEngine` / `PeriodicHunter`.
- **`HuntStatus`** / **`Hunt`** — Aggregate hunt state (`requirements` holds the embedded `SearchProfile`). Built by `repo.get_hunt` for API responses.

Enums: **`Gender`**, **`RentType`**, **`MessageDirection`**, **`ReplyIntent`** — constrain domain fields and API string patterns (`CreateUserBody.gender`).

## `dto.py`

DTOs: `UserDTO`, `CreateUserBody`, `UpdateUserBody`, `SearchProfileDTO`, `UpsertSearchProfileBody`, `CredentialsBody`, `CredentialsStatusDTO`, `CreateHuntBody`, `ActionDTO`, `ComponentDTO`, `ListingDTO`, `NearbyPlaceDTO`, `HuntDTO`, `ListingDetailDTO`.

Conversion helpers: `user_to_dto`, `search_profile_to_dto`, `upsert_body_to_search_profile`, `action_to_dto`, `component_to_dto`, `listing_to_dto`, `hunt_to_dto`.

**Three-layer rule:** HTTP handlers in [`api.py`](../backend/app/wg_agent/api.py) accept/return DTOs and call these helpers (or `upsert_body_to_search_profile`) to cross into [`models.py`](../backend/app/wg_agent/models.py) domain types. Handlers must not construct SQLModel rows. The documented exception is `_get_listing_detail`, which reads `*Row` tables directly to assemble `ListingDetailDTO` (including rehydrating `components` via `_components_dto_from_row`) — see [DATA_MODEL.md](./DATA_MODEL.md). `repo.py` remains the routine domain ↔ row boundary for mutations.

## `db.py`

- Builds `DATABASE_URL` from `WG_DB_URL` or default `sqlite:///~/.wg_hunter/app.db` (path expanded under the user home).
- SQLite engine uses `check_same_thread=False`; a connect listener runs `PRAGMA journal_mode=WAL` for SQLite dialects only.
- `init_db()` calls `crypto.ensure_key()` then opens/closes a connection (lightweight “touch” after Alembic owns schema).
- `get_session()` is a FastAPI dependency yielding a `Session` context manager.

## `db_models.py`

Defines the nine `*Row` tables: `UserRow`, `WgCredentialsRow`, `SearchProfileRow`, `HuntRow`, `ListingRow`, `PhotoRow`, `ListingScoreRow`, `AgentActionRow`, `MessageRow`. Column-level documentation lives in [DATA_MODEL.md](./DATA_MODEL.md).

## `crypto.py`

Key order: `WG_SECRET_KEY` (must be a valid Fernet key string) else read `~/.wg_hunter/secret.key`; if missing, generate a key, write the file with mode `600`, parent dir `700`. `encrypt` / `decrypt` wrap `Fernet` and UTF-8 strings.

## `repo.py`

Narrow surface (domain in/out unless noted):

| Function | Purpose |
| --- | --- |
| `create_user` | Insert `UserRow` from `UserProfile` |
| `get_user` | `UserRow` → `UserProfile` or `None` |
| `update_user` | Mutate `UserRow.age` / `UserRow.gender` from a `UserProfile`; returns the refreshed domain user (used by `PUT /api/users/{username}`) |
| `upsert_search_profile` | Merge `SearchProfile` into `SearchProfileRow` |
| `get_search_profile` | Row → `SearchProfile`, deriving `city` from `main_locations[0].label` and `max_rent_eur` from `price_max_eur` when absent (parses `main_locations` via `PlaceLocation.model_validate`) |
| `upsert_credentials` | JSON-encode `WGCredentials`, Fernet-encrypt, upsert `WgCredentialsRow` |
| `delete_credentials` | Remove credential row |
| `credentials_status` | `(connected, saved_at)` tuple |
| `create_hunt` | Insert `HuntRow` (`pending`), return assembled `Hunt` |
| `get_hunt` | Join hunt row, search profile, listings, actions → domain `Hunt` |
| `update_hunt_status` | Mutate `HuntRow.status` / optional `stopped_at` |
| `append_action` | Insert `AgentActionRow` |
| `upsert_listing` | Merge `ListingRow`, preserve `first_seen_at` |
| `save_score` | Upsert `ListingScoreRow` |
| `save_photos` | Replace `PhotoRow` rows for a listing |
| `list_hunts_by_status` | All hunts matching a `HuntStatus` |
| `list_listings_for_hunt` | Listings + latest score → `Listing` domain list |
| `list_actions_for_hunt` | Ordered `AgentAction` list |

Internal helpers: `_listing_from_row`, `_default_requirements`.

## `api.py`

| Method | Path | Purpose | Bodies / models |
| --- | --- | --- | --- |
| POST | `/api/users` | Create local user | `CreateUserBody` → `UserDTO` |
| GET | `/api/users/{username}` | Fetch user | `UserDTO` |
| PUT | `/api/users/{username}` | Update age / gender on an existing user (username stays immutable) | `UpdateUserBody` → `UserDTO` |
| PUT | `/api/users/{username}/search-profile` | Upsert wizard profile | `UpsertSearchProfileBody` → `SearchProfileDTO` |
| GET | `/api/users/{username}/search-profile` | Fetch profile | `SearchProfileDTO` |
| PUT | `/api/users/{username}/credentials` | Store encrypted creds | `CredentialsBody` → 204 |
| DELETE | `/api/users/{username}/credentials` | Remove creds | 204 |
| GET | `/api/users/{username}/credentials` | Connection metadata | `CredentialsStatusDTO` |
| POST | `/api/users/{username}/hunts` | Create hunt, spawn task | `CreateHuntBody` → `HuntDTO` |
| POST | `/api/hunts/{hunt_id}/stop` | Cancel asyncio task, mark done | `HuntDTO` |
| GET | `/api/hunts/{hunt_id}` | Full hunt snapshot | `HuntDTO` |
| GET | `/api/hunts/{hunt_id}/stream` | SSE JSON lines + keep-alive | `AgentAction` / terminal `stream-end` object |
| GET | `/api/listings/{listing_id}` | Drawer payload | Query `hunt_id` required → `ListingDetailDTO` |

`HuntRequest` remains in this module as a Pydantic body shape for [`test_orchestrator.py`](../backend/tests/test_orchestrator.py) (`HuntOrchestrator`); no route consumes it in v1.

## `periodic.py`

- **`HuntEngine.run_find_only`** — Loads `SearchProfile`, loads existing listing ids, `await browser.anonymous_search`, caps to `max_listings` (default 15), emits `search` / per-id `new_listing` actions, deep-scrapes with `anonymous_scrape_listing`, calls [`commute.travel_times`](../backend/app/wg_agent/commute.py) with the geocoded `(lat, lng)` against the user's `main_locations` in every profile-applicable mode (guarded: skipped when either coord is `None`), calls [`places.nearby_places`](../backend/app/wg_agent/places.py) for the user's place-like preferences around the same listing coordinates, passes both data sets into [`evaluator.evaluate`](../backend/app/wg_agent/evaluator.py) (hard filter → deterministic components → single `brain.vibe_score` LLM call → composition), collapses the commute matrix into the fastest `(mode, minutes)` per location for persistence and stores the nearby-place list alongside it (`repo.save_score(..., travel_minutes=..., nearby_places=..., components=..., veto_reason=...)`), and emits either `"Rejected {id}: <veto_reason>"` or `"Scored {id}: 0.82"` with a `price 0.9 · size 1.0 · …` breakdown in `detail`. Every persisted action also lands on the per-hunt asyncio queue for SSE (`_safe_put`).
- **`PeriodicHunter`** — Async loop calling `run_find_only`; for `periodic` schedules sleeps `interval_minutes * 60` seconds, optionally overridden by `WG_RESCAN_INTERVAL_MINUTES` when `schedule == "periodic"` and the interval is positive, emits `rescan` between passes, ends with `_finalize_done` (`HuntStatus.done`) or `_finalize_failed` on unexpected exceptions. `asyncio.CancelledError` triggers `_finalize_done` then re-raises.
- **Registry** — `_ACTIVE_HUNTERS` maps hunt id → `Task`, `_EVENT_QUEUES` maps hunt id → `Queue`. `spawn_hunter` creates/replaces the queue and task; `cancel_hunter` cancels the task; `event_queue_for` exposes the queue to SSE; `resume_running_hunts` respawns tasks for DB rows still `running`.

## `browser.py`

1. **Pure parsing** — `build_search_url`, `parse_search_page`, `parse_listing_page` (unit-tested via fixtures and `test_wg_parser`). The detail parser prefers scoped DOM selectors over `get_text` regex: `_section_pairs` walks forward from a section `<h2>` until the next `<h2>` to collect `{label: value}` rows (Kosten, Verfügbarkeit), `_wg_details_lines` returns the WG-Details `<li>`s for languages/pets/smoking, `_parse_address_panel` splits the Adresse detail into `(street, postal_code, city, district)`, and the description comes from `#ad_description_text` with embedded `<script>`/`<iframe>`/`div-gpt-ad-*` stripped. Every DOM path falls back to the original full-text regex so a DOM shift degrades gracefully instead of nulling fields. `_parse_map_lat_lng` extracts the listing's own map pin from the `map_config.markers` script block, giving `(lat, lng)` for free (see ADR-014).
2. **Anonymous httpx** — `anonymous_search`, `anonymous_scrape_listing` using shared headers, timeouts, and polite delays (`ANONYMOUS_PAGE_DELAY_SECONDS`). `anonymous_scrape_listing` trusts the map-pin coordinates produced by `parse_listing_page` when present and only calls [`geocoder.geocode`](../backend/app/wg_agent/geocoder.py) as a fallback (best string: `listing.address` → `"{district}, {city or req_city}"`), so `listing.lat` / `listing.lng` are populated before `repo.upsert_listing` persists the row.
3. **Playwright driver** — `WGBrowser` (`search`, `scrape_listing`, `send_message`, `fetch_inbox`) plus `launch_browser` for authenticated flows retained for future messaging.

## `geocoder.py`

Thin async client around Google Geocoding, used only as a fallback when `browser._parse_map_lat_lng` didn't find a map pin on the detail page (ADR-014, ADR-017). `geocode(address)` returns `(lat, lng)` or `None` and never raises. Reads `GOOGLE_MAPS_SERVER_KEY` from the environment; if unset, returns `None` without touching the network so local dev works without the key. An in-process dict caches results keyed on `address.strip().lower()` (cleared when it passes 1024 entries) so rescans of the same listing don't re-bill the same string. Every outbound call first waits on the shared `google_maps.wait_turn()` gate, which defaults to `8 req/s` process-wide and can be tuned with `GOOGLE_MAPS_MAX_RPS`.

## `commute.py`

Thin async client around Google Distance Matrix. `travel_times(origin, destinations, modes)` returns `{(place_id, mode): seconds}` for reachable pairs only — absent entries mean "no route" or "API failed", so callers treat the returned dict as authoritative. Issues one GET per travel mode with a one-origin/many-destinations shape; malformed rows and per-pair non-`OK` elements are skipped silently. Reuses the same `GOOGLE_MAPS_SERVER_KEY` as [`geocoder.py`](../backend/app/wg_agent/geocoder.py); without the key, the function short-circuits to `{}` so dev flows stay offline-friendly. Each request also waits on the shared `google_maps.wait_turn()` gate so concurrent hunts do not burst above the configured provider cap. `modes_for(sp)` derives the mode list straight from the search profile: always `TRANSIT`, plus `BICYCLE` when `sp.has_bike`, plus `DRIVE` when `sp.has_car`.

## `places.py`

Thin async client around Google Places API (New). `nearby_places(origin, preferences)` returns `{pref_key: NearbyPlace}` for the subset of user preferences that map cleanly to real nearby amenities (`gym`, `park`, `supermarket`, `cafe`, `bars`, `library`, `coworking`, `nightlife`, `green_space`, `public_transport`). Type-backed preferences use Nearby Search (New); `coworking` uses Text Search (New) with a distance-biased circle. Distances are computed from the returned place coordinates, cached in-process, and fail-soft: missing key or HTTP issues degrade to `{}` so the evaluator can fall back to keyword evidence instead of crashing. Requests pass through the same shared Google throttle as geocoding and routing.

## `brain.py`

- `vibe_score(listing, requirements, *, nearby_places=None) -> VibeScore` — Narrow Chat Completions JSON-object call used by [`evaluator.vibe_fit`](../backend/app/wg_agent/evaluator.py). The prompt is explicitly told **not** to judge price, size, WG size, or commute — only how well `listing.description` + `listing.district` match the user's free-form `notes`, `preferred_districts` / `avoid_districts`, and the nearby-place/lifestyle context that matters to those preferences. Returns a validated Pydantic model `{score: float, evidence: list[str]}`; `ValidationError` bubbles up so the evaluator can mark the vibe component `missing_data`.
- `score_listing(listing, requirements, *, travel_times=None, nearby_places=None)` — Legacy one-shot LLM score (used by [`orchestrator.py`](../backend/app/wg_agent/orchestrator.py), not by the v1 find loop). The optional `travel_times` matrix is rendered into a per-location "Commute times" block and `nearby_places` into a "Nearby preference places" block inside the user prompt.
- `draft_message` — First outbound message text (orchestrator path).
- `classify_reply` — `ReplyAnalysis` from landlord text (orchestrator path).
- `reply_to_landlord` — Follow-up composer (orchestrator path).

## `evaluator.py`

The scorecard pipeline (ADR-015). Public functions:

- `hard_filter(listing, profile) -> VetoResult | None` — Deterministic vetoes: over `max_rent_eur`, city mismatch (with Muenchen/München normalization), district in `avoid_districts`, `available_from > move_in_until`, weight-5 structured preference directly contradicted.
- Component functions (pure): `price_fit`, `size_fit`, `wg_size_fit`, `availability_fit`, `commute_fit`, `preference_fit`. Each returns a `ComponentScore` with a score in `[0, 1]`, composition `weight`, `evidence` list, optional `hard_cap`, and `missing_data` flag. `preference_fit` now prefers `nearby_places` distance facts for place-like preferences before falling back to description keywords.
- `vibe_fit(listing, profile, *, nearby_places=None) -> ComponentScore` — async wrapper around `brain.vibe_score` that degrades to `missing_data=True` on `ValidationError` or any exception.
- `compose(components, *, veto=None) -> EvaluationResult` — Weighted mean across non-`missing_data` components, take the minimum of every `hard_cap` present, clamp to `[0, 1]`; veto short-circuits to `score=0.0`.
- `evaluate(listing, profile, *, travel_times=None) -> EvaluationResult` — End-to-end facade; what `HuntEngine.run_find_only` calls.

Curve tuning (weights and thresholds) lives at the top of the module in `COMPONENT_WEIGHTS`, `DEFAULT_COMMUTE_BUDGET_MIN`, and `PREFERENCE_KEYWORDS`; unit tests pin each curve's boundaries in [`test_evaluator.py`](../backend/tests/test_evaluator.py).

## `orchestrator.py`

`HuntOrchestrator` implements the full Playwright + messaging + inbox poll loop described in the module docstring. It is exercised by [`test_orchestrator.py`](../backend/tests/test_orchestrator.py) and is not invoked from [`api.py`](../backend/app/wg_agent/api.py) in v1.

## Tests

| File | Role | Command |
| --- | --- | --- |
| [`test_wg_parser.py`](../backend/tests/test_wg_parser.py) | Cached HTML fixtures under `tests/fixtures/`; asserts parser output shape and locks down the structured fields the scorer relies on (address split, available-from/to, languages, pets/smoking, description-doesn't-leak-page-chrome, map-pin lat/lng) | `cd backend && python tests/test_wg_parser.py` (or `pytest tests/test_wg_parser.py`) |
| [`test_orchestrator.py`](../backend/tests/test_orchestrator.py) | Mock browser/brain end-to-end orchestrator run | `cd backend && python tests/test_orchestrator.py` (or `pytest tests/test_orchestrator.py`) |
| [`test_repo.py`](../backend/tests/test_repo.py) | In-memory SQLite round-trip for `repo` + crypto | `cd backend && pytest tests/test_repo.py` |
| [`test_periodic.py`](../backend/tests/test_periodic.py) | `HuntEngine` / `PeriodicHunter` with mocked I/O (includes the commute-reaches-evaluator and lat-missing guard cases) | `cd backend && pytest tests/test_periodic.py` |
| [`test_commute.py`](../backend/tests/test_commute.py) | Route Matrix client with monkey-patched `httpx.post` | `cd backend && pytest tests/test_commute.py` |
| [`test_places.py`](../backend/tests/test_places.py) | Nearby-place client with mocked `httpx` (cache + fail-soft paths) | `cd backend && pytest tests/test_places.py` |
| [`test_brain.py`](../backend/tests/test_brain.py) | `_listing_summary` commute-block formatting (no LLM) | `cd backend && pytest tests/test_brain.py` |
| [`test_evaluator.py`](../backend/tests/test_evaluator.py) | Scorecard evaluator: `hard_filter` paths, per-component curves, `compose` arithmetic + caps, `vibe_fit` graceful degradation (no LLM, no DB) | `cd backend && pytest tests/test_evaluator.py` |
| [`test_geocoder.py`](../backend/tests/test_geocoder.py) | Geocoding client with mocked `httpx` (cache + fail-soft paths) | `cd backend && pytest tests/test_geocoder.py` |
| [`test_dto.py`](../backend/tests/test_dto.py) | `UpsertSearchProfileBody` → `SearchProfile` conversion rules (e.g. München normalization for commute anchors) | `cd backend && pytest tests/test_dto.py` |
| [`test_google_maps.py`](../backend/tests/test_google_maps.py) | Shared async throttle in [`google_maps.py`](../backend/app/wg_agent/google_maps.py) — verifies rate-limit spacing across concurrent callers | `cd backend && pytest tests/test_google_maps.py` |

Run the whole suite with `cd backend && pytest` after activating the venv.

## Alembic

Seven revisions in [`backend/alembic/versions/`](../backend/alembic/versions/), each additive:

| Revision | Purpose |
| --- | --- |
| [`0001_initial.py`](../backend/alembic/versions/0001_initial.py) | Creates all v1 tables + indexes described in [DATA_MODEL.md](./DATA_MODEL.md) |
| [`0002_places_main_locations.py`](../backend/alembic/versions/0002_places_main_locations.py) | Resets `searchprofilerow.main_locations` to `[]` so pre-Places rows don't feed bare strings into `PlaceLocation` parsing (ADR-010) |
| [`0003_listing_coords.py`](../backend/alembic/versions/0003_listing_coords.py) | Adds `listingrow.lat` / `listingrow.lng` for server-side geocoding (ADR-011) |
| [`0004_listing_commute.py`](../backend/alembic/versions/0004_listing_commute.py) | Adds `listingscorerow.travel_minutes` JSON for the drawer's commute section (ADR-012) |
| [`0005_weighted_prefs.py`](../backend/alembic/versions/0005_weighted_prefs.py) | Resets `searchprofilerow.preferences` + `.main_locations` so pre-weighted-schema rows don't poison `PreferenceWeight` / per-location `max_commute_minutes` parsing (ADR-013) |
| [`0006_scorecard_components.py`](../backend/alembic/versions/0006_scorecard_components.py) | Adds `listingscorerow.components` JSON + `veto_reason` String for the scorecard evaluator (ADR-015) |
| [`0007_nearby_places.py`](../backend/alembic/versions/0007_nearby_places.py) | Adds `listingscorerow.nearby_places` JSON for persisted nearby preference distances (ADR-016) |

[`env.py`](../backend/alembic/env.py) imports `app.wg_agent.db_models` so `SQLModel.metadata` matches the app. [`main.py`](../backend/app/main.py) runs `command.upgrade(cfg, "head")` during FastAPI lifespan startup (ADR-005), so you never call `alembic upgrade` by hand.

After editing [`db_models.py`](../backend/app/wg_agent/db_models.py), generate a new revision from `backend/`:

```bash
alembic revision --autogenerate -m "describe change"
```

Review the diff (autogenerate is not infallible for SQLite — it can miss column renames and generate `DROP` + `ADD` pairs), tweak if needed, then commit the revision file alongside your model change.
