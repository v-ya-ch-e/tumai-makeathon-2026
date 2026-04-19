# Design system

Editorial detective skin for **Sherlock Homes** (the WG Hunter product brand): warm fog surfaces, deep ink text, green accent, brass and burgundy as case-file accents. **No blues, no gradients, no emojis** in the product chrome. Tokens live in CSS; Tailwind maps them for utility usage (see [`frontend/tailwind.config.ts`](../frontend/tailwind.config.ts)).

## Brand

### Brand idea

**Sherlock Homes** is the sharp-eyed rental companion that helps people investigate listings, spot hidden trade-offs, and move with confidence. The name blends detective intelligence with the emotional goal of finding a real home.

### Positioning

- **Category:** rental discovery and decision support
- **Audience:** students, young professionals, and city movers overwhelmed by noisy housing platforms
- **Promise:** turn chaotic housing search into a clear case you can solve
- **Differentiator:** Sherlock Homes does not just show listings — it examines them, surfaces clues, and helps users decide faster

### Brand essence

- **Archetype:** the detective guide
- **Mission:** help renters uncover the right home with less stress and more certainty
- **Vision:** make every housing search feel intelligent, human, and actionable
- **Tagline territory:** investigate less, belong sooner

### Personality

Observant · Reassuring · Clever · Crisp · Trustworthy · Warm, never cold or robotic.

### Voice in one sentence

Sherlock Homes sounds like a brilliant friend who notices what others miss and explains it simply.

### Messaging pillars

1. **Clarity from clutter** — reduces noise, highlights the meaningful details, and shows what matters first.
2. **Insight, not overload** — the product feels analytical without becoming technical or dense.
3. **Confidence in the decision** — helps users act with conviction, not just browse endlessly.
4. **Human stakes** — a home is emotional. The brand acknowledges relief, comfort, and belonging alongside logic.

### Example taglines

- Find the clues. Choose your home.
- Smarter searching for better living.
- Every listing tells a story.
- Investigate with confidence.
- From scattered listings to solid leads.

### Brand do's and don'ts

**Do:** lead with confidence and clarity; make intelligence feel helpful, not intimidating; balance rational proof with emotional reassurance; treat the user like an active decision-maker.

**Don't:** sound gimmicky or theatrical; overplay Sherlock Holmes references (no deerstalker hats, no pipes, no "elementary"); use fear-based language about missing out; feel sterile, corporate, or purely transactional.

### Visual direction

Editorial detective energy meets modern property tech: refined, intelligent, warm, and grounded. Subtle keyholes, map pins, magnifying glass geometry, or case-file framing as accents — linework over heavy illustration. Cropped city photography, doors, windows, facades, handwritten notes, and map textures. Use mystery as texture, not gimmick.

### Logo concept

A wordmark or badge that balances deduction and warmth. The logo should feel premium, memorable, and slightly literary rather than playful or cartoonish. Sample prompt: *"Design a brand logo for 'Sherlock Homes,' a modern home-search service that combines detective-like insight with warmth and trust. Refined logo that blends a subtle house shape with an investigative cue such as a magnifying glass, keyhole, or clue marker. Style: editorial, intelligent, premium, minimal, slightly vintage but clearly modern tech. Avoid cartoon detective tropes, deerstalker hats, pipes, or kitsch. Restrained palette of warm ivory, deep ink navy, muted brass, and burgundy accents. Typography sophisticated and readable, serif-led or serif-plus-sans combination."*

## Color tokens

Edit [`frontend/src/index.css`](../frontend/src/index.css) `:root` to re-theme the entire UI (Tailwind `bg-*` / `text-*` aliases read these variables). The live tokens:

```css
:root {
  /* Sherlock Homes brand palette */

  /* Surfaces (fog → parchment) */
  --canvas: #f5f3f0;       /* page background */
  --surface: #ffffff;      /* cards */
  --surface-raised: #f1efea; /* inputs, modals, raised tiles */
  --hairline: rgba(31, 36, 48, 0.1); /* 1px borders */

  /* Text (ink → slate) */
  --ink: #1f2430;          /* primary text */
  --ink-muted: #5b6675;    /* labels, hints */

  /* Accent (verdant detective green — primary buttons, focus rings, active states) */
  --accent: #1f7a4d;
  --accent-muted: #d9eae0; /* hover, selected chip bg */

  /* Signal */
  --good: #1f7a4d;         /* same green — high score, agent running */
  --warn: #b08d57;         /* brass — dry-run skip, pacing */
  --bad:  #8d4250;         /* burgundy — errors, rejected */
}
```

