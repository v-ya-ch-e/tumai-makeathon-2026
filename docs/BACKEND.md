# Backend

FastAPI entrypoint plus the `wg_agent` package: JSON/SSE API, SQLite persistence, anonymous wg-gesucht scraping, and the v1 find-and-score loop.

## File map

```text
backend/app/main.py              FastAPI app, lifespan (DB + Alembic + hunt resume), SPA mount, legacy /items routes
backend/app/wg_agent/
  __init__.py                    Package docstring; points contributors to WG recon notes
  api.py                         `/api` router: users, search profile, credentials, hunts, SSE stream, listing detail
  brain.py                       OpenAI chat calls: score, draft, classify (v1 loop uses `score_listing` only)
  browser.py                     URL builders, HTML parsers, httpx anonymous path, Playwright `WGBrowser` + factory
  crypto.py                      Fernet key resolution + encrypt/decrypt for credential blobs
  db.py                          SQLModel engine, WAL pragma, `init_db`, `get_session` dependency
  db_models.py                   `*Row` SQLModel table classes (see [DATA_MODEL.md](./DATA_MODEL.md))
  dto.py                         Pydantic DTOs + `*_to_dto` / `upsert_body_to_search_profile` converters
  models.py                      Domain Pydantic models + enums + `CITY_CATALOGUE`
  orchestrator.py                Legacy `HuntOrchestrator` (Playwright messaging loop) for tests / future work
  periodic.py                    `HuntEngine`, `PeriodicHunter`, hunter task registry, `resume_running_hunts`
  repo.py                        Domain ↔ `*Row` conversions; narrow CRUD surface for hunts, listings, actions, users
```

## `main.py`

`FastAPI` is constructed with `lifespan=lifespan`. On startup the async context runs, in order: `wg_db.init_db()` (ensures Fernet key material and touches the engine), logs `DATABASE_URL`, loads Alembic `Config` from `backend/alembic.ini` with `script_location` pointing at `backend/alembic`, runs `command.upgrade(cfg, "head")`, then `await wg_periodic.resume_running_hunts()`. The API router from [`api.py`](../backend/app/wg_agent/api.py) is included under `/api`. Tutorial-style `/items/*` routes and `/health` remain alongside `/api/health`. When `frontend/dist/assets` exists, `/assets` is mounted; the catch-all `GET /{full_path:path}` returns `index.html` for non-`api/` and non-`assets/` paths (503 if the bundle is missing).

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
- **`Listing`** — Normalized listing + LLM score fields. Produced by `browser` parsers, consumed by `brain`, persisted via `repo.upsert_listing` / `save_score`.
- **`Message`**, **`ReplyAnalysis`**, **`ReplyIntent`** — Messaging and inbox semantics for future orchestrator flows.
- **`ActionKind`** / **`AgentAction`** — Append-only log line kinds and payload. Written by API boot/stop paths and `periodic.HuntEngine` / `PeriodicHunter`.
- **`HuntStatus`** / **`Hunt`** — Aggregate hunt state (`requirements` holds the embedded `SearchProfile`). Built by `repo.get_hunt` for API responses.

Enums: **`Gender`**, **`RentType`**, **`MessageDirection`**, **`ReplyIntent`** — constrain domain fields and API string patterns (`CreateUserBody.gender`).

## `dto.py`

DTOs: `UserDTO`, `CreateUserBody`, `SearchProfileDTO`, `UpsertSearchProfileBody`, `CredentialsBody`, `CredentialsStatusDTO`, `CreateHuntBody`, `ActionDTO`, `ListingDTO`, `HuntDTO`, `ListingDetailDTO`.

Conversion helpers: `user_to_dto`, `search_profile_to_dto`, `upsert_body_to_search_profile`, `action_to_dto`, `listing_to_dto`, `hunt_to_dto`.

**Three-layer rule:** HTTP handlers in [`api.py`](../backend/app/wg_agent/api.py) accept/return DTOs and call these helpers (or `upsert_body_to_search_profile`) to cross into [`models.py`](../backend/app/wg_agent/models.py) domain types. Handlers must not construct SQLModel rows. The documented exception is `_get_listing_detail`, which reads `*Row` tables directly to assemble `ListingDetailDTO` ([DATA_MODEL.md](./DATA_MODEL.md)). `repo.py` remains the routine domain ↔ row boundary for mutations.

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
| `upsert_search_profile` | Merge `SearchProfile` into `SearchProfileRow` |
| `get_search_profile` | Row → `SearchProfile`, deriving `city` and `max_rent_eur` from `main_locations` / `price_max_eur` when absent |
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

