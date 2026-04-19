# Setup

Clone the repo and run the WG Hunter stack locally: FastAPI backend + background scraper + Vite-built React UI, all pointing at the team-shared AWS MySQL.

## Prerequisites

- Python **3.11+**
- **Node.js** 20+ (Node **24** is what we use day-to-day; it works with the checked-in lockfile)
- **npm** 10+
- **MySQL credentials** — WG Hunter is MySQL-only. Developers use the team-shared AWS RDS instance by setting the five `DB_*` vars (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`) in `.env`; no local MySQL install is required.
- A working **`OPENAI_API_KEY`** (see [`.env.example`](../.env.example)). The evaluator's `vibe_fit` component calls OpenAI once per scored listing; without it, every listing's vibe component degrades to `missing_data` and the composite score uses only the deterministic components.
- Optional location API keys:
  - `VITE_GOOGLE_MAPS_API_KEY` — **Maps JavaScript API** + **Places API (New)**, referrer-restricted to `http://localhost:5173/*`, `http://localhost:8000/*`, and your deployed origin. Used only by the onboarding wizard's Main locations autocomplete ([`PlaceAutocomplete.tsx`](../frontend/src/components/PlaceAutocomplete.tsx)). Without it the field falls back to a disabled placeholder.
  - `GOOGLE_MAPS_SERVER_KEY` — backend-only key for Google Geocoding API, Distance Matrix API, and Places API (New). Powers [`geocoder.py`](../backend/app/wg_agent/geocoder.py) (fallback when the listing HTML doesn't ship a map pin), [`commute.py`](../backend/app/wg_agent/commute.py) (per-mode commute matrix used by `evaluator.commute_fit`), and [`places.py`](../backend/app/wg_agent/places.py) (nearby amenity distances for place-like user preferences). Without it, commute and nearby-place components degrade to missing data, but scraping still works.

## One-shot setup

1. **Clone** this repository and `cd` into the repo root.

2. **Environment file**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set:
   - `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` — AWS RDS credentials (ask a teammate). All five are required for both the backend and scraper processes; `db.py` assembles the `mysql+pymysql://` DSN from them at import time and refuses to boot if any are missing.
   - `OPENAI_API_KEY` — OpenAI key for the vibe-score component.
   - Optionally `VITE_GOOGLE_MAPS_API_KEY` for the Places Autocomplete widget and `GOOGLE_MAPS_SERVER_KEY` for backend routing/geocoding/place enrichment; Vite reads this file via [`envDir: '..'`](../frontend/vite.config.ts) from `frontend/`, so one repo-root `.env` covers backend, scraper, and frontend.
   - Optionally tune the scraper via `SCRAPER_CITY`, `SCRAPER_MAX_RENT`, `SCRAPER_INTERVAL_SECONDS`, `SCRAPER_REFRESH_HOURS`, `SCRAPER_MAX_AGE_DAYS`, `SCRAPER_KIND` (defaults in [`.env.example`](../.env.example)). Pagination depth is freshness-driven: search URLs request newest-first and the agent stops the moment a stub's posting date is older than `SCRAPER_MAX_AGE_DAYS` (default 4). Set `SCRAPER_KIND=wg` (or `flat`) to restrict the scraper to one vertical; default `both`.
   - Optional LLM-driven enrichment of missing structured fields: `SCRAPER_ENRICH_ENABLED`, `SCRAPER_ENRICH_MODEL`, `SCRAPER_ENRICH_MIN_DESC_CHARS` (default off; requires `OPENAI_API_KEY`).

3. **Backend**

   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

   Playwright is only required if you use cookie-based browser flows elsewhere; the v1 scraper + per-user matcher loop uses **httpx** only.

4. **Frontend**

   ```bash
   cd ../frontend
   npm install
   npm run build
   ```

5. **Run the backend** (loads env from the repo-root `.env`)

   ```bash
   cd ../backend
   set -a && source ../.env && set +a
   venv/bin/uvicorn app.main:app --reload
   ```

   Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/). On startup the app calls `db.init_db()` (which bootstraps any missing tables via `SQLModel.metadata.create_all`) and resumes per-user matcher agents for every user with a saved search profile ([`main.py`](../backend/app/main.py), [`periodic.resume_user_agents`](../backend/app/wg_agent/periodic.py)).

6. **Run the scraper** — in a second terminal, from `backend/` (same `.env`):

   ```bash
   set -a && source ../.env && set +a
   venv/bin/python -m app.scraper.main
   ```

   This runs migrations, then enters the scraper loop. In docker-compose setups, the `scraper` service takes care of this.

7. **Frontend dev loop** — in a third terminal, from `frontend/`:

   ```bash
   npm run dev
   ```

   Vite proxies `/api` to `http://127.0.0.1:8000` ([`vite.config.ts`](../frontend/vite.config.ts)).

## Reset the database

Drop and recreate the MySQL database, then restart both backend and scraper — `db.init_db()` will recreate the schema via `SQLModel.metadata.create_all` on the next startup:

```sql
DROP DATABASE wg_hunter;
CREATE DATABASE wg_hunter CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

This is also the correct way to pick up a model change that isn't additive: we don't use Alembic in this project ([docs/BACKEND.md "Schema evolution"](./BACKEND.md#schema-evolution) explains why), so column additions to existing tables require a reset too.

For a dev DB on AWS, coordinate with the team before resetting — everyone shares the instance.

## Your first contribution

Two well-scoped warm-ups for new teammates, no Python-side heavy lifting required for the first one.

### A. Add a new preference tile (15 min, frontend-only)

The backend stores `SearchProfile.preferences` as `list[PreferenceWeight]` ([`SearchProfileRow.preferences`](../backend/app/wg_agent/db_models.py)); any new snake_case `key` is accepted without a migration because the row stays a JSON column.

1. Open [`frontend/src/pages/OnboardingPreferences.tsx`](../frontend/src/pages/OnboardingPreferences.tsx).
2. Append one tile object to the relevant section in the `GROUPS` array: a unique snake_case `key`, a short `label`, and an SVG `path` inside the existing `Icon` wrapper (copy an existing tile's structure).
3. Save, run `npm run dev` (or `npm run build` if you test against production-like static files).
4. Walk through onboarding again; select the new tile and adjust its importance on the 1–5 slider. The `{key, weight}` pair is persisted via `PUT /api/users/{username}/search-profile`.
5. Save the profile and the matcher agent will pick it up on its next pass. The evaluator's [`preference_fit`](../backend/app/wg_agent/evaluator.py) will see the new tile. If the `key` matches one of the structured booleans in `STRUCTURED_PREFERENCES` (e.g. `furnished`), it's resolved directly against the listing row. If it matches one of the Google Places-backed nearby-place categories in [`places.py`](../backend/app/wg_agent/places.py), the scorer uses the nearest real nearby place and its distance. Otherwise the evaluator substring-scans the listing description for the key plus any synonyms in `PREFERENCE_KEYWORDS`.
6. **Optional polish**: if your new tile has German/English synonyms worth matching (e.g. `garden` → `garten`), add an entry to `PREFERENCE_KEYWORDS` in [`evaluator.py`](../backend/app/wg_agent/evaluator.py). Without it, only the bare key is matched.

### B. Tune a component curve (30 min, backend + test)

Every component in [`evaluator.py`](../backend/app/wg_agent/evaluator.py) is pure Python with a boundary-pinned unit test in [`test_evaluator.py`](../backend/tests/test_evaluator.py). Good first PRs: tighten `commute_fit`'s ramp for users with long budgets, widen `size_fit`'s upper tolerance for flats, etc.

1. Edit the relevant component function in [`evaluator.py`](../backend/app/wg_agent/evaluator.py). Keep it pure — no I/O, no state.
2. Run `cd backend && venv/bin/pytest tests/test_evaluator.py -k your_component` to see which boundary assertions break, and either update the curve or the test (match the existing table-driven style).
3. Run the full suite: `venv/bin/pytest tests` — the targeted backend suite should stay green; parser fixture tests need the checked-in `backend/tests/fixtures/` HTML snapshots.
4. Open the listing drawer on the dashboard — your change shows up immediately as a different bar height and updated `evidence` string once the matcher has rescored the listing.

## Troubleshooting

- **`Database credentials are incomplete` at startup** — The backend and scraper both raise if any of `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` is missing or empty. Re-source `.env` (`set -a && source ../.env && set +a`) in the terminal you launch from, and confirm all five values are set.

- **`Can't connect to MySQL server` / SSL handshake errors** — Check that the RDS instance accepts connections from your IP and that the DSN includes `?charset=utf8mb4`. The engine uses `pool_pre_ping=True` so stale connections are transparently reopened, but first-connect failures will still surface on startup.

- **`OPENAI_API_KEY` is not set** — Ensure `set -a && source ../.env && set +a` (or export the variable) in the same shell session before launching `uvicorn`.

- **Schema drifted after a model change** — Coordinate with the team, drop + recreate the MySQL database (see "Reset the database"), then restart both backend and scraper so `db.init_db()` rebuilds the schema from `SQLModel.metadata`.

- **Port 8000 already in use** — `lsof -ti :8000 | xargs kill` (macOS) then restart `uvicorn`.

- **503 on `/` with “frontend/dist/index.html not found”** — Run `npm run build` in `frontend/` so [`main.py`](../backend/app/main.py) can serve the SPA.

- **Empty listing photos in the drawer** — [`repo.save_photos`](../backend/app/wg_agent/repo.py) is populated by the scraper container; when the scraper is offline or a listing hasn't been deep-scraped yet, the drawer still returns listing fields and the per-user match score from the DB with an empty photo list.

- **"Vibe check skipped" in a component bar** — The vibe component degrades to `missing_data=True` when `brain.vibe_score` raises (no `OPENAI_API_KEY`, HTTP error, model returns invalid JSON). The rest of the scorecard still runs and the composite score is computed from the remaining components. Check the backend logs for `vibe_fit:` warnings to see the exact cause.

- **All commutes show "no commute data"** — Either the user hasn't picked `main_locations` in onboarding (nothing to commute to), or `GOOGLE_MAPS_SERVER_KEY` is unset / invalid. [`commute.travel_times`](../backend/app/wg_agent/commute.py) returns `{}` on a missing key, which makes `evaluator.commute_fit` flip to `missing_data` for that listing.
- **Nearby preferences all show as missing/unknown** — Ensure `GOOGLE_MAPS_SERVER_KEY` is set. [`places.nearby_places`](../backend/app/wg_agent/places.py) short-circuits to `{}` without a key, so place-like preferences fall back to description keywords or `missing_data`.
