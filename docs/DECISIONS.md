# Architecture Decision Records

ADR index for WG Hunter. Each entry lists context, decision, consequences, and the introducing commit where applicable. See also [ARCHITECTURE.md](./ARCHITECTURE.md), [DATA_MODEL.md](./DATA_MODEL.md), and [DESIGN.md](./DESIGN.md).

---

## ADR-001: SQLite + SQLModel + Alembic for persistence

- **Date:** 2026-04-18  
- **Status:** Accepted  

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
- **Status:** Accepted  

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
