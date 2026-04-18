# Design system

Warm editorial skin for WG Hunter: cream surfaces, terracotta accent, sage/amber/rust semantics. **No blues, no gradients, no emojis** in the product chrome. Tokens live in CSS; Tailwind maps them for utility usage (see [`frontend/tailwind.config.ts`](../frontend/tailwind.config.ts)).

## Color tokens

Edit [`frontend/src/index.css`](../frontend/src/index.css) `:root` to re-theme the entire UI (Tailwind `bg-*` / `text-*` aliases read these variables).

```css
:root {
  /* Surfaces */
  --canvas: #F5F0E6;       /* page background */
  --surface: #FAF6EE;      /* cards */
  --surface-raised: #FFFCF6; /* inputs, modals */
  --hairline: #E6DECF;     /* 1px borders */

  /* Text */
  --ink: #2B2623;          /* primary text, near-black warm */
  --ink-muted: #7A6E60;    /* labels, hints */

  /* Accent (used sparingly — primary buttons, focus rings, active states) */
  --accent: #8A5A3B;       /* terracotta / cognac */
  --accent-muted: #C8A586; /* hover, selected chip bg */

  /* Signal */
  --good: #6B8E5A;         /* sage — high score, agent running */
  --warn: #C08A3E;         /* amber — dry-run skip, pacing */
  --bad:  #A85C4A;         /* rust — errors, rejected */
}
```

## Typography

**Geist** sans (weights **400 / 500 / 600 / 700**) and **Geist Mono** (**400 / 500**) load from Google Fonts in [`frontend/index.html`](../frontend/index.html). Global `html, body` in `index.css` sets `font-family: Geist, ui-sans-serif, system-ui, sans-serif` at **15px** body size. Tailwind `font-sans` / `font-mono` map to the same stacks in `tailwind.config.ts`; mono is used for compact log labels (`ActionLog`, `ProgressSteps` indices).

## Spacing & layout

Baseline rhythm from the product plan: **24px** page gutter, **16px** card padding (`Card` uses `p-4`), **48px** page padding on large breakpoints where pages opt into `px-6 lg:px-12` patterns. Surfaces are flat: **1px** `border-hairline` dividers instead of drop shadows, except the drawer (below).

## Shapes

Tailwind extended radii: default **6px** (`rounded`), **`rounded-card` = 12px** for cards, **`rounded-drawer`** (20px) on the leading edge of the drawer sheet. Buttons use the default 6px radius, **not** pills—only chips/status use full rounding.

## Motion

**150ms** `ease-out` on interactive hover/focus color transitions (`Button`, `Chip`). **220ms** `ease-in-out` on the drawer translate + scrim opacity ([`Drawer`](../frontend/src/components/ui/Drawer.tsx) uses `duration-[220ms]`). No other timed animations are part of the system.

## Primitive components

| Primitive | Summary | Used in |
| --- | --- | --- |
| `Button` | Variants **primary** (terracotta fill), **secondary** (transparent with hover surface), **destructive** (rust); sizes **md** / **sm**; focus ring uses `ring-accent`. | Onboarding footers, dashboard controls, dialogs |
| `Card` | `rounded-card`, `border-hairline`, `bg-surface`, `p-4`, no shadow. | Dashboard columns, onboarding sections |
| `Input` | `h-10`, hairline border, `bg-surface-raised`, terracotta focus ring. | Profile + credential forms |
| `Textarea` | Same field chrome, `min-h` with vertical resize. | Connect WG dialog (storage JSON) |
| `Select` | Native `<select>` with identical field styling. | Requirements mode / schedule selects |
| `Chip` | Pill toggle (`rounded-full`); selected state uses `border-accent bg-accent-muted`. | Requirements chips, preference toggles |
| `StatusPill` | **6px** dot (`h-1.5 w-1.5`) + label inside a compact pill; tones **idle / running / rescanning / good / warn / bad** choose dot colors (`ink-muted`, `good`, `warn`, `bad`). | Dashboard agent status, score badges |
| `Drawer` | Portal + scrim; panel `shadow-drawer`, `rounded-l-drawer`, **220ms** slide from right; locks body scroll. | `ListingDrawer` |
| `ProgressSteps` | Typographic `01 Profile / 02 Requirements / …` using mono index + sans label weights. | `OnboardingShell` header |

## Rules (enforced in review)

1. Warm neutrals only: no pure `#FFFFFF` / `#000000`, no cool gray fills.
2. **One accent** (`--accent`): reserve for primary buttons and focus rings; secondary actions are quiet text buttons that pick up `hover:bg-surface`.
3. Baseline rhythm: **24px** gutters, **16px** card padding, **48px** page padding on desktop where applied.
4. Hairlines, not shadows: cards use `1px` `border-hairline`; only drawer/modal surfaces use the single soft shadow token (`shadow-drawer`).
5. Radii: **6px** default, **12px** cards, **20px** drawer corners; no pill **buttons**—pills are for `Chip` / `StatusPill` only.
6. Listing cards prioritize imagery when present (first photo full-bleed in list UI).
7. Motion: **150ms** hover transitions, **220ms** drawer transition—avoid extra animation.

## What we did NOT do

- No packaged icon library (preference tiles ship **inline SVG paths** in [`OnboardingPreferences.tsx`](../frontend/src/pages/OnboardingPreferences.tsx)).
- No animation framework (no Framer Motion / GSAP).
- No external design-system package beyond Tailwind itself.
- No Tailwind plugins in [`tailwind.config.ts`](../frontend/tailwind.config.ts) (`plugins: []`).
