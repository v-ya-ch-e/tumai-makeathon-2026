# Frontend

Vite-built React SPA for onboarding, dashboard controls, SSE-fed action log, and ranked listing exploration. Styling is Tailwind on top of CSS variables documented in [DESIGN.md](./DESIGN.md).

## Stack

- **Vite** 8 (`vite` dev server, `vite build` to `dist/`)
- **React** 19 + **React DOM** 19
- **React Router** 7 (`BrowserRouter`, `Routes`, `Navigate`)
- **TypeScript** 6 (`tsc -b` before production build)
- **Tailwind CSS** 3 (theme extension maps CSS variables)
- **clsx** for conditional class names
- **`@vis.gl/react-google-maps`** — Google Maps JS SDK loader used for Places Autocomplete in onboarding (`APIProvider` in [`App.tsx`](../frontend/src/App.tsx), hooks in [`PlaceAutocomplete`](../frontend/src/components/PlaceAutocomplete.tsx))
- No global state library: **React Context** (`SessionProvider`) plus local component state

## File map

```text
frontend/src/App.tsx                 Route table + `HomeRedirect` shell
frontend/src/main.tsx              `createRoot`, `StrictMode`, CSS import
frontend/src/index.css             Tailwind layers + `:root` design tokens
frontend/src/types.ts              CamelCase TS mirrors of JSON DTOs
frontend/src/vite-env.d.ts         Vite client typings
frontend/src/lib/api.ts            Fetch helpers, SSE wrapper, `toCamel` / `toSnake`, `ApiError`
frontend/src/lib/api.test.ts       Vitest coverage for parsing helpers / client behavior
frontend/src/lib/session.tsx       `SessionProvider`, localStorage-backed username hydration
frontend/src/components/ui/Button.tsx      Primary / secondary / destructive buttons (md/sm)
frontend/src/components/ui/Card.tsx        Hairline-bordered surface panel
frontend/src/components/ui/Chip.tsx        Pill toggle for preference chips
frontend/src/components/ui/Drawer.tsx      Right-slide portal drawer + scrim
frontend/src/components/ui/Input.tsx       `Input`, `Textarea`, `Select` primitives
frontend/src/components/ui/ProgressSteps.tsx  Typographic onboarding progress
frontend/src/components/ui/StatusPill.tsx  Dot + label status badge
frontend/src/components/ui/index.ts        Re-export barrel
frontend/src/components/OnboardingShell.tsx  Wizard chrome (progress + nav)
frontend/src/components/ConnectWGDialog.tsx  Modal to save wg credentials (optional)
frontend/src/components/PlaceAutocomplete.tsx  Google Places combobox + removable chips (used in step 2)
frontend/src/components/ActionLog.tsx        Monospace-tagged SSE log
frontend/src/components/ListingList.tsx      Ranked cards + selection callback
frontend/src/components/ListingDrawer.tsx    Detail fetch + `Drawer` presentation
frontend/src/pages/OnboardingProfile.tsx       Step 1: demographics
frontend/src/pages/OnboardingRequirements.tsx Step 2: rent, locations, schedule
frontend/src/pages/OnboardingPreferences.tsx  Step 3: preference tiles
frontend/src/pages/Dashboard.tsx               Hunt controls, log, listings, drawer host
frontend/src/pages/Health.tsx                Simple connectivity check page
```

## Routes

| Path | Component | Behavior |
| --- | --- | --- |
| `/` | `HomeRedirect` | Waits for `isReady`; sends authenticated users to `/dashboard`, else `/onboarding/profile` |
| `/onboarding/profile` | `OnboardingProfile` | Creates/fetches `UserProfile`, stores username in session |
| `/onboarding/requirements` | `OnboardingRequirements` | Edits numeric/slider/chip requirements, `PUT` search profile |
| `/onboarding/preferences` | `OnboardingPreferences` | Toggles string tags, merges into search profile |
| `/dashboard` | `Dashboard` | Starts/stops hunts, renders log + listings + credential dialog |
| `/health` | `HealthPage` | Lightweight sanity page for local debugging |

