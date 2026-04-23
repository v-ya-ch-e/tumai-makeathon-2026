# Sherlock Homes — WG Hunter

> *Investigate less. Belong sooner.*

**Sherlock Homes** is an autonomous rental detective for Munich. It watches `wg-gesucht.de`, `living.tum.de`, and `kleinanzeigen.de` around the clock, deep-scrapes every fresh listing, scores it against your personal brief — commute, price, WG vibe, neighbourhood preferences — and surfaces the strongest leads on a live dashboard before your inbox even pings.

Built in 36 hours at **TUM.ai Makeathon 2026** as team `doubleu`'s submission to Reply's *Campus Co-Pilot Suite* challenge.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite&logoColor=white)
![TailwindCSS](https://img.shields.io/badge/Tailwind-3-38BDF8?logo=tailwindcss&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-B08D57)

---

## Demo

<!--
Drop a hosted demo URL, screenshots, and/or a short video here.
Recommended layout:

### Live demo
http://<your-ec2-host>/

### Dashboard
![Dashboard](docs/assets/dashboard.png)

### Onboarding wizard
![Onboarding](docs/assets/onboarding.png)

### Listing drawer (component breakdown)
![Drawer](docs/assets/drawer.png)
-->

> **Live demo, screenshots, and walkthrough video coming soon.** Drop assets under `docs/assets/` and fill in this section.

---

## Why we built it

Every semester, roughly ten thousand students land in Munich and collide with the same failure mode: hundreds of near-identical WG ads, a three-hour window before a good room is gone, and no way to tell a twenty-minute commute from a fifty-minute one without opening six tabs. The challenge asked us to turn TUM's fragmented digital ecosystem into something that *acts* on behalf of the student. WG Hunter is that agent for the housing problem — it never sleeps, never copy-pastes, and never picks the listing that looks cheap but sits an hour from your lecture hall.

---

## What it does

Two autonomous agents cooperate over a shared database.

1. **The scraper** runs continuously, independent of any user. It drives a registry of source plugins — **wg-gesucht**, **TUM Living** (GraphQL), **Kleinanzeigen** — walks each source newest-first, deep-scrapes every new listing into a global pool with photos, coordinates, rent, and prose, and refreshes stale entries on a schedule.
2. **The per-user matcher** spawns one background task per student the moment they save their search profile. For every new candidate in the pool it asks Google Distance Matrix how long the commute is in each mode, asks Google Places how far the user's preferred amenities are, then runs a **scorecard evaluator** — six deterministic component curves (price, size, WG size, availability, commute, preferences) plus one narrow LLM call that judges *only* prose vibe — and composes a weighted score with hard-filter vetoes.

The dashboard streams every step over **Server-Sent Events**, so the action log and ranked listing cards update live. Click any listing to see the full component breakdown, commute times per mode, nearby-place distances, and a link straight back to the source.

**Above a configurable score threshold (default 0.9), the agent emails the student immediately via Amazon SES** — so the best leads arrive in the inbox within minutes of being posted, not hours.

---

## Highlights

- **Autonomous, not reactive.** Once a profile is saved, the agent runs forever. Restart the server and every user's matcher loop resumes automatically.
- **Deep-scrape once, match for many.** A single scraped listing feeds every user's scoring pass — O(1) HTTP cost per listing regardless of team size.
- **Explainable ranking.** Every score is a sum of transparent component curves. The LLM is confined to one narrow prose-vibe judgement; nothing that matters to ranking hides behind "ask the model."
- **Multi-source by design.** Namespaced listing ids (`source:external_id`) and a first-class `kind` column (`wg` / `flat`) make adding a fourth site a single new module + registry entry.
- **Live action log.** Server-Sent Events stream `search`, `new_listing`, `evaluate`, and `rescan` events to the browser in real time. No polling, no WebSockets.
- **Commute-aware from day one.** Google Geocoding fallback for listing addresses, Distance Matrix per mode (walk / bike / transit / drive), Places lookups for the user's configured main locations and nearby-amenity preferences.
- **Email-on-match alerts.** When a listing scores at or above your threshold, Amazon SES fires off an HTML mail with the score, rent, and a deep link before you've refreshed the browser.
- **Desktop-first SPA with a point of view.** Editorial detective skin (*Sherlock Homes* branding — fog surfaces, deep ink, brass and burgundy accents, zero emojis in product chrome). See [`docs/DESIGN.md`](./docs/DESIGN.md).
- **Documented like a real product.** Every architecture decision has an ADR, every backend and frontend file is covered in a tour doc, the OpenAPI spec is committed, and the three-layer rule (UI ↔ DTO ↔ domain ↔ row) is enforced across the codebase.

---

## Architecture at a glance

```text
┌──────────────┐          ┌──────────────────────────┐          ┌──────────────────────────┐
│ React SPA    │ ──fetch──▶ FastAPI (/api + SPA)     │ ──httpx──▶ Google Maps + OpenAI    │
│ (Vite, TS)   │ ◀── SSE ──│ matcher → evaluator     │                                    │
└──────────────┘          └──────────────────────────┘          ┌──────────────────────────┐
                                       │                         │ wg-gesucht.de            │
                                       ▼                         │ living.tum.de (GraphQL)  │
                                  ┌─────────┐    ┌──────────┐    │ kleinanzeigen.de         │
                                  │ MySQL   │◀───│ Scraper  │───▶└──────────────────────────┘
                                  │ (AWS)   │    │ (laptop) │
                                  └─────────┘    └──────────┘
```

Full diagram with component boundaries, invariants, and request flow sequence diagrams: [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md).

**Stack**

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | **FastAPI** + asyncio matcher tasks | Single process hosts JSON API, SSE stream, built SPA, and per-user match loops |
| Scraper | **Standalone Python** with pluggable `Source` registry | Sole writer of listings; decouples scrape cadence from per-user match cadence |
| Persistence | **MySQL** (AWS RDS) + **SQLModel** | Single shared DB; schema bootstrapped via `metadata.create_all` on startup |
| Frontend | **Vite + React 19 + TypeScript + Tailwind 3** | Desktop-first SPA served straight from FastAPI |
| Scoring | **Scorecard evaluator** (code) + **OpenAI** (narrow vibe call) | Deterministic, unit-testable components; LLM judges only what it's good at |
| Alerts | **Amazon SES** | HTML mail above a score threshold; disables cleanly without credentials |
| External | wg-gesucht / living.tum / kleinanzeigen (httpx + GraphQL/HTML), Google Maps Platform, OpenAI | No first-party APIs for the listing sites — we scrape defensively |

---

## Quick start — local

**Prerequisites** (full details in [`docs/SETUP.md`](./docs/SETUP.md)):

- **Python 3.11+**
- **Node.js 20+** and **npm 10+**
- An **OpenAI API key**
- Optional: **Google Maps keys** — `VITE_GOOGLE_MAPS_API_KEY` drives in-browser Places Autocomplete; `GOOGLE_MAPS_SERVER_KEY` powers server-side geocoding, commute routing, and nearby-place enrichment. Without the server key, listings still scrape and score — commute and nearby context simply degrade to missing data.

```bash
# 1. Clone and configure
git clone https://github.com/<your-fork>/tumai-makeathon-2026.git
cd tumai-makeathon-2026
cp .env.example .env
# Edit .env: DB_* credentials, OPENAI_API_KEY, optional Google Maps keys

# 2. Backend
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Frontend
cd ../frontend
npm install
npm run build

# 4. Run the backend (serves the built SPA at /, resumes every user's matcher loop)
cd ../backend
set -a && source ../.env && set +a
venv/bin/uvicorn app.main:app --reload

# 5. (Separate terminal, same .env) Run the scraper
cd backend
set -a && source ../.env && set +a
venv/bin/python -m app.scraper.main
```

Open http://127.0.0.1:8000/ — the dashboard is ready. For UI iteration, `npm run dev` inside `frontend/` serves Vite at http://127.0.0.1:5173/ with `/api/*` proxied to the backend.

### Reset the database

The schema is bootstrapped with `SQLModel.metadata.create_all`. For destructive changes, drop and recreate (coordinate with the team — the AWS MySQL is shared):

```sql
DROP DATABASE wg_hunter;
CREATE DATABASE wg_hunter CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Restart the backend and the schema comes back clean.

### Run the test suites

```bash
cd backend && source venv/bin/activate && pytest
cd frontend && npm test
```

---

## Deploy to AWS EC2 (Docker)

Full walkthrough in [`DEPLOYMENT.md`](./DEPLOYMENT.md); CI/CD in [`CI-CONFIGURATION.md`](./CI-CONFIGURATION.md).

```bash
# On the EC2 instance, after Docker + Compose plugin are installed:
git clone <repo>
cd tumai-makeathon-2026
# create .env with your secrets
docker compose up -d --build
```

The root [`docker-compose.yml`](./docker-compose.yml) runs:

- **frontend** — nginx on port 80 serving the built Vite SPA with `/api/*` reverse-proxied to the backend.
- **backend** — FastAPI talking to the shared AWS RDS MySQL via the `DB_*` env vars.

The scraper runs on a team laptop against the same MySQL — keeping the cloud deploy backend + frontend only avoids long-running container drift and unnecessary egress. See [`docs/SCRAPER.md`](./docs/SCRAPER.md#local-scraper-run-laptop).

Verify:

```bash
curl http://<EC2_PUBLIC_IP>/api/health
# {"status":"ok"}
```

Open `http://<EC2_PUBLIC_IP>/` for the app and `/docs` for interactive API docs.

### Continuous deployment

[`.github/workflows/deploy.yml`](./.github/workflows/deploy.yml) pushes to EC2 on every commit to `main`. Three secrets in **Settings → Secrets and variables → Actions**:

| Secret | Value |
| ------ | ----- |
| `EC2_HOST` | Public IPv4 or DNS of the instance |
| `EC2_USERNAME` | `ec2-user` (Amazon Linux) or `ubuntu` (Ubuntu) |
| `EC2_SSH_KEY` | Full contents of the `.pem` private key |

---

## Environment variables

Single source: [`.env.example`](./.env.example). Vite reads the same file via [`envDir: '..'`](./frontend/vite.config.ts), so one repo-root `.env` covers both sides.

| Variable | Required | Consumer | Purpose |
| -------- | -------- | -------- | ------- |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | **yes** | backend + scraper | AWS RDS MySQL credentials. `backend/app/wg_agent/db.py` assembles the DSN and refuses to boot if any are missing |
| `OPENAI_API_KEY` | **yes** | backend | OpenAI Chat Completions for the evaluator's narrow vibe component |
| `OPENAI_MODEL` | no | backend | Override model (`gpt-4o-mini` by default) |
| `VITE_GOOGLE_MAPS_API_KEY` | optional | browser | Places Autocomplete in onboarding (referrer- + API-restricted) |
| `GOOGLE_MAPS_SERVER_KEY` | optional | backend | Google Geocoding + Distance Matrix + Places (New) for listing fallback geocoding, commute times, and nearby amenity distances |
| `GOOGLE_MAPS_MAX_RPS` | no | backend | Process-wide throttle for backend Google Maps requests; defaults to `8` |
| `WG_SECRET_KEY` | no | backend | Pin the Fernet key used to encrypt credentials (else auto-generated at `~/.wg_hunter/secret.key`) |
| `WG_RESCAN_INTERVAL_MINUTES` | no | backend | Global floor on how often each per-user matcher re-checks the listing pool. Default `3` |
| `WG_STATE_FILE` | no | backend | Playwright `storage_state.json` for authenticated flows (reserved for post-v1) |
| `SCRAPER_ENABLED_SOURCES` | no | scraper | Comma-separated source names. Default `wg-gesucht`. Valid: `wg-gesucht`, `tum-living`, `kleinanzeigen` |
| `SCRAPER_KIND` | no | scraper | Restrict verticals. One of `wg`, `flat`, `both`. Default `both` |
| `SCRAPER_CITY` / `SCRAPER_MAX_RENT` / `SCRAPER_INTERVAL_SECONDS` / `SCRAPER_REFRESH_HOURS` / `SCRAPER_MAX_AGE_DAYS` | no | scraper | Tune the scraper loop. Defaults: `München` / `2000` / `300` / `24` / `4` |
| `SCRAPER_ENRICH_ENABLED` / `SCRAPER_ENRICH_MODEL` / `SCRAPER_ENRICH_MIN_DESC_CHARS` | no | scraper | Optional LLM enrichment that fills missing structured fields when the description states them clearly. Default off |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | no | backend | IAM credentials for Amazon SES. Used by [`notifier.py`](./backend/app/wg_agent/notifier.py) to email users when a new match scores at/above `WG_NOTIFY_THRESHOLD`. Blank values silently disable notifications |
| `SES_FROM_EMAIL` | no | backend | Sender identity (must be a verified SES identity). Default `noreply@doubleu.team` |
| `WG_NOTIFY_THRESHOLD` | no | backend | Minimum match score (inclusive) that triggers an email. Default `0.9` |
| `ENABLE_EMAIL_DEBUG` | no | backend | Set to `1` to expose `GET /api/debug/send-test-email?to=…` for smoke-testing SES |

---

## Repository layout

```text
.
├── README.md ────────────── this file — showcase + quick-start + env table
├── CLAUDE.md ────────────── agent orientation + full doc tree
├── AGENTS.md ────────────── pointer to CLAUDE.md + docs/README.md
├── DEPLOYMENT.md ────────── AWS EC2 + Docker walkthrough
├── CI-CONFIGURATION.md ─── GitHub Actions → EC2 pipeline
├── docker-compose.yml ──── frontend (nginx) + backend (FastAPI)
├── .env.example ────────── every supported environment variable
│
├── docs/                    developer docs (single source of truth)
│   ├── README.md ────────── index + read-in-order + three-layer rule
│   ├── SETUP.md ─────────── clone-to-running in ~30 min
│   ├── ARCHITECTURE.md ─── runtime shape + request-flow sequence diagrams
│   ├── DATA_MODEL.md ───── every table + DTO + ER diagram
│   ├── BACKEND.md ───────── file-by-file tour of backend/app/wg_agent/
│   ├── FRONTEND.md ──────── file-by-file tour of frontend/src/
│   ├── DESIGN.md ────────── Sherlock Homes brand, palette, typography, primitives
│   ├── SCRAPER.md ───────── multi-source scraper contract + per-source recon
│   ├── DECISIONS.md ────── ADR log (~25 decisions and counting)
│   ├── ROADMAP.md ───────── queued / later / done-recently
│   └── _generated/openapi.json   committed OpenAPI spec
│
├── backend/                 FastAPI app + scraper
│   ├── app/main.py ─────── lifespan: init_db → resume matchers → serve API + SPA
│   ├── app/wg_agent/ ──── agent package (api, repo, periodic, evaluator, brain, …)
│   ├── app/scraper/ ──── scraper process + sources/ plugins
│   └── tests/ ────────── pytest suite (parser, repo, evaluator, periodic, commute, …)
│
├── frontend/                Vite + React SPA
│   ├── src/App.tsx ────── router: onboarding wizard, dashboard, profile, health
│   ├── src/pages/ ────── screens
│   ├── src/components/ ─ shared components + ui/ primitives
│   └── src/lib/api.ts ── fetch + SSE + toCamel/toSnake
│
└── context/                 hackathon background (challenge brief, TUM systems, snippets)
```

Suggested reading order for contributors:

1. [`docs/SETUP.md`](./docs/SETUP.md) — clone to running in 30 min.
2. [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — runtime shape and request flow.
3. [`docs/DATA_MODEL.md`](./docs/DATA_MODEL.md) — entities, ER diagram, the three-layer rule.
4. [`docs/BACKEND.md`](./docs/BACKEND.md) and [`docs/FRONTEND.md`](./docs/FRONTEND.md) — file-by-file tours.
5. [`docs/DESIGN.md`](./docs/DESIGN.md), [`docs/SCRAPER.md`](./docs/SCRAPER.md), [`docs/DECISIONS.md`](./docs/DECISIONS.md).
6. [`docs/ROADMAP.md`](./docs/ROADMAP.md) — what's next and what's deliberately out of scope.
7. [`docs/_generated/openapi.json`](./docs/_generated/openapi.json) — OpenAPI spec.

Coding guidelines for both humans and LLM agents: [`CLAUDE.md`](./CLAUDE.md) and [`AGENTS.md`](./AGENTS.md).

---

## Scope — what's in v1 (and what isn't)

**In v1**

- Vite + React onboarding (profile → requirements → preferences) and a dashboard with SSE-fed action log, ranked listing cards, filter bar, and a component-breakdown drawer.
- Multi-source scraper (wg-gesucht / TUM Living / Kleinanzeigen) with pluggable `Source` registry, newest-first walking, freshness-based pagination termination, and optional LLM enrichment for sparse stubs.
- Per-user `UserAgent` matcher loops, auto-resumed on boot for every saved search profile.
- Commute-aware scoring: Google Geocoding fallback, Distance Matrix per mode, Places for nearby preferences.
- Scorecard evaluator with deterministic hard filter, six component curves, narrow vibe LLM call, weighted composition.
- Amazon SES email alerts above a configurable score threshold.
- MySQL + SQLModel with schema bootstrap via `metadata.create_all`; Fernet-encrypted optional wg-gesucht credentials at rest.

**Deliberately out of v1**

- Landlord messaging, inbox polling, and viewing-scheduling flows (the helper functions are staged but nothing mounts them).
- Deterministic pre-filter at the search-URL level — we currently scrape before vetoing (see [`docs/ROADMAP.md`](./docs/ROADMAP.md) for the proposed change).
- Learned composition weights / user 👍-👎 feedback.
- AWS Bedrock — the challenge mentioned it, but we use OpenAI directly for a simpler local setup. See `ROADMAP.md` for the swap-in plan.

---

## Team

Built by **team `doubleu`** at TUM.ai Makeathon 2026, for Reply's *Campus Co-Pilot Suite* challenge. The challenge brief and TUM systems inventory live under [`context/`](./context/).

<!-- Optional: replace with full member credits when ready. -->

---

## License

Released under the [MIT License](./LICENSE) — copyright © 2026 Team `doubleu` and contributors. Use it, fork it, ship your own campus co-pilot.

---

## Acknowledgements

- **TUM.ai** for running the Makeathon.
- **Reply (Data Reply)** for the *Campus Co-Pilot Suite* challenge and sponsorship.
- **OpenAI** for the model powering the narrow vibe-score call.
- **Google Maps Platform** for geocoding, Distance Matrix, and Places (New).
- The maintainers of **FastAPI**, **SQLModel**, **Vite**, **React**, **Tailwind**, and **httpx** — the boring-good stack that let us spend the weekend on the agent, not the plumbing.
