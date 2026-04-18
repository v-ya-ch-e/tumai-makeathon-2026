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
6. Streams every action to a live dashboard at `/wg/`.

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

# 3. Run the server
uvicorn app.main:app --reload
# open http://127.0.0.1:8000/wg/
```

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

## API endpoints

| Method | Path                     | Description                                              |
| ------ | ------------------------ | -------------------------------------------------------- |
| `GET`  | `/wg/`                   | Dashboard: form to start a new hunt + list of recent runs. |
| `POST` | `/wg/hunt`               | Kick off a new hunt. Body: `HuntRequest` (see `api.py`). |
| `GET`  | `/wg/hunt/{run_id}`      | JSON state of a hunt (listings, messages, action log).  |
| `GET`  | `/wg/hunt/{run_id}/stream` | Server-Sent Events stream of live agent actions.        |
| `GET`  | `/wg/runs/{run_id}`      | Rendered HTML view of a hunt.                           |
| `GET`  | `/health`                | Readiness check.                                        |

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
- Every action is logged and streamed to the dashboard.

## File layout

```
backend/
├── app/
│   ├── main.py                  # FastAPI entry point (plugs in the WG router)
│   └── wg_agent/
│       ├── WG_GESUCHT.md        # Live recon notes — update when the site changes
│       ├── models.py            # Pydantic: SearchProfile, Listing, Hunt, …
│       ├── browser.py           # Playwright driver + BeautifulSoup parsers
│       ├── brain.py             # OpenAI: score / draft / classify / reply
│       ├── orchestrator.py      # The agent loop + action log
│       ├── api.py               # FastAPI router + SSE stream
│       ├── templates/           # Jinja2 (home + run)
│       └── static/              # CSS + tiny JS
└── tests/
    ├── test_wg_parser.py
    └── test_orchestrator.py
```
