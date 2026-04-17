# TUM.ai Makeathon 2026 · Campus Co-Pilot

Our submission for Reply's **The Campus Co-Pilot Suite** challenge: autonomous
agents that take concrete actions across the university's fragmented digital
ecosystem so that students can stop acting as human APIs.

## What's in this repo

| Path | What it is |
|------|------------|
| [`backend/`](./backend) | FastAPI backend. |
| [`backend/app/wg_agent/`](./backend/app/wg_agent) | **The WG Hunter agent** — fully autonomous `wg-gesucht.de` room hunt: search → rank → message → schedule viewing. |
| [`context/`](./context) | Verbatim challenge brief, TUM-systems inventory, AWS resources, code examples. |
| [`CLAUDE.md`](./CLAUDE.md), [`AGENTS.md`](./AGENTS.md) | Coding guidelines for humans and LLM agents collaborating on this repo. |

## Quickstart

```bash
cp .env.example .env            # fill in OPENAI_API_KEY
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000/wg/](http://127.0.0.1:8000/wg/), fill in the hunt
form, click **Start autonomous hunt**, and watch the live action log.

See [`backend/README.md`](./backend/README.md) for the full walkthrough and
[`backend/app/wg_agent/WG_GESUCHT.md`](./backend/app/wg_agent/WG_GESUCHT.md) for
the site-reconnaissance notes that ground the scraper.
