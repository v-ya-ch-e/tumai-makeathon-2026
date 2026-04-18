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

