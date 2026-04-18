# TUM.ai Makeathon 2026 · Campus Co-Pilot

Our submission for Reply's **The Campus Co-Pilot Suite** challenge: autonomous agents that take concrete actions across the university's fragmented digital ecosystem so that students can stop acting as human APIs.

The active workstream is **WG Hunter** — a fully autonomous `wg-gesucht.de` room hunt that searches, ranks, and surfaces listings via a live React dashboard.

```text
┌──────────────┐          ┌──────────────────────────┐          ┌────────────────┐
│ React SPA    │ ──fetch──▶ FastAPI (/api + SPA)     │ ──httpx──▶ Google Maps    │
│ (Vite, TS)   │ ◀── SSE ──│ HuntEngine → evaluator   │ ──httpx──▶ OpenAI (vibe)  │
└──────────────┘          └──────────────────────────┘          └────────────────┘
                                       │                         ┌────────────────┐
                                       ▼                         │ wg-gesucht.de  │
                                  ┌─────────┐    ┌──────────┐    └────────────────┘
                                  │ MySQL   │◀───│ Scraper  │───httpx──────▲
                                  │ (AWS)   │    │ container│
                                  └─────────┘    └──────────┘
```

See [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) for the full diagram.

---

## Quick start — local

Prerequisites (details in [`docs/SETUP.md`](./docs/SETUP.md)):

- **Python 3.11+**
- **Node.js 20+** and **npm 10+**
- An **OpenAI API key**
- Optional: **maps/location API keys** — `VITE_GOOGLE_MAPS_API_KEY` keeps the existing in-browser Google Places Autocomplete for picking `main_locations`, and `GOOGLE_MAPS_SERVER_KEY` powers server-side geocoding fallback, commute routing, and nearby-place enrichment. Without `GOOGLE_MAPS_SERVER_KEY`, listings still scrape and score, but commute and nearby-place context degrade to missing data.

1. Clone and create the env file:

   ```bash
   git clone https://github.com/<your-fork>/tumai-makeathon-2026.git
   cd tumai-makeathon-2026
   cp .env.example .env
   # Edit .env: OPENAI_API_KEY, optional GOOGLE_MAPS_SERVER_KEY, optional VITE_GOOGLE_MAPS_API_KEY
   ```

2. Install + build once:

   ```bash
   # Backend
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

   # Frontend (from repo root)
   cd ../frontend
   npm install
   npm run build
   ```

3. Run the backend — it creates any missing tables on the shared AWS MySQL, resumes any `running` hunts, and serves the built SPA at `/`:

   ```bash
   cd ../backend
   set -a && source ../.env && set +a
   venv/bin/uvicorn app.main:app --reload
   ```

   Open http://127.0.0.1:8000/ — the dashboard is ready.

4. Run the scraper (separate terminal, same `.env`):

   ```bash
   set -a && source ../.env && set +a
   venv/bin/python -m app.scraper.main
   ```

5. Frontend dev loop (optional, for UI iteration) — in a third terminal, from `frontend/`:

   ```bash
   npm run dev
   ```

   Vite serves at http://127.0.0.1:5173/ and proxies `/api/*` to the backend.

### Reset the database

