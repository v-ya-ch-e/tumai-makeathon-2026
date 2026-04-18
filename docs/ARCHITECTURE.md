# Architecture

WG Hunter runs as two containers against a shared AWS-hosted MySQL:

1. **backend** — FastAPI process that serves the built React SPA, bootstraps the schema on startup via `SQLModel.metadata.create_all`, and spawns per-hunt `PeriodicHunter` asyncio tasks that **match** listings from the shared pool (they never scrape).
2. **scraper** — Standalone Python process that owns `ListingRow` + `PhotoRow`. It hits `wg-gesucht.de` via httpx on a fixed interval, deep-scrapes every new listing, and refreshes listings older than `SCRAPER_REFRESH_HOURS`.

## Runtime shape

```mermaid
flowchart LR
  subgraph scraperSvc [scraper container]
    SA["ScraperAgent<br/>asyncio loop"]
  end
  subgraph backendSvc [backend container]
    API["/api/* JSON + SSE"]
    SPA["SPA fallback + /assets"]
    PH["PeriodicHunter / HuntEngine<br/>matcher only"]
  end
  subgraph external [External]
    WG["wg-gesucht.de via httpx"]
    OAI["OpenAI API"]
    GM["Google Maps Platform"]
  end
  MySQL[("AWS MySQL<br/>DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME")]
  SA -->|"anonymous_search + anonymous_scrape_listing"| WG
  SA -->|"upsert_global_listing + save_photos"| MySQL
  React["React (Vite)"] -->|"fetch /api"| API
  React -->|"GET /api/hunts/{id}/stream"| API
  API --> PH
  PH -->|"list_scorable_listings"| MySQL
  PH -->|"save_score (per hunt)"| MySQL
  PH -->|"evaluator.evaluate (vibe_score)"| OAI
  PH -->|"commute + places"| GM
  API --> MySQL
  React -->|"GET / (non-api)"| SPA
```

Invariants:

1. Only the scraper writes `ListingRow` and `PhotoRow`.
2. Only hunts write `ListingScoreRow`. A `ListingScoreRow` row *is* the hunt ↔ listing membership record — including for vetoed listings (score `0.0`, `veto_reason` set).
3. MySQL is the single source of truth. Both services call `SQLModel.metadata.create_all(engine)` on startup (via `db.init_db()`), which creates any missing tables and no-ops when the schema is already up to date. Destructive changes require a `DROP DATABASE; CREATE DATABASE` — see [SETUP.md](./SETUP.md#reset-the-database).

Fernet key material for credential blobs is resolved in [`crypto.py`](../backend/app/wg_agent/crypto.py): optional `WG_SECRET_KEY`, otherwise a key file under `~/.wg_hunter/secret.key` (shared between containers via the `wg_data` Docker volume).

## Why these choices

- **Split scraping from matching** — The scraper writes once per listing across all users; per-hunt work is pure scoring. See [ADR-018](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only).
- **MySQL on AWS, no local DB** — All developers share one AWS RDS instance via five `DB_*` env vars (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`) in `.env`. No docker-compose `mysql` service, no per-developer schema drift. Tests use in-memory SQLite for isolation ([`backend/tests/conftest.py`](../backend/tests/conftest.py) sets inert `DB_*` placeholders so the production `db.py` can import; individual tests then build their own SQLite engine and monkey-patch `db_module.engine`).
- **Vite + React, not Next.js** — No SSR requirement; the UI is a desktop-first SPA. FastAPI serves `frontend/dist/` so one service covers API + static assets.
- **httpx anonymous path** — Both the scraper and the legacy orchestrator use `browser.anonymous_search` / `anonymous_scrape_listing` without Playwright, keeping cold starts short.
- **SSE instead of WebSockets** — The action log is server → client only. [`api.stream_hunt`](../backend/app/wg_agent/api.py) streams JSON lines plus keep-alives.

## Request flow

```mermaid
sequenceDiagram
  participant UI as React
  participant API as FastAPI
  participant PH as PeriodicHunter
  participant DB as MySQL
  participant GM as Google Maps
  participant OAI as OpenAI

  UI->>API: POST /api/users/{username}/hunts
  API->>DB: create_hunt + mark running
  API->>PH: spawn_hunter (asyncio task + queue)
  API-->>UI: 201 HuntDTO
  UI->>API: EventSource GET /api/hunts/{id}/stream
  loop find pass (+ periodic sleep)
    PH->>DB: list_scorable_listings(hunt_id, status="full")
    DB-->>PH: ListingRow candidates (from shared pool)
    PH->>GM: commute.travel_times + places.nearby_places
    PH->>OAI: evaluator.evaluate (hard filter + components + vibe)
    PH->>DB: save_score(listing_id, hunt_id, ...)
    PH->>API: repo.append_action + Queue.put(AgentAction)
    API-->>UI: SSE data: search / new_listing / evaluate / rescan
  end
  UI->>API: GET /api/listings/{listing_id}?hunt_id=
  API->>DB: ListingRow by id + ListingScoreRow by (id, hunt_id)
  API-->>UI: ListingDetailDTO
```

Meanwhile, independently of any hunt, the scraper container runs its own loop:

```mermaid
sequenceDiagram
  participant SA as ScraperAgent
  participant WG as wg-gesucht.de
  participant DB as MySQL

  loop every SCRAPER_INTERVAL_SECONDS
    SA->>WG: anonymous_search
    WG-->>SA: Listing stubs
    loop per new or stale listing
      SA->>WG: anonymous_scrape_listing
      WG-->>SA: enriched Listing
      SA->>DB: upsert_global_listing (status=full) + save_photos
    end
  end
```

On process start, [`main.py`](../backend/app/main.py) calls `db.init_db()` (which in turn calls `SQLModel.metadata.create_all(engine)`) and [`periodic.resume_running_hunts`](../backend/app/wg_agent/periodic.py) re-spawns tasks for hunts still marked `running` in MySQL. The scraper's [`app/scraper/main.py`](../backend/app/scraper/main.py) follows the same `init_db()` path before starting its loop.
