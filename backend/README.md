# backend

FastAPI backend for WG Hunter. Hosts the v1 JSON + SSE API under `/api/*`, serves `frontend/dist/` as SPA, and runs the `PeriodicHunter` agent loop in the same process.

See [`../docs/README.md`](../docs/README.md) for the index. Quick jumps:

- [`../docs/SETUP.md`](../docs/SETUP.md) — how to run it.
- [`../docs/BACKEND.md`](../docs/BACKEND.md) — file-by-file walkthrough.
- [`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md) — tables, DTOs, the three-layer rule.
- [`../docs/AGENT_LOOP.md`](../docs/AGENT_LOOP.md) — one hunt iteration in detail.
- [`../docs/_generated/openapi.json`](../docs/_generated/openapi.json) — current OpenAPI spec.
