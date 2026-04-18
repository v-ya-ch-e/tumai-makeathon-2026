# Setup

Clone the repo and run the WG Hunter stack locally: FastAPI backend + Vite-built React UI, with SQLite under `~/.wg_hunter/`.

## Prerequisites

- Python **3.11+**
- **Node.js** 20+ (Node **24** is what we use day-to-day; it works with the checked-in lockfile)
- **npm** 10+
- A working **`OPENAI_API_KEY`** (see [`.env.example`](../.env.example)). The evaluator's `vibe_fit` component calls OpenAI once per scored listing; without it, every listing's vibe component degrades to `missing_data` and the composite score uses only the deterministic components.
- **Google Maps Platform** keys (both optional, but strongly recommended for demos):
  - `VITE_GOOGLE_MAPS_API_KEY` — **Maps JavaScript API** + **Places API (New)**, referrer-restricted to `http://localhost:5173/*`, `http://localhost:8000/*`, and your deployed origin. Used by the onboarding wizard's Main locations autocomplete ([`PlaceAutocomplete.tsx`](../frontend/src/components/PlaceAutocomplete.tsx)). Without it the field falls back to a disabled placeholder.
  - `GOOGLE_MAPS_SERVER_KEY` — **Geocoding API** + **Routes API**, IP-restricted, **never** shipped to the browser. Powers [`geocoder.py`](../backend/app/wg_agent/geocoder.py) (fallback when the listing HTML doesn't ship a map pin) and [`commute.py`](../backend/app/wg_agent/commute.py) (per-mode commute matrix used by `evaluator.commute_fit`). Without it, `commute_fit` degrades to `missing_data` and the composite score leans on the other components.

## One-shot setup

1. **Clone** this repository and `cd` into the repo root.

2. **Environment file**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set `OPENAI_API_KEY`. Optionally set `VITE_GOOGLE_MAPS_API_KEY` for the Places Autocomplete widget; Vite reads this file via [`envDir: '..'`](../frontend/vite.config.ts) from `frontend/`, so one repo-root `.env` covers both backend and frontend.

3. **Backend**

   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

   Playwright is only required if you use cookie-based browser flows elsewhere; the v1 periodic hunter uses **httpx** only.

4. **Frontend**

   ```bash
   cd ../frontend
   npm install
   npm run build
   ```

5. **Run the backend** (loads `OPENAI_API_KEY` from the repo-root `.env`)

   ```bash
   cd ../backend
   set -a && source ../.env && set +a
   venv/bin/uvicorn app.main:app --reload
   ```

   Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/). On startup the app runs Alembic `upgrade head` and resumes hunts still marked `running` in SQLite ([`main.py`](../backend/app/main.py), [`periodic.resume_running_hunts`](../backend/app/wg_agent/periodic.py)).

6. **Frontend dev loop** — in a second terminal, from `frontend/`:

   ```bash
   npm run dev
   ```

   Vite proxies `/api` to `http://127.0.0.1:8000` ([`vite.config.ts`](../frontend/vite.config.ts)).

## Reset the database

```bash
rm ~/.wg_hunter/app.db*
```

Restart `uvicorn`. Alembic recreates an empty schema on the next `upgrade head`.

## Your first contribution

Two well-scoped warm-ups for new teammates, no Python-side heavy lifting required for the first one.

### A. Add a new preference tile (15 min, frontend-only)

The backend stores `SearchProfile.preferences` as `list[PreferenceWeight]` ([`SearchProfileRow.preferences`](../backend/app/wg_agent/db_models.py)); any new snake_case `key` is accepted without a migration because the row stays a JSON column.

1. Open [`frontend/src/pages/OnboardingPreferences.tsx`](../frontend/src/pages/OnboardingPreferences.tsx).
2. Append one tile object to the relevant section in the `GROUPS` array: a unique snake_case `key`, a short `label`, and an SVG `path` inside the existing `Icon` wrapper (copy an existing tile's structure).
3. Save, run `npm run dev` (or `npm run build` if you test against production-like static files).
4. Walk through onboarding again; select the new tile and adjust its importance on the 1–5 slider. The `{key, weight}` pair is persisted via `PUT /api/users/{username}/search-profile`.
5. Start a hunt from the dashboard. The evaluator's [`preference_fit`](../backend/app/wg_agent/evaluator.py) will see the new tile. If the `key` matches one of the structured booleans in `STRUCTURED_PREFERENCES` (e.g. `furnished`), it's resolved directly against the listing row. Otherwise the evaluator substring-scans the listing description for the key plus any synonyms in `PREFERENCE_KEYWORDS`.
6. **Optional polish**: if your new tile has German/English synonyms worth matching (e.g. `garden` → `garten`), add an entry to `PREFERENCE_KEYWORDS` in [`evaluator.py`](../backend/app/wg_agent/evaluator.py). Without it, only the bare key is matched.

### B. Tune a component curve (30 min, backend + test)

Every component in [`evaluator.py`](../backend/app/wg_agent/evaluator.py) is pure Python with a boundary-pinned unit test in [`test_evaluator.py`](../backend/tests/test_evaluator.py). Good first PRs: tighten `commute_fit`'s ramp for users with long budgets, widen `size_fit`'s upper tolerance for flats, etc.

1. Edit the relevant component function in [`evaluator.py`](../backend/app/wg_agent/evaluator.py). Keep it pure — no I/O, no state.
2. Run `cd backend && venv/bin/pytest tests/test_evaluator.py -k your_component` to see which boundary assertions break, and either update the curve or the test (match the existing table-driven style).
3. Run the full suite: `venv/bin/pytest tests` — the 78-test suite should stay green.
4. Start a hunt and open the listing drawer — your change shows up immediately as a different bar height and updated `evidence` string.

## Troubleshooting

- **`OPENAI_API_KEY` is not set** — Ensure `set -a && source ../.env && set +a` (or export the variable) in the same shell session before launching `uvicorn`.

- **Alembic / SQLite revision mismatch after manual edits** — Stop the server, `rm ~/.wg_hunter/app.db*`, start again so `upgrade head` applies [`0001_initial.py`](../backend/alembic/versions/0001_initial.py) cleanly.

- **Port 8000 already in use** — `lsof -ti :8000 | xargs kill` (macOS) then restart `uvicorn`.

- **503 on `/` with “frontend/dist/index.html not found”** — Run `npm run build` in `frontend/` so [`main.py`](../backend/app/main.py) can serve the SPA.

- **Empty listing photos in the drawer** — [`repo.save_photos`](../backend/app/wg_agent/repo.py) exists but the v1 `HuntEngine` path does not populate `PhotoRow` yet; detail still returns listing fields and score from SQLite.

- **"Vibe check skipped" in a component bar** — The vibe component degrades to `missing_data=True` when `brain.vibe_score` raises (no `OPENAI_API_KEY`, HTTP error, model returns invalid JSON). The rest of the scorecard still runs and the composite score is computed from the remaining components. Check the backend logs for `vibe_fit:` warnings to see the exact cause.

- **All commutes show "no commute data"** — Either the user hasn't picked `main_locations` in onboarding (nothing to commute to), or `GOOGLE_MAPS_SERVER_KEY` is unset / invalid. [`commute.travel_times`](../backend/app/wg_agent/commute.py) returns `{}` on a missing key, which makes `evaluator.commute_fit` flip to `missing_data` for that listing.
