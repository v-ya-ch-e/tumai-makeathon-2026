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
frontend/src/components/ui/WeightSlider.tsx  1–5 importance slider (nice / important / must-have)
frontend/src/components/ui/index.ts        Re-export barrel
frontend/src/components/OnboardingShell.tsx  Wizard chrome (progress + nav)
frontend/src/components/ConnectWGDialog.tsx  Modal to save wg credentials (optional)
frontend/src/components/PlaceAutocomplete.tsx  Google Places combobox + removable chips (used in step 2)
frontend/src/components/AppTabs.tsx          Pill-style Dashboard / Profile nav tabs
frontend/src/components/ActionLog.tsx        Monospace-tagged SSE log
frontend/src/components/ListingList.tsx      Ranked cards + selection callback
frontend/src/components/ListingDrawer.tsx    Detail fetch + `Drawer` presentation
frontend/src/pages/OnboardingProfile.tsx       Step 1: demographics
frontend/src/pages/OnboardingRequirements.tsx Step 2: rent, locations, schedule
frontend/src/pages/OnboardingPreferences.tsx  Step 3: preference tiles
frontend/src/pages/Dashboard.tsx               Hunt controls, log, listings, drawer host
frontend/src/pages/Profile.tsx                 Account page: edit age / gender, jump back into the wizard
frontend/src/pages/Health.tsx                Simple connectivity check page
```

## Routes

| Path | Component | Behavior |
| --- | --- | --- |
| `/` | `HomeRedirect` | Waits for `isReady`; sends authenticated users to `/dashboard`, else `/onboarding/profile` |
| `/onboarding/profile` | `OnboardingProfile` | Two tabs: *Create account* (POST `/api/users`, then continue the wizard) and *Sign in* (GET `/api/users/{name}` to verify an existing username, then route to `/`). Both paths store the username in session. |
| `/onboarding/requirements` | `OnboardingRequirements` | Edits numeric/slider/chip requirements, `PUT` search profile |
| `/onboarding/preferences` | `OnboardingPreferences` | Toggles string tags, merges into search profile |
| `/dashboard` | `Dashboard` | Starts/stops hunts, renders log + listings + credential dialog |
| `/profile` | `Profile` | Account settings: edit age/gender (`PUT /api/users/{username}`) and shortcut back into the onboarding wizard steps |
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
- **User mutations** — `createUser` → `POST /api/users`; `updateUser` → `PUT /api/users/{username}` (age / gender only — username is immutable). Both return `UserDTO` normalized to camelCase.
- **`streamHunt`** — Constructs `EventSource` against `/api/hunts/{id}/stream`, JSON-parses each `message` event, normalizes with `toCamel`, returns a disposer that calls `es.close()`.

## Components

- **UI primitives** — See [DESIGN.md](./DESIGN.md) for tokens and primitive rules. Shared via [`components/ui/index.ts`](../frontend/src/components/ui/index.ts).
- **`OnboardingShell`** — Wraps the three wizard pages with `ProgressSteps`, title slot, and sticky footer actions (`Button` variants).
- **`ConnectWGDialog`** — Modal form for email/password or pasted storage JSON; calls credential `PUT`/`DELETE` APIs; consumed from `Dashboard`.
- **`AppTabs`** — Pill-style nav used on both the dashboard header and the profile page to switch between `/dashboard` and `/profile` without leaving the shared card shell.
- **`ActionLog`** — Renders `Action` rows with `font-mono` kind labels and timestamps; fed from SSE + initial `hunt.actions`.
- **`ListingList`** — Scrollable ranked cards (score pill, meta); notifies parent on row activate.
- **`ListingDrawer`** — Fetches `getListingDetail`, shows description/meta inside `Drawer`, external link to wg-gesucht.

## Pages

- **`OnboardingProfile`** — Dual-mode page gated by a *Create account* / *Sign in* tab control. *Create* collects username/age/gender and `POST /api/users`, then navigates to `/onboarding/requirements`. *Sign in* accepts an existing username, verifies it with `getUser` (404 → inline error), then calls `setUsername` and navigates to `/` so `HomeRedirect` routes based on hydrated session. Progress steps only render on the create tab.
- **`OnboardingRequirements`** — Binds sliders, chips, mode select, move-in dates, schedule fields to `UpsertSearchProfileBody`, persists with `putSearchProfile`. Main locations are collected via [`PlaceAutocomplete`](../frontend/src/components/PlaceAutocomplete.tsx) as structured `PlaceLocation[]` (`label`, `placeId`, `lat`, `lng`, optional `maxCommuteMinutes`). Each picked location renders a row with a 5–240 minute ideal-commute input; blank means no budget.
- **`OnboardingPreferences`** — Grouped grid of inline-SVG tiles toggling `PreferenceWeight[]` entries; selected tiles expand to show an inline [`WeightSlider`](../frontend/src/components/ui/WeightSlider.tsx) bound to the 1–5 importance value (default 3). Saves merged profile before routing to `/dashboard`.
- **`Dashboard`** — Loads search profile + optional credentials status, persists last hunt id in `localStorage` (`wg-hunter.hunt-id`), starts hunts (`createHunt`), attaches `streamHunt`, hydrates listings from periodic `getHunt` polling / SSE merges, hosts `ListingDrawer` + `ConnectWGDialog`, maps backend hunt status to UI pill tones. Top-right `AppTabs` links over to `/profile`.
- **`Profile`** — Account page for an already-onboarded user. Hydrates `user` + `SearchProfile` from the session, lets the user edit age / gender via `updateUser`, and exposes `Edit` shortcuts that jump back into `/onboarding/requirements` or `/onboarding/preferences` while keeping the shared `AppTabs` navigation visible.
- **`HealthPage`** — Minimal read-only check (useful when verifying proxy + API reachability during dev).

## Build & dev

- **`npm run dev`** — Vite dev server; [`vite.config.ts`](../frontend/vite.config.ts) proxies `/api` to the FastAPI origin and sets `envDir: '..'` so `VITE_*` values come from the repo-root `.env`.
- **`npm run build`** — `tsc -b && vite build` emits static assets under `frontend/dist/` for [`main.py`](../backend/app/main.py) to serve.
- **`npm test`** — `vitest run` (includes [`lib/api.test.ts`](../frontend/src/lib/api.test.ts)).

## Design tokens

See [DESIGN.md](./DESIGN.md).
