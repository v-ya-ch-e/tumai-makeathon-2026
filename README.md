# TUM.ai Makeathon 2026 · Campus Co-Pilot

Our submission for Reply's **The Campus Co-Pilot Suite** challenge: autonomous agents that take concrete actions across the university's fragmented digital ecosystem so that students can stop acting as human APIs.

The active workstream is **WG Hunter** — a fully autonomous `wg-gesucht.de` room hunt that searches, ranks, and surfaces listings via a live React dashboard.

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
