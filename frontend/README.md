# frontend

The case-file on screen — a **Vite + React 19 + TypeScript + Tailwind 3** SPA for the **Sherlock Homes** onboarding wizard, live dashboard, listing drawer, and profile page. Built output lands in `dist/` and is served by the FastAPI backend ([`../backend/app/main.py`](../backend/app/main.py)), so one process covers API, SSE, and static assets.

Desktop-first, editorially skinned: warm fog surfaces, deep ink text, brass and burgundy case-file accents. **No blues, no gradients, no emojis in product chrome** — see [`../docs/DESIGN.md`](../docs/DESIGN.md) for the full brand.

---

## Orientation

Repo-wide onboarding is in [`../CLAUDE.md`](../CLAUDE.md); developer docs live under [`../docs/`](../docs/README.md). Quick jumps for frontend work:

- [`../docs/SETUP.md`](../docs/SETUP.md) — run `npm run dev` alongside the backend.
- [`../docs/FRONTEND.md`](../docs/FRONTEND.md) — file-by-file walkthrough of [`src/`](./src).
- [`../docs/DESIGN.md`](../docs/DESIGN.md) — palette, typography, primitives, enforced rules.
- [`../docs/DATA_MODEL.md`](../docs/DATA_MODEL.md) — the DTO shapes the UI consumes.
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — where SSE events come from and how the dashboard consumes them.

---

## Source layout

```text
frontend/
├── src/
│   ├── App.tsx            router: onboarding wizard, dashboard, profile, health
│   ├── main.tsx           createRoot + StrictMode + CSS import
│   ├── index.css          Tailwind layers + :root design tokens
│   ├── types.ts           camelCase TS mirrors of backend DTOs
│   ├── lib/               api.ts (fetch + SSE + toCamel/toSnake) + session.tsx
│   ├── components/        shared UI (ActionLog, ListingList, ListingDrawer, …) + ui/ primitives
│   └── pages/             OnboardingProfile, OnboardingRequirements, OnboardingPreferences,
│                          Dashboard, Profile, Timeline, Health
├── index.html             Vite entry, Geist font load
├── vite.config.ts         envDir: '..' — repo-root .env feeds VITE_* keys to the bundle
├── tailwind.config.ts     maps CSS variables to Tailwind aliases
├── postcss.config.js      Tailwind + Autoprefixer pipeline
├── tsconfig.json          strict TS config
├── Dockerfile             nginx build that serves dist/ + reverse-proxies /api/*
└── nginx.conf             TLS termination + static + /api proxy
```

---

## Running it

```bash
# Install once (from this folder)
npm install

# Production build — writes dist/; the backend serves it at /
npm run build

# Dev loop — Vite on :5173 with /api/* proxied to the backend on :8000
npm run dev

# Tests
npm test
```

The dev server reads `VITE_GOOGLE_MAPS_API_KEY` from the repo-root `.env` (via `envDir: '..'` in [`vite.config.ts`](./vite.config.ts)). No per-folder `.env` needed.

---

## What lives here

- **Onboarding wizard** — three-step profile / requirements / preferences flow with in-browser Google Places Autocomplete for main locations, weighted preferences, and commute-mode selection.
- **Dashboard** — SSE-fed live action log, ranked listing cards, filter bar, and a drawer that breaks the final score down into its component curves + commute times per mode + nearby-place distances.
- **Profile** — lets the user re-open their saved search profile and edit it; saving re-arms the backend matcher loop.
- **Types** — [`src/types.ts`](./src/types.ts) is the **only** place camelCase DTOs are defined for the UI side. `lib/api.ts` converts snake_case JSON from the backend into these automatically via `toCamel`.

The UI never imports SQLModel types. It sees only DTOs as JSON. See the [three-layer rule](../docs/README.md#the-three-layer-rule).
