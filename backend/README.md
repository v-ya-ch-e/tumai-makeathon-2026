# backend

FastAPI backend for WG Hunter. Hosts the v1 JSON + SSE API under `/api/*`, serves `frontend/dist/` as SPA, bootstraps the schema via `SQLModel.metadata.create_all` on startup (no Alembic — see [ADR-019](../docs/DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)), and drives the per-user `PeriodicUserMatcher` agent loops in the same process.

Repo-wide orientation is in [`../CLAUDE.md`](../CLAUDE.md); developer docs live under [`../docs/`](../docs/README.md). Quick jumps for backend work:

- [`../docs/SETUP.md`](../docs/SETUP.md) — how to run the backend + frontend locally.
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — runtime shape and why each piece exists.
- [`../docs/BACKEND.md`](../docs/BACKEND.md) — file-by-file walkthrough of [`app/wg_agent/`](./app/wg_agent), including the agent loop.
- [`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md) — tables, DTOs, the three-layer rule.
- [`../docs/SCRAPER.md`](../docs/SCRAPER.md) — multi-source scraper contract + per-source recon (wg-gesucht, TUM Living, Kleinanzeigen).
- [`../docs/_generated/openapi.json`](../docs/_generated/openapi.json) — current OpenAPI spec (regenerate after API changes).

Source layout:

```text
backend/
├── app/
│   ├── main.py          lifespan: init_db → resume_user_agents → API + SPA
│   ├── wg_agent/        API router, scorecard evaluator, periodic user matcher, repo, domain models
│   └── scraper/         standalone scraper container + Source plugins (sources/) + migrate_multi_source.py
├── tests/               pytest suite (parser, repo, evaluator, periodic, commute, places, …)
└── requirements.txt     pinned Python deps
```
