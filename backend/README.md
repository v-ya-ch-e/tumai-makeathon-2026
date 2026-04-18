# Backend · WG Hunter agent

FastAPI backend that runs the autonomous **WG-Gesucht room hunter** for the TUM.ai
Makeathon 2026 *Campus Co-Pilot* challenge.

The agent:

1. Logs into [`wg-gesucht.de`](https://www.wg-gesucht.de) (cookie-first, credentials-fallback).
2. Searches room listings that match the student's requirements.
3. Deep-scrapes every short-listed listing and scores it with an LLM.
4. Drafts and sends tailored intro messages (German or English, matched to the listing).
5. Polls the inbox for replies, classifies each landlord response, and:
   - confirms the viewing slot when the landlord proposes one, or
   - answers any questions the landlord asks, or
   - drops already-rented listings.
6. Streams every action to the React dashboard served from `frontend/dist/`.

See [`app/wg_agent/WG_GESUCHT.md`](app/wg_agent/WG_GESUCHT.md) for the playbook
that grounds the scraper/selector choices.

## Quickstart

```bash
# 1. Install dependencies (Python 3.11+)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# 2. Configure secrets
cp ../.env.example .env
#  → edit .env and fill in OPENAI_API_KEY (and optionally WG_STATE_FILE)

# 3. Build the frontend (once; re-run after UI changes)
(cd ../frontend && npm install && npm run build)

# 4. Run the server
uvicorn app.main:app --reload
# open http://127.0.0.1:8000/
```

For active frontend development, run `npm run dev` in `frontend/` alongside
the backend — Vite proxies `/api` to `127.0.0.1:8000`.

## Environment variables

| Variable         | Required | Purpose                                                                 |
| ---------------- | -------- | ----------------------------------------------------------------------- |
| `OPENAI_API_KEY` | yes      | Used by `app/wg_agent/brain.py` for scoring, drafting, classification.  |
| `OPENAI_MODEL`   | no       | Defaults to `gpt-4o-mini`. Use `gpt-4o` for stronger German writing.    |
| `WG_STATE_FILE`  | no       | Path to Playwright `storage_state.json`. Recommended for demo runs.     |

## One-time wg-gesucht session capture (recommended)

Credentialed login on every run risks CAPTCHA. Instead, log in **once** manually
and save the cookies:

```bash
python - <<'PY'
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto("https://www.wg-gesucht.de/login.html")
        print(">>> Log in manually in the browser, then press Enter here.")
        input()
        await ctx.storage_state(path="wg_state.json")
        await browser.close()
asyncio.run(main())
PY
```

Then point the agent at the generated file by setting `WG_STATE_FILE=wg_state.json`
in your `.env`, or pass the absolute path via the "Playwright storage_state.json"
field on the home page.

## API endpoints (v1 JSON + SSE)

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/users` | Create account. Body: `CreateUserBody`. 201 or 409. |
| `GET` | `/api/users/{username}` | User profile JSON. |
| `PUT` | `/api/users/{username}/search-profile` | Upsert search profile (DTO body). |
| `GET` | `/api/users/{username}/search-profile` | Search profile or 404. |
| `PUT` | `/api/users/{username}/credentials` | Save WG credentials (email+password or `storage_state`). 204. |
| `DELETE` | `/api/users/{username}/credentials` | Remove stored credentials. 204. |
| `GET` | `/api/users/{username}/credentials` | `{connected, saved_at}` only (no secrets). |
| `POST` | `/api/users/{username}/hunts` | Create hunt row + boot action (orchestrator wired later). 201. |
| `POST` | `/api/hunts/{id}/stop` | Mark hunt finished; appends `done` action. |
| `GET` | `/api/hunts/{id}` | Hunt DTO (listings + actions). |
| `GET` | `/api/hunts/{id}/stream` | SSE action log + keep-alives until terminal status. |
| `GET` | `/api/listings/{listing_id}?hunt_id=` | Listing detail + photo URLs + score. |
| `GET` | `/health`, `/api/health` | Readiness check. |
| `GET` | `/`, `/<anything>` | Serves `frontend/dist/index.html` (SPA fallback). |

Request/response shapes live in `app/wg_agent/dto.py`. The legacy `HuntRequest` class remains in `api.py` for `tests/test_orchestrator.py`.

## Tests

```bash
# Parser regression (hits wg-gesucht once, then caches HTML in tests/fixtures/).
python tests/test_wg_parser.py

# Orchestrator end-to-end with mock browser + mock OpenAI.
python tests/test_orchestrator.py
```

## Safety & pacing

This is a **hackathon demo**: wg-gesucht's ToS do not permit automated scraping,
so the agent is configured for safe, low-volume demonstration use only:

- Default `dry_run=True` — the agent drafts messages but does **not** send them.
- At most `max_messages_to_send=5` per run.
- 35-second pace between outbound messages.
- Inbox polled every 45 s for max 8 minutes.
- Every action is logged and streamed to the React dashboard.

## File layout

```
backend/
├── app/
│   ├── main.py                  # FastAPI entry point (includes `/api` WG router)
│   └── wg_agent/
│       ├── WG_GESUCHT.md        # Live recon notes — update when the site changes
│       ├── models.py            # Domain Pydantic models (agent + repo)
│       ├── dto.py               # API-only Pydantic DTOs (JSON with frontend)
│       ├── browser.py           # Playwright driver + BeautifulSoup parsers
│       ├── brain.py             # OpenAI: score / draft / classify / reply
│       ├── orchestrator.py      # The agent loop + action log
│       └── api.py               # FastAPI `/api` router + SSE stream
└── tests/
    ├── test_wg_parser.py
    └── test_orchestrator.py
```