Drop and recreate the MySQL database (coordinate with the team — it's shared):

```sql
DROP DATABASE wg_hunter;
CREATE DATABASE wg_hunter CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Restart the backend; `SQLModel.metadata.create_all` recreates the schema on the next boot.

### Run the test suites

```bash
cd backend && source venv/bin/activate && pytest
cd frontend && npm test
```

---

## Deploy to AWS EC2 (Docker)

Full walkthrough in [`DEPLOYMENT.md`](./DEPLOYMENT.md) and CI/CD setup in [`CI-CONFIGURATION.md`](./CI-CONFIGURATION.md).

The short version:

1. Launch an EC2 instance (t2.micro is enough for demos), open **SSH (22)** and **HTTP (80)** in the security group, SSH in.
2. Install Docker + Compose plugin.
3. Clone the repo on the instance and create `.env` at the repo root with your secrets (`OPENAI_API_KEY`, `VITE_GOOGLE_MAPS_API_KEY`, `GOOGLE_MAPS_SERVER_KEY`, …).
4. Build + run from the repo root:

   ```bash
   docker compose up -d --build
   ```

   The root [`docker-compose.yml`](./docker-compose.yml) starts an nginx frontend (port 80, with the built Vite SPA and a reverse proxy to `/api/*`) and a FastAPI backend that persists its SQLite database to the `wg_data` named volume.

5. Verify: `curl http://<EC2_PUBLIC_IP>/api/health` → `{"status":"ok"}`. Open `http://<EC2_PUBLIC_IP>/` for the app and `/docs` for interactive API docs.

### Continuous deployment

[`.github/workflows/deploy.yml`](./.github/workflows/deploy.yml) pushes to EC2 on every commit to `main`. Set the three secrets in **Settings → Secrets and variables → Actions**:

| Secret | Value |
| ------ | ----- |
| `EC2_HOST` | Public IPv4 or DNS of the instance |
| `EC2_USERNAME` | `ec2-user` (Amazon Linux) or `ubuntu` (Ubuntu) |
| `EC2_SSH_KEY` | Full contents of your `.pem` private key |

---

## Environment variables

From [`.env.example`](./.env.example). Vite reads the same file via [`envDir: '..'`](./frontend/vite.config.ts), so one repo-root `.env` covers both sides.

| Variable | Required | Consumer | Purpose |
| -------- | -------- | -------- | ------- |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | **yes** | backend + scraper | AWS RDS MySQL credentials. `backend/app/wg_agent/db.py` assembles the `mysql+pymysql://…` DSN from these at import time and refuses to boot if any are missing |
| `OPENAI_API_KEY` | **yes** | backend | OpenAI Chat Completions for the evaluator's narrow vibe component ([`brain.vibe_score`](./backend/app/wg_agent/brain.py)) plus the legacy orchestrator path |
| `OPENAI_MODEL` | no | backend | Override model (`gpt-4o-mini` by default) |
| `VITE_GOOGLE_MAPS_API_KEY` | optional | browser | Places Autocomplete in onboarding (referrer- + API-restricted) |
| `GOOGLE_MAPS_SERVER_KEY` | optional | backend | Google Geocoding API + Distance Matrix API + Places API (New) for listing fallback geocoding, commute times, and nearby amenity distances |
| `GOOGLE_MAPS_MAX_RPS` | no | backend | Process-wide throttle for backend Google Maps requests; defaults to `8` |
| `WG_SECRET_KEY` | no | backend | Pin the Fernet key used to encrypt credentials (else auto-generated at `~/.wg_hunter/secret.key`) |
| `WG_RESCAN_INTERVAL_MINUTES` | no | backend | Shorten rescan interval during demos |
| `WG_STATE_FILE` | no | backend | Playwright `storage_state.json` for authenticated flows (reserved for post-v1) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | no | backend | Reserved for Bedrock-based alternatives |

---

## Documentation

All developer docs live under **[`docs/`](./docs/README.md)**. Start there:

1. [`docs/SETUP.md`](./docs/SETUP.md) — clone to running in 30 min.
2. [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — runtime shape + request flow.
3. [`docs/DATA_MODEL.md`](./docs/DATA_MODEL.md) — entities, ER diagram, the three-layer rule.
4. [`docs/BACKEND.md`](./docs/BACKEND.md), [`docs/FRONTEND.md`](./docs/FRONTEND.md), [`docs/AGENT_LOOP.md`](./docs/AGENT_LOOP.md) — walkthroughs.
5. [`docs/DESIGN.md`](./docs/DESIGN.md), [`docs/DECISIONS.md`](./docs/DECISIONS.md), [`docs/WG_GESUCHT.md`](./docs/WG_GESUCHT.md).
6. [`docs/ROADMAP.md`](./docs/ROADMAP.md) — what's next and what's deliberately out of scope.
7. [`docs/_generated/openapi.json`](./docs/_generated/openapi.json) — OpenAPI spec (regenerated after API changes).

Project context (challenge brief, TUM systems inventory, AWS notes) lives under [`context/`](./context).

Coding guidelines for humans and LLM agents are in [`CLAUDE.md`](./CLAUDE.md) and [`AGENTS.md`](./AGENTS.md). Both point at `docs/README.md` first.
