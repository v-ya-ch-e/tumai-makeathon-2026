# backend

FastAPI backend for WG Hunter. Hosts the v1 JSON + SSE API under `/api/*`, serves `frontend/dist/` as SPA, runs Alembic migrations on startup, and drives the `PeriodicHunter` agent loop in the same process.

Repo-wide orientation is in [`../CLAUDE.md`](../CLAUDE.md); developer docs live under [`../docs/`](../docs/README.md). Quick jumps for backend work:

- [`../docs/SETUP.md`](../docs/SETUP.md) — how to run the backend + frontend locally.
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — runtime shape and why each piece exists.
- [`../docs/BACKEND.md`](../docs/BACKEND.md) — file-by-file walkthrough of [`app/wg_agent/`](./app/wg_agent).
- [`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md) — tables, DTOs, the three-layer rule.
- [`../docs/AGENT_LOOP.md`](../docs/AGENT_LOOP.md) — one hunt iteration in detail.
- [`../docs/_generated/openapi.json`](../docs/_generated/openapi.json) — current OpenAPI spec (regenerate after API changes).

Source layout:

```text
backend/
├── app/
│   ├── main.py          lifespan: init_db → Alembic upgrade → resume_running_hunts → API + SPA
│   └── wg_agent/        API router, scorecard evaluator, periodic hunter, repo, domain models
├── alembic/versions/    0001_initial … 0007_nearby_places
├── tests/               pytest suite (parser, repo, evaluator, periodic, commute, places, …)
└── requirements.txt     pinned Python deps
```
