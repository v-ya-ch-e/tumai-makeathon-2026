# TUM.ai Makeathon 2026 · Campus Co-Pilot

Our submission for Reply's **The Campus Co-Pilot Suite** challenge: autonomous agents that take concrete actions across the university's fragmented digital ecosystem so that students can stop acting as human APIs.

The active workstream is **WG Hunter** — a fully autonomous `wg-gesucht.de` room hunt that searches, ranks, and surfaces listings via a live React dashboard.

```text
┌──────────────┐          ┌──────────────────────────┐          ┌────────────────┐
│ React SPA    │ ──fetch──▶ FastAPI (/api + SPA)     │ ──httpx──▶ wg-gesucht.de  │
│ (Vite, TS)   │ ◀── SSE ──│ HuntEngine + OpenAI      │ ──httpx──▶ OpenAI         │
└──────────────┘          │ SQLite (+ Alembic)       │ ──httpx──▶ Google Maps    │
                          └──────────────────────────┘
```

See [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) for the full diagram.

---

## Quick start — local

Prerequisites (details in [`docs/SETUP.md`](./docs/SETUP.md)):

- **Python 3.11+**
- **Node.js 20+** and **npm 10+**
- An **OpenAI API key**
- Optional: **Google Maps Platform key(s)** — one `VITE_GOOGLE_MAPS_API_KEY` for in-browser Places Autocomplete, and a separate `GOOGLE_MAPS_SERVER_KEY` for server-side Geocoding + Routes API commute scoring. Without them, onboarding still works but locations fall back to disabled/free-text inputs and listings carry no commute data.

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

3. Run the backend — it migrates SQLite (`~/.wg_hunter/app.db`), resumes any `running` hunts, and serves the built SPA at `/`:

   ```bash
   cd ../backend
   set -a && source ../.env && set +a
   venv/bin/uvicorn app.main:app --reload
   ```

   Open http://127.0.0.1:8000/ — the dashboard is ready.

4. Frontend dev loop (optional, for UI iteration) — in a second terminal, from `frontend/`:

   ```bash
   npm run dev
   ```

   Vite serves at http://127.0.0.1:5173/ and proxies `/api/*` to the backend.

### Reset the database

```bash
rm ~/.wg_hunter/app.db*
```

Restart `uvicorn`; Alembic recreates the schema.

### Run the test suites

```bash
cd backend && source venv/bin/activate && pytest
cd frontend && npm test
```

---

## Deploy to AWS EC2 (Docker)

Full walkthrough in [`DEPLOYMENT.md`](./DEPLOYMENT.md) and CI/CD setup in [`CI-CONFIGURATION.md`](./CI-CONFIGURATION.md).

The short version:

1. Launch an EC2 instance (t2.micro is enough for demos), open **SSH (22)** and **TCP 8000** in the security group, SSH in.
2. Install Docker + Compose plugin.
3. Clone the repo on the instance and create `.env` at the repo root with your secrets (`OPENAI_API_KEY`, `GOOGLE_MAPS_SERVER_KEY`, …).
4. Edit `backend/docker-compose.yml` to pass the secrets through (see [`DEPLOYMENT.md` step 5](./DEPLOYMENT.md)):

   ```yaml
   services:
     backend:
       build: .
       ports:
         - "8000:8000"
       volumes:
         - .:/app
       env_file:
         - ../.env
       environment:
         - PYTHONUNBUFFERED=1
   ```

5. Build + run:

   ```bash
   cd backend
   docker compose up -d --build
   ```

6. Verify: `curl http://<EC2_PUBLIC_IP>:8000/api/health` → `{"status":"ok"}`. Interactive docs at `/docs`.

> **Heads-up on the SPA**: the current `backend/Dockerfile` only bundles the backend; the React SPA is served by FastAPI from `frontend/dist/` at the repo root. Build the frontend on the host (`cd frontend && npm install && npm run build`) and add `../frontend/dist:/frontend/dist:ro` to the compose `volumes:` block, or extend the Dockerfile with a multi-stage Node build. Without the SPA bundle, `/api/*` still responds but `/` returns 503. See [`DEPLOYMENT.md`](./DEPLOYMENT.md) for the full note.

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
| `OPENAI_API_KEY` | **yes** | backend | OpenAI Chat Completions for listing scoring ([`brain.py`](./backend/app/wg_agent/brain.py)) |
| `OPENAI_MODEL` | no | backend | Override model (`gpt-4o-mini` by default) |
| `VITE_GOOGLE_MAPS_API_KEY` | optional | browser | Places Autocomplete in onboarding (referrer- + API-restricted) |
| `GOOGLE_MAPS_SERVER_KEY` | optional | backend | Geocoding + Routes API (IP- + API-restricted, **never** shipped to the browser) |
| `WG_DB_URL` | no | backend | Override SQLite path / swap in Postgres |
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
6. [`docs/_generated/openapi.json`](./docs/_generated/openapi.json) — OpenAPI spec (regenerated after API changes).

Project context (challenge brief, TUM systems inventory, AWS notes) lives under [`context/`](./context).

Coding guidelines for humans and LLM agents are in [`CLAUDE.md`](./CLAUDE.md) and [`AGENTS.md`](./AGENTS.md). Both point at `docs/README.md` first.