- **`HuntEngine.run_find_only`** — Loads `SearchProfile`, loads existing listing ids, `await browser.anonymous_search`, caps to `max_listings` (default 15), emits `search` / per-id `new_listing` actions, deep-scrapes with `anonymous_scrape_listing`, calls `brain.score_listing`, `repo.upsert_listing` + `save_score`, emits `evaluate`. Each persisted action is also pushed to the per-hunt asyncio queue for SSE (`_safe_put`).
- **`PeriodicHunter`** — Async loop calling `run_find_only`; for `periodic` schedules sleeps `interval_minutes * 60` seconds, optionally overridden by `WG_RESCAN_INTERVAL_MINUTES` when `schedule == "periodic"` and the interval is positive, emits `rescan` between passes, ends with `_finalize_done` (`HuntStatus.done`) or `_finalize_failed` on unexpected exceptions. `asyncio.CancelledError` triggers `_finalize_done` then re-raises.
- **Registry** — `_ACTIVE_HUNTERS` maps hunt id → `Task`, `_EVENT_QUEUES` maps hunt id → `Queue`. `spawn_hunter` creates/replaces the queue and task; `cancel_hunter` cancels the task; `event_queue_for` exposes the queue to SSE; `resume_running_hunts` respawns tasks for DB rows still `running`.

## `browser.py`

1. **Pure parsing** — `build_search_url`, `parse_search_page`, `parse_listing_page` (unit-tested via fixtures and `test_wg_parser`).
2. **Anonymous httpx** — `anonymous_search`, `anonymous_scrape_listing` using shared headers, timeouts, and polite delays (`ANONYMOUS_PAGE_DELAY_SECONDS`).
3. **Playwright driver** — `WGBrowser` (`search`, `scrape_listing`, `send_message`, `fetch_inbox`) plus `launch_browser` for authenticated flows retained for future messaging.

## `brain.py`

- `score_listing` — Chat Completions JSON object; mutates `Listing` score fields (wired in `HuntEngine`).
- `draft_message` — First outbound message text (orchestrator path).
- `classify_reply` — `ReplyAnalysis` from landlord text (orchestrator path).
- `reply_to_landlord` — Follow-up composer (orchestrator path).

## `orchestrator.py`

`HuntOrchestrator` implements the full Playwright + messaging + inbox poll loop described in the module docstring. It is exercised by [`test_orchestrator.py`](../backend/tests/test_orchestrator.py) and is not invoked from [`api.py`](../backend/app/wg_agent/api.py) in v1.

## Tests

| File | Role | Command |
| --- | --- | --- |
| [`test_wg_parser.py`](../backend/tests/test_wg_parser.py) | Cached HTML fixtures; asserts parser output shape | `cd backend && python tests/test_wg_parser.py` |
| [`test_orchestrator.py`](../backend/tests/test_orchestrator.py) | Mock browser/brain end-to-end orchestrator run | `cd backend && python tests/test_orchestrator.py` |
| [`test_repo.py`](../backend/tests/test_repo.py) | In-memory SQLite round-trip for `repo` + crypto | `cd backend && python tests/test_repo.py` |
| [`test_periodic.py`](../backend/tests/test_periodic.py) | `HuntEngine` / `PeriodicHunter` with mocked I/O | `cd backend && python tests/test_periodic.py` |

## Alembic

[`0001_initial.py`](../backend/alembic/versions/0001_initial.py) creates all v1 tables and indexes described in [DATA_MODEL.md](./DATA_MODEL.md). [`env.py`](../backend/alembic/env.py) imports `app.wg_agent.db_models` so `SQLModel.metadata` matches the app.

After editing [`db_models.py`](../backend/app/wg_agent/db_models.py), generate migrations from `backend/`:

```bash
alembic revision --autogenerate -m "describe change"
```

Review the diff (autogenerate is not infallible for SQLite), then commit the revision file.
