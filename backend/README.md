# backend

The investigative engine behind **Sherlock Homes** — a FastAPI process that hosts the v1 JSON + SSE API under `/api/*`, serves the built React SPA from `frontend/dist/`, bootstraps the MySQL schema via `SQLModel.metadata.create_all` on startup (no Alembic — see [ADR-019](../docs/DECISIONS.md#adr-019-drop-alembic-use-sqlmodelmetadatacreate_all)), and drives every user's `PeriodicUserMatcher` agent loop in the same process.

The scraper lives here too, as a sibling package under [`app/scraper/`](./app/scraper) — a standalone Python process that deep-scrapes wg-gesucht, TUM Living, and Kleinanzeigen into the shared global listing pool.

---

## Orientation

Repo-wide onboarding is in [`../CLAUDE.md`](../CLAUDE.md); developer docs live under [`../docs/`](../docs/README.md). Quick jumps for backend work:

- [`../docs/SETUP.md`](../docs/SETUP.md) — run the backend + frontend locally in ~30 minutes.
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — runtime shape, invariants, request-flow sequence diagrams.
- [`../docs/BACKEND.md`](../docs/BACKEND.md) — file-by-file walkthrough of [`app/wg_agent/`](./app/wg_agent), including the agent loop end-to-end.
- [`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md) — tables, DTOs, the three-layer rule (UI ↔ DTO ↔ domain ↔ row).
- [`../docs/SCRAPER.md`](../docs/SCRAPER.md) — multi-source scraper contract + per-source recon.
- [`../docs/DECISIONS.md`](../docs/DECISIONS.md) — ADR log; add an entry for any new architecture decision.
- [`../docs/_generated/openapi.json`](../docs/_generated/openapi.json) — current OpenAPI spec (regenerate after API changes).

---

## Source layout

```text
backend/
├── app/
│   ├── main.py          lifespan: init_db → resume_user_agents → API + SPA
│   ├── wg_agent/        API router, scorecard evaluator, periodic user matcher,
│   │                    repo, domain models, Google Maps commute + places, SES notifier
│   └── scraper/         standalone scraper process + Source plugins (sources/)
│                        + migrate_multi_source.py one-shot DB migration
├── tests/               pytest suite (parser, repo, evaluator, periodic, commute, places, …)
├── requirements.txt     pinned Python deps
└── Dockerfile           production image built by docker-compose
```

---

## Running it

```bash
# From repo root
cp .env.example .env  # fill DB_*, OPENAI_API_KEY, optional Google Maps + SES

cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Backend (API + SSE + built SPA)
set -a && source ../.env && set +a
venv/bin/uvicorn app.main:app --reload

# Scraper (separate terminal, same env)
venv/bin/python -m app.scraper.main
```

On boot, [`app/main.py`](./app/main.py) calls `db.init_db()` (which runs `SQLModel.metadata.create_all`) and then [`resume_user_agents`](./app/wg_agent/periodic.py) spawns one matcher task for every user with a saved `SearchProfileRow`.

## Tests

```bash
source venv/bin/activate
pytest
```

Tests use in-memory SQLite for isolation ([`tests/conftest.py`](./tests/conftest.py) sets inert `DB_*` placeholders so `db.py` can import cleanly, then individual tests build their own engine and monkey-patch `db_module.engine`).

---

## Core invariants

1. **Only the scraper writes `ListingRow` and `PhotoRow`.**
2. **Only the per-user matcher writes `UserListingRow`.** A `UserListingRow` *is* the user ↔ listing membership record — including vetoes (`score=0.0`, `veto_reason` set).
3. **MySQL is the single source of truth.** Both services call `db.init_db()` on startup; destructive schema changes require a `DROP DATABASE; CREATE DATABASE`.
4. **Three-layer rule.** UI sees DTOs; the agent sees domain models; [`repo.py`](./app/wg_agent/repo.py) is the **only** boundary between domain and rows. Full explanation in [`../docs/README.md#the-three-layer-rule`](../docs/README.md#the-three-layer-rule).
