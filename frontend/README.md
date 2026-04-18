# frontend

Vite + React 19 + TypeScript + Tailwind 3 SPA for the WG Hunter wizard, dashboard, listing drawer, and profile page. Built output lands in `dist/` and is served by the FastAPI backend ([`backend/app/main.py`](../backend/app/main.py)).

Repo-wide orientation is in [`../CLAUDE.md`](../CLAUDE.md); developer docs live under [`../docs/`](../docs/README.md). Quick jumps for frontend work:

- [`../docs/SETUP.md`](../docs/SETUP.md) — how to run `npm run dev` alongside the backend.
- [`../docs/FRONTEND.md`](../docs/FRONTEND.md) — file-by-file walkthrough of [`src/`](./src).
- [`../docs/DESIGN.md`](../docs/DESIGN.md) — palette, typography, primitives, enforced rules.
- [`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md) — the DTO shapes the UI consumes.

Source layout:

```text
frontend/
├── src/
│   ├── App.tsx            router: onboarding wizard, dashboard, profile, health
│   ├── main.tsx           createRoot + StrictMode + CSS import
│   ├── index.css          Tailwind layers + :root design tokens
│   ├── types.ts           camelCase TS mirrors of backend DTOs
│   ├── lib/               api.ts (fetch + SSE + toCamel/toSnake) + session.tsx
│   ├── components/        shared UI (ActionLog, ListingList, ListingDrawer, …) + ui/ primitives
│   └── pages/             OnboardingProfile, OnboardingRequirements, OnboardingPreferences, Dashboard, Profile, Health
├── index.html             Vite entry, Geist font load
├── vite.config.ts         envDir: '..' so the repo-root .env feeds VITE_* keys
├── tailwind.config.ts     maps CSS variables to Tailwind aliases
├── Dockerfile             nginx build that serves dist/ + reverse-proxies /api/*
└── nginx.conf             TLS termination + static + /api proxy
```
