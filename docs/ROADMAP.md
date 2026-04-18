# Roadmap

What's next for WG Hunter — ranked by demo impact and scoped so a teammate can pick any item up cold. If you see something you want to own, ping the team channel, then edit this file when the PR lands.

Background reading for each item is linked inline. The evaluator pipeline is defined in [ADR-015](./DECISIONS.md) and the implementation in [`evaluator.py`](../backend/app/wg_agent/evaluator.py).

## Queued (clearly scoped, ready to pick up)

### Deterministic pre-filter on search results

**Why:** Right now every new listing id is deep-scraped + geocoded + commute-routed before [`evaluator.hard_filter`](../backend/app/wg_agent/evaluator.py) can veto it. That's wasted work for listings that `anonymous_search` already returns with enough data to reject (e.g. price exceeds `max_rent_eur`, WG size outside range, availability clearly outside the move-in window). Cutting this step saves one HTTP round-trip to wg-gesucht + one Routes API call per rejected listing, which matters when the top results page is dominated by over-budget listings during peak season.

**Shape of the change:**
- Add a new `evaluator.can_search_filter(stub_listing, profile) -> Optional[VetoResult]` that runs **only** the vetoes that don't need the scraped description or coords (price, WG size, move-in date). Keep the full `hard_filter` for the post-scrape pass.
- In [`HuntEngine.run_find_only`](../backend/app/wg_agent/periodic.py), run this cheaper filter on the `new_stubs` list produced by `anonymous_search` and before the `new_listing` action + deep scrape. Log a compact `Skipped pre-filter: <reason>` action so the UI still hears about vetoed stubs.
- Write the skip to `ListingRow` + `ListingScoreRow` (with `score=0.0`, `veto_reason=<reason>`, `components=null`) so the user can still see the rejected listing in the drawer and understand why, mirroring today's post-scrape veto path.

**Touches:** `evaluator.py`, `periodic.py`, `test_evaluator.py` (add pre-filter cases), `test_periodic.py` (assert the skipped stub still persists a score row).

### Surface rejections in the dashboard list

**Why:** `ListingList` currently sorts by score so vetoed listings sink to the bottom unsegregated. Teammates and users can't easily answer "did the agent see listing X and reject it?" vs. "is it still being evaluated?"

**Shape of the change:** in [`ListingList.tsx`](../frontend/src/components/ListingList.tsx), split the sorted list into two sections — "Matched" (no `vetoReason`) and "Rejected" (has `vetoReason`). Rejected cards collapse to a single-line row showing the veto reason in `text-bad` without the score pill.

**Touches:** `ListingList.tsx`, maybe a small tweak to `ListingDrawer.tsx` if we want to keep the breakdown hidden for vetoed listings.

## Later (design work needed first)

### Thumbs up/down on listings → learned composition weights

`COMPONENT_WEIGHTS` in [`evaluator.py`](../backend/app/wg_agent/evaluator.py) is hand-picked. Once users can rate listings in the drawer, we have labeled pairs we can fit weights against. Prerequisites: UI pattern for 👍/👎, a new `ListingFeedbackRow` (plus migration), and a scheduled job that refits weights per-user (or globally) from recent feedback. See ADR-015's "out of scope" note — this one needs the feedback UI before any Python work.

### LLM-as-judge per fuzzy component (with self-consistency)

Instead of one `vibe_fit` LLM call, ask the LLM to rate each soft component (`preference_fit` keywords that can't be resolved deterministically, vibe, district fit) independently with structured output per component. Add a self-consistency pass: two calls at `temperature=0.2`, reject if they disagree by > ε. Roughly 2–3× the LLM cost of today's path, so hold until we see the single-call variance become a real problem during demos.

### Landlord messaging path

[`orchestrator.py`](../backend/app/wg_agent/orchestrator.py) + [`brain.draft_message`](../backend/app/wg_agent/brain.py) + [`brain.classify_reply`](../backend/app/wg_agent/brain.py) + [`MessageRow`](../backend/app/wg_agent/db_models.py) are already in the repo, guarded behind the legacy `HuntRequest` body and exercised by [`test_orchestrator.py`](../backend/tests/test_orchestrator.py). What's missing is the UI ("draft preview", "send", "inbox"), the dry-run / rate-limit toggles from [`WG_GESUCHT.md`](./WG_GESUCHT.md) §5, and Playwright credentials that survive across deploys. Treat this as a full v2 workstream, not a PR.

### AWS Bedrock swap (challenge requirement)

The Reply brief asks for Bedrock. Today we call OpenAI directly via [`brain._client`](../backend/app/wg_agent/brain.py). A minimal swap: accept a `LLM_PROVIDER` env var (`openai` | `bedrock`), add a `brain._bedrock_client()` branch that calls a Bedrock model through `boto3` (code sample in `context/AWS_RESOURCES.md`), and route `score_listing` + `vibe_score` through a thin provider interface. Keep the Pydantic validation so the evaluator can degrade the same way for either provider.

## Done recently

Track what's shipped so reviewers and demo judges can spot-check the history without spelunking git.

- **2026-04-18** — ADR-019: dropped Alembic in favour of `SQLModel.metadata.create_all` on startup; deleted `backend/alembic/` + `backend/alembic.ini` + the `alembic` dependency. See [BACKEND.md "Schema evolution"](./BACKEND.md#schema-evolution).
- **2026-04-18** — ADR-018: split scraper into its own container, global `ListingRow` pool, MySQL-only persistence. See [DECISIONS.md](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only).
- **2026-04-18** — ADR-015: scorecard evaluator with deterministic components + narrow LLM vibe. Replaces the single-LLM-call scoring path. 50 new tests in [`test_evaluator.py`](../backend/tests/test_evaluator.py); component-breakdown bars in [`ListingDrawer`](../frontend/src/components/ListingDrawer.tsx).
- **2026-04-18** — ADR-014: structured DOM selectors + `map_config.markers` coords in `parse_listing_page`; zeroed out the cookie-banner-text-in-prompt regression and cut Geocoding API calls to near-zero for listings that render a map.
- **2026-04-18** — ADR-013: weighted preferences + per-location commute budgets. UI: 1–5 weight slider on each preference tile + per-location ideal-commute input.
- **2026-04-18** — ADR-012: commute-aware scoring via Routes API's `computeRouteMatrix`, LLM-only composition (superseded by ADR-015).
- **2026-04-18** — ADR-010 + ADR-011: structured `main_locations` with client-side Places Autocomplete and server-side Geocoding for listing addresses.

---

*This file supersedes the old `ISSUES_TO_FIX.md` scratchpad. When you ship something from "Queued" or "Later", move it under "Done recently" with a one-line summary + ADR/commit reference.*
