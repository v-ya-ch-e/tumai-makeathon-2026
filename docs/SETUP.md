# Setup

Clone the repo and run the WG Hunter stack locally: FastAPI backend + Vite-built React UI, with SQLite under `~/.wg_hunter/`.

## Prerequisites

- Python **3.11+**
- **Node.js** 20+ (Node **24** is what we use day-to-day; it works with the checked-in lockfile)
- **npm** 10+
- A working **`OPENAI_API_KEY`** (see [`.env.example`](../.env.example))
- A **Google Maps Platform API key** with **Maps JavaScript API** and **Places API (New)** enabled, exposed as `VITE_GOOGLE_MAPS_API_KEY`. Used by the onboarding wizard's Main locations autocomplete ([`frontend/src/components/PlaceAutocomplete.tsx`](../frontend/src/components/PlaceAutocomplete.tsx)). Without it the field falls back to a disabled placeholder but the rest of onboarding still works. Restrict the key to HTTP referrers (`http://localhost:5173/*`, `http://localhost:8000/*`, and your deployed origin) and to the two APIs above.

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

Add a new optional preference tile to the wizard. The backend stores `SearchProfile.preferences` as `list[str]` ([`SearchProfileRow.preferences`](../backend/app/wg_agent/db_models.py)); any new string key is accepted without a migration.

1. Open [`frontend/src/pages/OnboardingPreferences.tsx`](../frontend/src/pages/OnboardingPreferences.tsx).
2. Append one object to the `TILES` array: a unique `key` (snake_case string), a short `label`, and an SVG `path` inside the existing `Icon` wrapper (copy an existing tile’s structure).
3. Save, run `npm run dev` (or `npm run build` if you test against production-like static files).
4. Walk through onboarding again; select the new tile so it is included in `preferences` on `PUT /api/users/{username}/search-profile`.
5. Start a hunt from the dashboard. The LLM may reference the new tag in `score_reason` / match lists when it is relevant to a listing.

No Python changes are required for this path.

## Troubleshooting

- **`OPENAI_API_KEY` is not set** — Ensure `set -a && source ../.env && set +a` (or export the variable) in the same shell session before launching `uvicorn`.

- **Alembic / SQLite revision mismatch after manual edits** — Stop the server, `rm ~/.wg_hunter/app.db*`, start again so `upgrade head` applies [`0001_initial.py`](../backend/alembic/versions/0001_initial.py) cleanly.

- **Port 8000 already in use** — `lsof -ti :8000 | xargs kill` (macOS) then restart `uvicorn`.

- **503 on `/` with “frontend/dist/index.html not found”** — Run `npm run build` in `frontend/` so [`main.py`](../backend/app/main.py) can serve the SPA.

- **Empty listing photos in the drawer** — [`repo.save_photos`](../backend/app/wg_agent/repo.py) exists but the v1 `HuntEngine` path does not populate `PhotoRow` yet; detail still returns listing fields and score from SQLite.