## Typography

Three families load from Google Fonts in [`frontend/index.html`](../frontend/index.html):

- **Cormorant Garamond** (weights 500 / 600 / 700) — `h1`-`h4` headlines, `.brand-wordmark`. Letter-spacing `-0.03em`, line-height `0.95`. Editorial detective feel.
- **Manrope** (weights 400 / 500 / 600 / 700) — body copy at 15px, line-height 1.6. Clean sans for readability.
- **IBM Plex Mono** (weights 400 / 500) — `.section-kicker`, `.data-label`, `.brand-chip`, log labels (`ActionLog`, `ProgressSteps` indices). Uppercase, wide tracking — case-file detail tone.

Tailwind `font-sans` maps to the Manrope stack; `font-mono` maps to IBM Plex Mono. The `@layer base` rule in `index.css` applies Cormorant Garamond to all `<h1>`–`<h4>` elements automatically.

## Spacing & layout

Baseline rhythm: **24px** page gutter, **16px** card padding (`Card` uses `p-4`), **48px** page padding on large breakpoints where pages opt into `px-6 lg:px-12` patterns. Surfaces are flat: **1px** `border-hairline` dividers instead of drop shadows, except the drawer (below).

The shared `.app-shell` utility (`mx-auto max-w-[1180px] px-5 py-6 sm:px-8 lg:px-12`) frames every page; `.page-frame` and `.panel` give the rounded card chrome.

## Shapes

Tailwind extended radii: default **6px** (`rounded`), **`rounded-card` = 12px** for cards, **`rounded-drawer`** (20px) on the leading edge of the drawer sheet. Buttons use the default 6px radius, **not** pills — only chips/status use full rounding.

## Motion

**150ms** `ease-out` on interactive hover/focus color transitions (`Button`, `Chip`). **220ms** `ease-in-out` on the drawer translate + scrim opacity ([`Drawer`](../frontend/src/components/ui/Drawer.tsx) uses `duration-[220ms]`). No other timed animations are part of the system.

## Primitive components

| Primitive | Summary | Used in |
| --- | --- | --- |
| `Button` | Variants **primary** (green fill), **secondary** (transparent with hover surface), **destructive** (burgundy); sizes **md** / **sm**; focus ring uses `ring-accent`. | Onboarding footers, dashboard controls, dialogs |
| `Card` | `rounded-card`, `border-hairline`, `bg-surface`, `p-4`, no shadow. | Dashboard columns, onboarding sections |
| `Input` | `h-10`, hairline border, `bg-surface-raised`, green focus ring. | Profile + credential forms |
| `Textarea` | Same field chrome, `min-h` with vertical resize. | Connect WG dialog (storage JSON) |
| `Select` | Native `<select>` with identical field styling. | Requirements mode / schedule selects |
| `Chip` | Pill toggle (`rounded-full`); selected state uses `border-accent bg-accent-muted`. | Requirements chips, preference toggles |
| `StatusPill` | **6px** dot (`h-1.5 w-1.5`) + label inside a compact pill; tones **idle / running / rescanning / good / warn / bad** choose dot colors (`ink-muted`, `good`, `warn`, `bad`). | Dashboard agent status, score badges |
| `Drawer` | Portal + scrim; panel `shadow-drawer`, `rounded-l-drawer`, **220ms** slide from right; locks body scroll. | `ListingDrawer` |
| `ProgressSteps` | Typographic `01 Profile / 02 Requirements / …` using mono index + sans label weights. | `OnboardingShell` header |

## Rules (enforced in review)

1. Warm neutrals only: no pure `#FFFFFF` page background, no cool gray fills (the cards do use `#ffffff` against the warmer canvas — that contrast is intentional).
2. **One accent** (`--accent`): reserve the green for primary buttons and focus rings; secondary actions are quiet text buttons that pick up `hover:bg-surface`.
3. Baseline rhythm: **24px** gutters, **16px** card padding, **48px** page padding on desktop where applied.
4. Hairlines, not shadows: cards use `1px` `border-hairline`; only drawer/modal surfaces use the single soft shadow token (`shadow-drawer`).
5. Radii: **6px** default, **12px** cards, **20px** drawer corners; no pill **buttons** — pills are for `Chip` / `StatusPill` only.
6. Listing cards prioritize imagery when present (first photo full-bleed in list UI).
7. Motion: **150ms** hover transitions, **220ms** drawer transition — avoid extra animation.

