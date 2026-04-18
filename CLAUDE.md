# CLAUDE.md

Single-page orientation for coding agents picking up this repo. Read the "Documentation map" below, then follow the "Read in order" list before any non-trivial change.

## What this repo is

**TUM.ai Makeathon 2026** submission for Reply's [*Campus Co-Pilot Suite*](context/CHALLENGE_BRIEF.md) challenge. The active workstream is **WG Hunter**: an autonomous `wg-gesucht.de` room-hunting agent that searches, scrapes, scores, and surfaces listings through a live React dashboard.

- **Backend:** FastAPI (Python 3.11+) under [`backend/`](./backend/), entrypoint [`backend/app/main.py`](./backend/app/main.py). One process hosts JSON API, SSE stream, Alembic-managed SQLite, the built SPA, and the `PeriodicHunter` agent loop.
- **Frontend:** Vite + React 19 + TypeScript + Tailwind 3 under [`frontend/`](./frontend/), entrypoint [`frontend/src/App.tsx`](./frontend/src/App.tsx). Built output (`frontend/dist/`) is served by FastAPI.
- **External services:** `wg-gesucht.de` (httpx scrape, no API), OpenAI (narrow `vibe_score` LLM call in the scorecard evaluator), Google Maps Platform (browser Places Autocomplete + backend Geocoding / Distance Matrix / Places (New)).
- **Deploy:** Docker Compose on AWS EC2, CI via GitHub Actions. See [`DEPLOYMENT.md`](./DEPLOYMENT.md) and [`CI-CONFIGURATION.md`](./CI-CONFIGURATION.md).

```text
┌──────────────┐          ┌──────────────────────────┐          ┌────────────────┐
│ React SPA    │ ──fetch──▶ FastAPI (/api + SPA)     │ ──httpx──▶ wg-gesucht.de  │
│ (Vite, TS)   │ ◀── SSE ──│ HuntEngine → evaluator   │ ──httpx──▶ OpenAI (vibe)  │
└──────────────┘          │ SQLite (+ Alembic)       │ ──httpx──▶ Google Maps    │
                          └──────────────────────────┘
```

Full runtime diagram: [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md).

## Documentation map

```text
.
├── README.md ─────────────── quick-start, env table, deploy summary
├── CLAUDE.md (this file) ── agent orientation + doc tree
├── AGENTS.md ─────────────── pointer to CLAUDE.md + docs/README.md
├── DEPLOYMENT.md ────────── AWS EC2 + Docker walkthrough
├── CI-CONFIGURATION.md ──── GitHub Actions → EC2 pipeline
├── .env.example ─────────── every supported environment variable
│
├── docs/                    developer docs (single source of truth)
│   ├── README.md ─────────── index + read-in-order + three-layer rule
│   ├── SETUP.md ──────────── clone-to-running in ~30 min + first-contribution recipes
│   ├── ARCHITECTURE.md ──── runtime shape, request flow, why each piece exists
│   ├── DATA_MODEL.md ─────── every table + DTO + the three-layer rule
│   ├── BACKEND.md ────────── file-by-file tour of backend/app/wg_agent/
│   ├── FRONTEND.md ───────── file-by-file tour of frontend/src/
│   ├── AGENT_LOOP.md ────── one HuntEngine.run_find_only pass end-to-end
│   ├── DESIGN.md ─────────── palette, typography, primitives, enforced rules
│   ├── WG_GESUCHT.md ────── live recon notes + DOM selectors we depend on
│   ├── DECISIONS.md ─────── ADR log (ADR-001 … ADR-017)
│   ├── ROADMAP.md ────────── queued / later / done-recently
│   └── _generated/openapi.json   committed OpenAPI spec
│
├── context/                 hackathon background (read before touching challenge scope)
│   ├── CHALLENGE_OVERVIEW.md  one-page orientation: sponsor, room 1100, deadlines
│   ├── CHALLENGE_BRIEF.md     primary Reply challenge text (from DataReply/makeathon)
│   ├── TUM_SYSTEMS.md         API + scraping notes for every TUM system the agent may touch
│   └── CODE_EXAMPLES.md       copy-paste-ready Python + TypeScript snippets
│
├── backend/                 FastAPI app
│   ├── README.md ─────────── pointer back to docs/
│   ├── app/main.py ───────── lifespan: init_db → Alembic upgrade → resume_running_hunts → API
│   ├── app/wg_agent/ ────── agent package (see docs/BACKEND.md for file-by-file)
│   ├── alembic/versions/ ── 0001_initial … 0007_nearby_places (see docs/DATA_MODEL.md)
│   └── tests/ ────────────── pytest suite (parser, repo, evaluator, periodic, commute, …)
│
└── frontend/                Vite + React SPA
    ├── README.md ─────────── pointer back to docs/
    ├── src/App.tsx ───────── router: onboarding wizard, dashboard, profile, health
    ├── src/pages/ ────────── screens (see docs/FRONTEND.md)
    ├── src/components/ ──── shared components + ui/ primitives (see docs/DESIGN.md)
    └── src/lib/api.ts ────── fetch + SSE + toCamel/toSnake
```

## Read in order (agent onboarding, ~20–90 min)

1. [`docs/README.md`](./docs/README.md) — doc index, what the agent does, stack-at-a-glance, three-layer rule.
2. [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — runtime shape of the WG Hunter stack.
3. [`docs/DATA_MODEL.md`](./docs/DATA_MODEL.md) — the **three-layer rule** (UI ↔ DTO ↔ domain ↔ row) that every API change must respect.
4. [`docs/BACKEND.md`](./docs/BACKEND.md) and [`docs/FRONTEND.md`](./docs/FRONTEND.md) — file maps.
5. [`docs/AGENT_LOOP.md`](./docs/AGENT_LOOP.md) — one hunt iteration in detail.
6. [`docs/DECISIONS.md`](./docs/DECISIONS.md) — ADR log; add an entry for any new architecture decision.
7. [`docs/ROADMAP.md`](./docs/ROADMAP.md) — queued work and explicit non-goals.

When touching the agent scoring surface, also skim [`docs/WG_GESUCHT.md`](./docs/WG_GESUCHT.md) for the DOM selectors and rate-limit notes we depend on.

## Behavioral guidelines

Guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