## Session

[`SessionProvider`](../frontend/src/lib/session.tsx) keeps `username`, hydrated `user`, `isReady`, and `refreshUser`.

- **Storage key:** `wg-hunter.username` in `localStorage`.
- **Hydration:** On mount, `refreshUser` reads the key; if present, calls `getUser` and clears stale keys when the API returns 404.
- **`setUsername`:** Writes or removes the key, updates local state, then `refreshUser` to pull the full `User` DTO.
- **`isReady`:** Gates `HomeRedirect` so `/` does not flash the wrong destination before the first `getUser` completes.

## API client (`lib/api.ts`)

- **`toCamel` / `toSnake`** — Deep key rewriting between snake_case JSON (backend) and camelCase TS objects.
- **`ApiError`** — Carries HTTP status plus parsed body for non-2xx responses.
- **`requestJson`** — Shared `fetch` wrapper: applies `toCamel` on JSON successes; throws `ApiError` on failures.
- **404 policy** — `getUser`, `getSearchProfile`, `getHunt`, and `getListingDetail` return `null` on 404 instead of throwing (safe idempotent reads). Mutating calls still throw `ApiError` on errors.
- **`streamHunt`** — Constructs `EventSource` against `/api/hunts/{id}/stream`, JSON-parses each `message` event, normalizes with `toCamel`, returns a disposer that calls `es.close()`.

## Components

- **UI primitives** — See [DESIGN.md](./DESIGN.md) for tokens and primitive rules. Shared via [`components/ui/index.ts`](../frontend/src/components/ui/index.ts).
- **`OnboardingShell`** — Wraps the three wizard pages with `ProgressSteps`, title slot, and sticky footer actions (`Button` variants).
- **`ConnectWGDialog`** — Modal form for email/password or pasted storage JSON; calls credential `PUT`/`DELETE` APIs; consumed from `Dashboard`.
- **`ActionLog`** — Renders `Action` rows with `font-mono` kind labels and timestamps; fed from SSE + initial `hunt.actions`.
- **`ListingList`** — Scrollable ranked cards (score pill, meta); notifies parent on row activate.
- **`ListingDrawer`** — Fetches `getListingDetail`, shows description/meta inside `Drawer`, external link to wg-gesucht.

## Pages

- **`OnboardingProfile`** — Collects username/age/gender, `POST /api/users` on first save, `setUsername`, navigates forward when `user` exists.
- **`OnboardingRequirements`** — Binds sliders, chips, mode select, move-in dates, schedule fields to `UpsertSearchProfileBody`, persists with `putSearchProfile`. Main locations are collected via [`PlaceAutocomplete`](../frontend/src/components/PlaceAutocomplete.tsx) as structured `PlaceLocation[]` (`label`, `placeId`, `lat`, `lng`).
- **`OnboardingPreferences`** — Grid of inline-SVG tiles toggling `preferences` string tags; saves merged profile before routing to `/dashboard`.
- **`Dashboard`** — Loads search profile + optional credentials status, persists last hunt id in `localStorage` (`wg-hunter.hunt-id`), starts hunts (`createHunt`), attaches `streamHunt`, hydrates listings from periodic `getHunt` polling / SSE merges, hosts `ListingDrawer` + `ConnectWGDialog`, maps backend hunt status to UI pill tones.
- **`HealthPage`** — Minimal read-only check (useful when verifying proxy + API reachability during dev).

## Build & dev

- **`npm run dev`** — Vite dev server; [`vite.config.ts`](../frontend/vite.config.ts) proxies `/api` to the FastAPI origin and sets `envDir: '..'` so `VITE_*` values come from the repo-root `.env`.
- **`npm run build`** — `tsc -b && vite build` emits static assets under `frontend/dist/` for [`main.py`](../backend/app/main.py) to serve.
- **`npm test`** — `vitest run` (includes [`lib/api.test.ts`](../frontend/src/lib/api.test.ts)).

## Design tokens

See [DESIGN.md](./DESIGN.md).