## Copywriting

Every line should make the user feel two things at once: **"this service is sharp"** and **"this service understands what home means."**

### Tone attributes

- **Observant** — notice meaningful details and name them plainly.
- **Calm** — reduce stress. Write as if the user is already overwhelmed.
- **Precise** — prefer exact language over hype.
- **Encouraging** — help people move forward with confidence.

Voice principles: clear before clever; insightful without jargon; reassuring without sounding soft; premium without sounding exclusive; human without sounding chatty.

### Writing rules

1. **Lead with the finding.** Start with the most useful conclusion, then explain the evidence.
   - Good: "This listing looks promising for your commute."
   - Better follow-up: "It keeps your travel time under 25 minutes and is close to your most-used tram line."
2. **Use investigative language sparingly.** Words like "clue," "signal," "case," or "evidence" can reinforce the brand, but they should appear as light seasoning, not in every sentence.
   - Good: "We found a few strong signals in this listing."
   - Avoid: "Case closed. Another clue cracked in your home hunt mystery."
3. **Translate analysis into decisions.** Don't stop at description. Explain what the information means for the user.
   - Weak: "The flat is 17 square meters."
   - Strong: "At 17 square meters, this flat works best if budget matters more than extra space."
4. **Respect the emotional context.** Housing is stressful. Acknowledge uncertainty without amplifying it.
   - Good: "This option has trade-offs, but the location could make it worth a closer look."
   - Avoid: "Act fast or you could lose this one."
5. **Keep it concise.** Short sentences build trust. Cut filler, adverbs, and internal jargon.
6. **Sound expert, not superior.** Guide the user. Never talk down to them.
   - Good: "Here is what stands out."
   - Avoid: "Obviously, this is the better choice."

### Messaging formula

A simple structure for most product copy:

1. **Observation** — what we found.
2. **Meaning** — why it matters.
3. **Next move** — what the user can do now.

Example: *"This listing scores well on price and commute. That makes it a strong practical option, even if the photos are limited. Save it for a closer review."*

### Vocabulary

**Lean toward:** find · uncover · spot · compare · examine · shortlist · match · signal · fit · confidence · home · next step.

**Avoid:** hack · crush your search · game-changing · secret · no-brainer · insane deal · case closed · elementary · cheap · luxury living.

### UX copy guidelines

- **Headlines** — direct and benefit-led; aim for 3–8 words; favor clarity over puns. Examples: "Find homes with more confidence." / "See what really matters." / "Strong matches, clearly explained."
- **Buttons** — action verbs, explicit outcomes. Examples: "Review matches" / "Save this home" / "Compare details" / "See why it scored well."
- **Empty states** — calm and constructive; offer a next step. Example: *"No strong matches yet. We are still checking new listings and will surface the best leads here."*
- **Alerts and warnings** — be specific, explain impact, suggest what to do next. Example: *"This listing is missing commute data right now. You can still save it, and we will update the analysis when more details arrive."*

### Brand cadence

Use brand-flavored language **most heavily** in: landing page headlines, hero statements, onboarding moments, feature naming.

Use it **lightly** in: dashboards, recommendations, transactional messages, system feedback.

### Copy checklist

Before publishing, ask:

- Is the meaning clear in one quick read?
- Does it sound intelligent but still warm?
- Did we explain why the detail matters?
- Did we avoid gimmicks and overdone Sherlock references?
- Does the line help the user decide or feel more confident?

**One-line test:** if a sentence would sound natural from a perceptive, trustworthy advisor helping a friend find a place to live, it fits Sherlock Homes.

## What we did NOT do

- No packaged icon library (preference tiles ship **inline SVG paths** in [`OnboardingPreferences.tsx`](../frontend/src/pages/OnboardingPreferences.tsx)).
- No animation framework (no Framer Motion / GSAP).
- No external design-system package beyond Tailwind itself.
- No Tailwind plugins in [`tailwind.config.ts`](../frontend/tailwind.config.ts) (`plugins: []`).
