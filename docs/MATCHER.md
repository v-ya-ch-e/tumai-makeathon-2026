# Matching engine

This document is the source of truth for how a candidate listing is turned
into a 0–1 match score for one user. It supersedes the scattered notes that
used to live in `evaluator.py` docstrings.

The engine is plugged into the existing pipeline at the boundaries set by
[`docs/ARCHITECTURE.md`](./ARCHITECTURE.md) — the per-user matcher loop
([`backend/app/wg_agent/periodic.py`](../backend/app/wg_agent/periodic.py))
calls `evaluator.evaluate(listing, profile, travel_times, nearby_places)`
once per `(user, listing)` pair, persists the resulting `ComponentScore`
list via `repo.save_user_match`, and the dashboard drawer renders one bar
per component.

> Notable v2 design choices vs the legacy ADR-015 scorecard:
> `quality_fit` is no longer double-counted (split match-vs-quality
> blend), all profile fields use the canonical `max_rent_eur` /
> `min_rent_eur` names, the size curve is monotone, all distance bands
> are pinned, threshold multipliers are named, the commute aggregator
> uses `0.7·min + 0.3·mean` across anchors, and evidence carries
> `[google]/[listing]/[llm]/[engine]` provenance tags.

---

## 1. Design goals

1. **Be honest about uncertainty.** Missing data is a first-class state,
   not a free pass. Each axis decides whether "unknown" should be neutral,
   penalised, or vetoed.
2. **Hard filters before soft scoring.** A listing the user clearly cannot
   accept (over real budget, far from any anchor, missing a hard
   requirement) never burns LLM tokens.
3. **Real-world distance over keyword scans.** Whenever Google Maps can
   answer a question, prefer it over scanning the listing description for
   substrings.
4. **The LLM judges what only an LLM can judge.** Vibe, flatmate fit,
   prose-only signals. Numeric fit stays in deterministic Python.
5. **Two scores, not one.** Every listing gets both a `match_score`
   (how close it is to the user's stated requirements) and a
   `quality_score` (an absolute "is this a good Munich rental?" signal,
   independent of the user). The product surfaces match; the absolute
   signal lets us de-rank obvious dumps even when they technically fit.
6. **Explainable.** Every component has machine-readable evidence the UI
   can render verbatim, so the score is debuggable end-user-side.

---

## 2. Inputs

### 2.1 From `SearchProfile` (canonical field names)

| field | role |
|-------|------|
| `max_rent_eur: int (≥100)` | budget. Drives `price_fit`'s falloff and the 1.5× hard veto. **Canonical.** |
| `min_rent_eur: int (≥0)` | optional sanity floor. Drives the "suspiciously cheap" signal in `price_fit` (no penalty, just evidence). |
| `min_size_m2`, `max_size_m2` | size band for `size_fit`. |
| `min_wg_size`, `max_wg_size` | WG-size band for `wg_fit`. |
| `main_locations: list[PlaceLocation]` (each `lat/lng`, `place_id`, optional `max_commute_minutes`) | commute anchors. |
| `has_car`, `has_bike` | which travel modes get queried in `commute.modes_for(profile)`. |
| `mode` (`wg` / `flat` / `both`) | candidate filter at the SQL layer (already done by `repo`). |
| `move_in_from`, `move_in_until` | move-in window. |
| `preferences: list[PreferenceWeight]` (each `{key, weight∈1..5}`) | the soft-preference tiles from the wizard. |
| `notes: str` (free text) | LLM vibe input. |
| `avoid_districts: list[str]` | hard veto on case+umlaut-folded match. |
| `preferred_districts: list[str]` | LLM vibe context only. |

The legacy `price_min_eur` / `price_max_eur` columns on
`SearchProfileRow` are kept for storage backward-compat but always
mirror `min_rent_eur` / `max_rent_eur` after this change. The engine
reads only the canonical names; `repo.get_search_profile` does the
mirroring.

### 2.2 From `Listing` (already populated by the scraper + enricher)

`price_eur`, `size_m2`, `wg_size`, `district`, `address`, `lat`, `lng`,
`available_from`, `available_to`, `description`, `furnished`,
`pets_allowed`, `smoking_ok`, `languages`, `kind`, `cover_photo_url`,
`photo_urls`, `posted_at`.

**`Listing.price_eur` is contractually total monthly rent (Warmmiete /
Gesamtmiete)** — utilities included. The scraper enforces this when it
can; the enricher fills it from the description when both Kaltmiete and
Nebenkosten are stated. When the source only quotes Kaltmiete, the
scraper sets `price_eur = round(kalt * 1.20)` and adds
`"price_estimated_warm"` to a new `Listing.price_basis` field
(`"warm" | "kalt_uplift" | "unknown"`). `price_fit` uses `price_basis`
as evidence; it does **not** apply a second uplift.

### 2.3 Computed at match time

- `travel_times: dict[(place_id, mode), seconds]` — pre-fetched by
  `commute.travel_times` (Google Distance Matrix).
- `nearby_places: dict[pref_key, NearbyPlace]` — pre-fetched by
  `places.nearby_places` (Google Places).
- `market_context: MarketContext | None` — optional district-level
  percentile cache from the existing global `ListingRow` pool; see §5.1.

---

## 3. Preference catalogue (single source of truth)

Four resolver families. Each preference key declares which family handles
it. Anything else is a bug in the wizard. The implementation enforces
this with a `RESOLVER_FAMILY` table that the wizard's hashed preference
list is validated against in `dto.upsert_body_to_search_profile`.

### 3.1 Structured booleans (resolved against `Listing` columns)

| ui key | listing field | invert? |
|--------|---------------|---------|
| `furnished` | `furnished` | no |
| `pet_friendly` | `pets_allowed` | no |
| `non_smoking` | `smoking_ok` | **yes** (we want `smoking_ok = False`) |

Resolution: `1.0` (positive evidence), `0.0` (negative evidence), `None`
(unknown). The hard-filter veto in §4 fires only on **negative
evidence** (`s == 0.0`), never on `None`.

### 3.2 Real-world distance via Google Places

The `(comfort, ok, max)` triples are pinned per category. They are the
single source of truth; the proof tests and production code import the
same `PLACE_DISTANCE_BANDS` table.

| ui key | google primary types | comfort_m | ok_m | max_m |
|--------|----------------------|----------:|-----:|------:|
| `supermarket` | `supermarket`, `grocery_store` | 400 | 900 | 1500 |
| `gym` | `gym` | 400 | 900 | 1500 |
| `cafe` | `cafe`, `coffee_shop` | 200 | 600 | 1000 |
| `bars` | `bar`, `pub` | 300 | 900 | 1500 |
| `library` | `library` | 400 | 1200 | 2000 |
| `coworking` | `coworking_space` (text-search fallback) | 400 | 1000 | 2000 |
| `nightlife` | `night_club`, `bar` | 500 | 1200 | 2000 |
| `park` | `park`, `national_park` | 800 | 2000 | 5000 |
| `green_space` | `park`, `national_park` | 800 | 2000 | 5000 |
| `public_transport` | `transit_station`, `subway_station`, `light_rail_station`, `bus_station` | 200 | 500 | 800 |

Resolution curve `_distance_score(d, comfort, ok, max)`:

```
if d ≤ comfort:           1.0
elif d ≤ ok:              1.0 - 0.4 * (d - comfort) / (ok - comfort)
elif d ≤ max:             0.6 * (1 - (d - ok) / (max - ok))
else:                     0.0
```

### 3.3 Description-keyword preferences (no clean structured/places source)

All matches are **case-insensitive whole-word regex**: `\bword\b`. No
substring scan — that produced false positives like `hof` matching
`Bahnhof` or `ruhig` matching `unruhig`.

| ui key | regex (any matches) |
|--------|---------------------|
| `balcony` | `\b(balkon|balcony|terrasse|terrace|dachterrasse|loggia)\b` |
| `garden` | `\b(garten|garden|innenhof)\b` |
| `elevator` | `\b(aufzug|elevator|lift|fahrstuhl)\b` |
| `dishwasher` | `\b(spülmaschine|geschirrspüler|dishwasher)\b` |
| `washing_machine` | `\b(waschmaschine|washing\s+machine|washer)\b` |
| `bike_storage` | `\b(fahrradkeller|fahrradraum|bike\s+storage|radkeller|fahrradabstellplatz)\b` |
| `parking` | `\b(parkplatz|parking|garage|tiefgarage|stellplatz)\b` |
| `quiet_area` | `\b(ruhig|ruhige|quiet|leise)\b` (and **negative** `\b(unruhig|laut|loud)\b` reduces score to 0.0) |

Resolution: `1.0` if a positive match is present, `0.0` if no match in a
non-empty description, `None` if `description` is empty.

### 3.4 LLM-resolved preferences ("soft signals")

Cannot be reliably caught by keywords or maps. Routed to the vibe LLM as
`(key, weight)` pairs; the LLM returns a per-key `0..1` score plus
optional evidence in `soft_signal_scores: dict[str, float]`. Missing key
→ `None`.

| ui key | what the LLM is asked |
|--------|-----------------------|
| `student_household` | "Does the listing describe the WG as student-oriented?" |
| `couples_ok` | "Does the listing welcome couples?" |
| `lgbt_friendly` | "Does the listing explicitly signal LGBTQ+ welcoming?" |
| `english_speaking` | "Does the listing welcome English-speakers?" |
| `international_friendly` | "Does the listing welcome internationals/expats?" |
| `wg_gender` | Resolves to `1.0` if the listing's gender preference matches the user's, `0.0` if it explicitly excludes them ("nur Frauen-WG" for a male user), `None` otherwise. |
| `wg_age_band` | `1.0` if the user's age fits the listing's stated band, `0.0` if it explicitly excludes (`"30+ WG"` for a 22-year-old), `None` otherwise. |

The LLM is told to use **only** evidence from the listing description.
It returns `None` for any key where no evidence is present. **No silent
imputation** of `0.5` on no-evidence — that's the `None` channel.

### 3.5 Removed / consolidated

The previous `STRUCTURED_PREFERENCES` table mapped
`furnished`/`pets_allowed`/`smoking_ok`. The wizard's tile names
(`furnished`/`pet_friendly`/`non_smoking`) didn't match — so the
structured veto silently never fired for the last two. v2's §3.1 uses
the wizard's tile names directly.

---

## 4. Hard filter (binary veto, runs first)

Returns the first matching reason or `None`. Vetoed listings score
`0.0`, no LLM call, persisted with a `veto_reason`. The composition
step in §6 short-circuits before any cap is consulted, so vetoed
listings never carry stale caps into persistence.

Named multipliers (the spec uses these symbols throughout):

```
PRICE_VETO_MULT          = 1.5    # §4 row 1
COMMUTE_VETO_MULT        = 2.0    # §4 row 5
COMMUTE_CAP_MULT         = 1.5    # §5.3
PREF_HARD_CAP_WEIGHT5    = 0.5    # §5.7
PREF_HARD_CAP_WEIGHT5_UNK = 0.6   # §5.7 (new)
PREF_HARD_CAP_WEIGHT4    = 0.7    # §5.7
COMMUTE_HARD_CAP         = 0.45   # §5.3
WARM_RENT_UPLIFT         = 1.20   # §2.2 (scraper, not engine)
```

| # | rule | reason string |
|---|------|---------------|
| 1 | `price_eur > max_rent_eur × PRICE_VETO_MULT`         | `"far over budget (€P > €cap)"` |
| 2 | a `weight=5` structured pref (§3.1) resolves to `0.0` (False) | `"must-have '<key>' missing"` |
| 3 | `available_from > move_in_until` (both non-null)   | `"available too late (D > D2)"` |
| 4 | `_normalize(district)` ∈ `{_normalize(d) for d in avoid_districts}` | `"district on avoid list"` |
| 5 | At least one anchor was scored AND **every** scored anchor's fastest-mode commute exceeded `anchor.budget × COMMUTE_VETO_MULT` | `"no anchor reachable within reasonable time"` |

Where `_normalize(s) = s.strip().lower().replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss").replace("-"," ")` — the same helper `_normalize_city` already in `evaluator.py`.

Threshold matrix (cross-reference for §5.3 and §9):

```
                        Soft band end (cap=1.0)   Hard cap fires    Hard veto fires
price (vs max_rent)     50 % of cap               n/a               150 % of cap
commute (vs anchor)     60 % of budget            150 % of budget   200 % of budget (across all anchors)
weight-5 pref miss      n/a                       cap=0.5 if score≤0.2; cap=0.6 if unknown   §3.1 structured prefs only
weight-4 pref miss      n/a                       cap=0.7 if score≤0.1                 n/a
```

---

## 5. Components (one `ComponentScore` each)

All component functions are **pure** (deterministic in their inputs)
except `vibe_fit`, which catches LLM errors and degrades to
`missing_data=True`. Every component returns
`ComponentScore(key, score∈[0,1], weight, evidence: list[str], hard_cap, missing_data)`.
Each evidence string ends with a provenance tag in square brackets:
`[google]`, `[listing]`, `[llm]`, or `[engine]` (engine-derived, like
percentile rank).

### 5.1 `price_fit` (weight 2.0)

```
ratio = price / max_rent
if ratio ≤ 0.5:                  score = 1.0
elif ratio ≤ 1.0:                score = 1.0 - 0.4 · (ratio − 0.5) / 0.5     # → 0.6 at cap
elif ratio ≤ 1.2:                score = max(0, 0.6 − 3.0 · (ratio − 1.0))   # → 0 at 1.2×
else:                            score = 0.0
```

`missing_data=True` if `price_eur is None` or `max_rent_eur ≤ 0`.

Evidence:

- `"€P inside budget €C [listing]"` if `price ≤ max_rent`.
- `"€P over budget €C — accelerated penalty [listing]"` if `1 < ratio ≤ 1.2`.
- `"price reported as Kaltmiete; warm-rent estimate €P (+20%) [engine]"`
  whenever `price_basis == "kalt_uplift"`.
- **Market percentile** (when `market_context` is non-null):
  `"€P at the Nth percentile for {kind} in {district} ~{size} m² [engine]"`.
  Compute `N` from peer rows (`scrape_status='full'`,
  `kind == listing.kind`, district equal under `_normalize`,
  `size_m2` within ±20 %, peers ≥ 6). Lower percentile = cheaper. This
  is purely an evidence string in v1; it does not move `price_fit.score`.
- `"suspiciously cheap (below your floor €min_rent) — verify it's real
  [engine]"` whenever `price_eur < min_rent_eur × 0.7` and
  `min_rent_eur > 0`. Also surfaced via `vibe_fit.red_flags`.

### 5.2 `size_fit` (weight 1.0)

`lo = min_size_m2`, `hi = max(max_size_m2, min_size_m2)`. The model
defaults are `lo=10, hi=40` (canonical). The spec no longer carries its
own per-`kind` defaults — the model is the single source of truth.

```
mid = lo + 0.3 · (hi − lo)
if size ≥ hi:                    score = 1.0
elif size ≥ mid:                 score = 0.85 + 0.15 · (size − mid) / (hi − mid)   # → 0.85 at mid, 1.0 at hi
elif size ≥ lo:                  score = 0.6 + 0.25 · (size − lo) / (mid − lo)     # → 0.6 at lo, 0.85 at mid
elif lo > 0:                     score = 0.6 · (size / lo)                         # → 0 at 0, 0.6 at lo (monotone)
else:                            score = 0.0
```

**Continuity at `lo`** is now exact (both branches yield 0.6) and the
function is monotone non-decreasing in `size`. `missing_data=True` if
`size_m2 is None`.

### 5.3 `commute_fit` (weight 2.5)

Per anchor, pick the **fastest available mode** (modes are restricted by
`commute.modes_for(profile)`: `TRANSIT` always; plus `BICYCLE`/`DRIVE`
per `has_*`). Convert seconds → minutes.

```
budget = anchor.max_commute_minutes or DEFAULT_COMMUTE_BUDGET_MIN     # 35
ratio  = minutes / budget
if ratio ≤ 0.6:                  sub = 1.0
elif ratio ≤ 1.0:                sub = 1.0 - 0.4 · (ratio − 0.6) / 0.4   # → 0.6 at budget
elif ratio ≤ 1.5:                sub = max(0, 0.6 - 1.2 · (ratio − 1.0))  # → 0 at 1.5×
else:                            sub = 0.0
```

Aggregator across anchors (weighted blend of min and mean — the
"deal-breaker" min pulls bad anchors hard, the mean preserves nuance
when all anchors are reasonable):

```
score = 0.7 · min(sub_scores) + 0.3 · mean(sub_scores)
```

Hard cap: if any anchor's fastest mode exceeds
`COMMUTE_CAP_MULT × budget` (1.5×), set `hard_cap = COMMUTE_HARD_CAP`
(0.45). One unreachable anchor caps the whole listing.

`missing_data=True` if no anchors are configured **or** the matrix call
returned nothing for any anchor (network/key failure across the board).
Partial coverage (some anchors scored, some failed) is **not** missing
— we score on what we got and add `"X of Y anchors had routing data
[google]"` to evidence.

Evidence headline: `"<anchor>: M min by <mode> (target M' min) [google]"`.

### 5.4 `availability_fit` (weight 0.8)

```
if listing.available_from is None OR (move_in_from is None AND move_in_until is None):
    missing_data = True
elif inside [from..until]:                    1.0
elif outside by ≤ 7 days:                     0.8
elif outside by ≤ 30 days:                    0.5
elif outside by ≤ 60 days:                    0.2
else:                                         0.0
```

The two missing-data conditions are now ORed (was contradictorily AND in
v1). `missing_data` ⇔ "we lack either the listing date or the user
window". Evidence: `"available <date> — N days outside your window
[listing]"`.

### 5.5 `wg_fit` (weight 0.5)

Skipped (`missing_data=True`) when `mode == "flat"` or
`listing.kind == "flat"`. Otherwise:

```
if min_wg_size > 1 and wg_size == 1:
    score = 0.0   # explicit floor: lone-roommate "WG" never matches a "real WG" preference
elif lo ≤ n ≤ hi:                 1.0
elif n in {lo - 1, hi + 1}:        0.6
elif n in {lo - 2, hi + 2}:        0.3
else:                              0.0
```

The `wg_size == 1` floor is now expressed as an explicit early-return
that overrides the band check, removing the contradiction the v1 spec
had.

### 5.6 `tenancy_fit` (weight 0.6)

Soft signal that the listing's **length** matches what the user wants.
Three signal sources:

1. `available_to` plus `available_from` give `L = available_to − available_from`.
2. The vibe LLM's `tenancy_label` field: one of
   `"open_ended" | "long_term" | "mid_term" | "short_term" | "unknown"`
   based on description text ("unbefristet", "Zwischenmiete für 4
   Wochen", "befristet bis SS25").
3. The user's `desired_min_months: Optional[int]` (new optional field on
   `SearchProfile`, defaults to `None` → "no preference").

Resolution:

```
listing_months = (
    L_in_months                     if L is not None
    else +infinity                  if tenancy_label == "open_ended"
    else 9                          if tenancy_label == "long_term"
    else 4                          if tenancy_label == "mid_term"
    else 1                          if tenancy_label == "short_term"
    else None
)

if listing_months is None:                    missing_data = True
elif desired_min_months is None:              # default heuristic
    if listing_months >= 12:                  1.0
    elif listing_months >= 6:                 0.7
    elif listing_months >= 3:                 0.5
    else:                                     0.2
else:
    desired = desired_min_months              # local alias for clarity
    if listing_months >= desired:             1.0
    elif listing_months >= 0.7 · desired:     0.6
    elif listing_months >= 0.5 · desired:     0.3
    else:                                     0.0
```

Crucial change vs v1: when `available_to is None`, we **do not** score
1.0 silently. We require either an `available_to` or an explicit
LLM-derived `tenancy_label != "unknown"` — otherwise `missing_data=True`.

### 5.7 `preferences_fit` (weight 1.5)

Aggregator over `profile.preferences`. Each pref resolves through one of
the four families in §3 to `(score, evidence, family)`.

```
weighted_sum = 0
total_weight = 0
caps         = []
for pref in preferences:
    s, ev, fam = resolve(pref.key, listing, nearby_places, llm_signals)
    if s is None:
        if pref.weight ≤ 3:
            continue                          # ignore unknown nice-to-haves
        if pref.weight == 4:
            s = 0.4                           # gentle nudge for unknown important
        if pref.weight == 5:
            # Treat as cap-triggering: must-have with no evidence is materially
            # different from a known-positive must-have.
            s = 0.4
            caps.append(PREF_HARD_CAP_WEIGHT5_UNK)   # 0.6
    weighted_sum += s · pref.weight
    total_weight += pref.weight
    if pref.weight == 5 and s ≤ 0.2:
        caps.append(PREF_HARD_CAP_WEIGHT5)    # 0.5
    if pref.weight == 4 and s ≤ 0.1:
        caps.append(PREF_HARD_CAP_WEIGHT4)    # 0.7

if total_weight == 0:
    return ComponentScore(missing_data=True)  # all prefs were unknown nice-to-haves

score    = weighted_sum / total_weight
hard_cap = min(caps) if caps else None
```

Behaviour notes:

- Unknown nice-to-haves (`weight ≤ 3`) are dropped — they neither help
  nor hurt the score and the denominator shrinks accordingly.
- Unknown important (`weight = 4`) get imputed `0.4` and contribute.
- Unknown must-have (`weight = 5`) get imputed `0.4` AND set the
  `PREF_HARD_CAP_WEIGHT5_UNK = 0.6` cap (closes the v1 escape route).
- If every pref came back unknown nice-to-have, the component is
  `missing_data=True` (was a misleading 0.0 in v1).

### 5.8 `vibe_fit` (weight 1.5) — LLM call

Single env-driven LLM call with strict JSON output. Model is resolved
from `WG_VIBE_MODEL` (default `gpt-5.4-nano`). Inputs:

- `notes` (truncated to 1500 chars).
- `preferred_districts` / `avoid_districts`.
- pre-fetched nearby-place facts (so the LLM can talk about them
  without re-deriving).
- listing district + description (truncated to 2000 chars).
- the LLM-resolved preference keys from §3.4 (asks the LLM for a
  per-key `0..1` score + evidence).

Output schema (Pydantic):

```python
class VibeJudgement(BaseModel):
    fit_score: float                         # 0..1, prose-only vibe
    evidence: list[str]                      # 1..4 short strings
    flatmate_vibe: str                       # one sentence
    lifestyle_match: str                     # one sentence
    red_flags: list[str]                     # 0..3 short strings
    green_flags: list[str]                   # 0..3 short strings
    soft_signal_scores: dict[str, float]     # per-key 0..1 for §3.4 prefs (None → omit key)
    tenancy_label: Literal[                  # for §5.6
        "open_ended", "long_term", "mid_term",
        "short_term", "unknown",
    ]
    scam_severity: float                     # 0..1; 1 = obvious scam
```

The v1 `prefs_weight_overrides` field has been **dropped** for v2 — it
saved tokens on every call without delivering a consumer. v3 can
re-introduce it when there's a real product use.

`vibe_fit` contributes:

- one `ComponentScore` with `score = fit_score`, weight 1.5.
- `soft_signal_scores` is consumed by `preferences_fit` (§5.7); the
  evaluator orchestrator calls vibe **before** preferences when the
  user has any §3.4 prefs configured (deterministic dependency, no
  cycle).
- `tenancy_label` feeds `tenancy_fit` (§5.6).
- `scam_severity` feeds `quality_fit` (§5.9) and adds
  `hard_cap = 0.30` to **vibe_fit** when `scam_severity ≥ 0.7` (§9
  table).
- `red_flags` go into `mismatch_reasons`; `green_flags` into
  `match_reasons`.
- `prefs_weight_overrides` is reserved for v3; v2 ignores it.

Failure modes (`missing_data=True`, no contribution):

- API key missing.
- HTTP error / timeout / 5xx.
- JSON validation fails.

When vibe fails AND the user has §3.4 prefs, those prefs route through
**deterministic fallbacks**: the per-key resolver returns `None` (not
`0.0`) so `preferences_fit` treats them as unknown per the §5.7 rules
above — i.e. weight-5 §3.4 prefs still trigger the unknown-must-have
cap. No silent zeroing.

### 5.9 `quality_fit` (weight 1.0, **excluded from `live`** — see §6)

A user-independent "is this a good Munich rental?" signal. Computes:

```
quality = 0.45 · description_quality
        + 0.25 · media_quality
        + 0.15 · availability_clarity
        + 0.15 · (1 - scam_severity)
```

- `description_quality`: `f(length, structure_markers, completeness)`.
  1.0 for ≥600 chars + at least 3 of {price, size, deposit, available,
  district} explicitly stated; 0.7 for 200–600 chars; 0.4 for 50–200;
  0.1 for <50 chars.
- `media_quality`: from `Listing.cover_photo_url + len(photo_urls)`.
  2+ photos → 1.0; 1 → 0.8; 0 → 0.4. (v3 fix #5: softer than v2's
  `3+/1-2/0 → 1.0/0.6/0.2` so honest one-photo listings aren't
  unfairly docked.)
- `availability_clarity`: 1.0 if both `available_from` and either
  `available_to` or LLM `tenancy_label != "unknown"` are present; 0.5 if
  only one; 0.0 if neither.
- `scam_severity` from §5.8 (defaults to 0 when vibe missing).

This component never hard-caps but keeps poor-quality listings from
sneaking to the top. **Never `missing_data`** — every input has a
deterministic fallback.

It is **explicitly excluded from `live`** in the §6 weighted mean — it
only enters via the post-blend in §6. The §7 weight column shows it for
transparency but the weight is `0` for the mean step (the implementation
sets `weight=0` on the `ComponentScore` so it's drawer-visible without
double-counting).

### 5.10 `upfront_cost_fit` (weight 0.6) — **new in v2**

Munich landlords routinely ask for a deposit (`Kaution`, 1-3 monthly
rents) and sometimes a furniture buyout (`Ablöse`) of several thousand
euros. A €4 000 Ablöse on a perfectly-priced room can drain a
student's savings in one day, so the engine treats it as a soft
component (not a veto) that pulls the score down.

```
deposit = listing.deposit_months
buyout  = listing.furniture_buyout_eur

if deposit is None and buyout is None:
    missing_data = True

deposit_score = (
    1.0 if deposit is None or deposit ≤ 2 else
    0.7 if deposit ≤ 3 else
    0.4 if deposit ≤ 4 else
    0.2
)
buyout_mult = (
    1.0 if buyout is None or buyout ≤ 500 else
    0.85 if buyout ≤ 2000 else
    0.6 if buyout ≤ 5000 else
    0.3
)
score = clamp01(deposit_score · buyout_mult)
```

Both inputs come from the scraper's enricher (which only fills them
when the description states them clearly per its "do not infer"
policy). When neither is known, the component goes `missing_data=True`
— we don't punish listings just for being silent on the topic.

---

## 6. Composition

```
# Step 1: hard-filter veto (§4) short-circuits everything else.
if veto:
    return EvaluationResult(score=0.0, veto_reason=veto.reason, ...)

# Step 2: weighted mean over LIVE components (NOT including quality).
live = [c for c in components
        if c.key != "quality"
        and not c.missing_data
        and c.weight > 0]
weight_total = sum(c.weight for c in live)
if weight_total == 0:
    return EvaluationResult(score=0.0, summary="No data to score", ...)
weighted = sum(c.score · c.weight for c in live)
raw      = weighted / weight_total

# Step 3: hard caps (only from non-missing components — defensive).
caps   = [c.hard_cap for c in components
          if c.hard_cap is not None and not c.missing_data]
capped = min(raw, min(caps)) if caps else raw
match_score = clamp01(capped)

# Step 4: blend with the absolute quality signal.
quality_score = quality_fit_component.score   # always present
final = clamp01(0.85 · match_score + 0.15 · quality_score)
```

`final` is what's persisted as `score`. `match_score` and
`quality_score` are persisted separately in their own
`ComponentScore`s so the drawer can show the breakdown.

---

## 7. Default weights summary (v2)

| component | weight (in `live`) | role |
|-----------|-------------------:|------|
| `price` | 2.0 | match — budget fit |
| `commute` | 2.5 | match — anchor reachability |
| `preferences` | 1.5 | match — soft preferences |
| `vibe` | 1.5 | match — LLM vibe |
| `size` | 1.0 | match — room/flat size |
| `availability` | 0.8 | match — move-in alignment |
| `tenancy` | 0.6 | match — lease length |
| `upfront_cost` | 0.6 | match — deposit + furniture buyout |
| `wg_size` | 0.5 | match — household size (skipped for `flat`) |
| `quality` | **0** in `live`, **0.15** post-blend | absolute (drawer shows it as 1.0 weight for visibility) |

`live` total weight (everyone present, not counting `quality`):
`2.0 + 2.5 + 1.5 + 1.5 + 1.0 + 0.8 + 0.6 + 0.6 + 0.5 = 11.0`.

So commute + price together are **43 %** of `match_score` pre-cap, the
LLM is **14 %**, lifestyle prefs are **14 %**, and `final` carries an
additional `+0.15` of absolute quality on top (with `match_score`
shrunk to `0.85 ×` of its raw self).

Worked examples (no caps):

```
"perfect" listing  : match=1.0, quality=0.9 → final = 0.85*1.0 + 0.15*0.9 = 0.985
"perfect, no pics" : match=1.0, quality=0.5 → final = 0.85*1.0 + 0.15*0.5 = 0.925
"so-so, polished"  : match=0.6, quality=1.0 → final = 0.85*0.6 + 0.15*1.0 = 0.660
"great fit, dump"  : match=0.9, quality=0.2 → final = 0.85*0.9 + 0.15*0.2 = 0.795
```

---

## 8. Missing-data policy summary

| component | what makes it missing |
|-----------|-----------------------|
| `price` | `price_eur is None` OR `max_rent_eur ≤ 0` |
| `size` | `size_m2 is None` |
| `commute` | no anchors configured OR distance-matrix returned `{}` for ALL anchors |
| `availability` | `available_from is None` OR (`move_in_from is None` AND `move_in_until is None`) |
| `tenancy` | `available_to is None` AND `tenancy_label in (None, "unknown")` |
| `wg_size` | `mode == "flat"` OR `listing.kind == "flat"` OR `wg_size is None` |
| `upfront_cost` | `deposit_months is None` AND `furniture_buyout_eur is None` |
| `preferences` | no preferences configured OR `total_weight == 0` after dropping unknown nice-to-haves |
| `vibe` | LLM call failed (any reason) |
| `quality` | **never** — all inputs have deterministic fallbacks |

Missing components are dropped from the weighted mean and their weight
is removed from the denominator. Caps from missing components are
**also** dropped (defensive, no stale caps from a failed component).

---

## 9. Hard caps summary

| trigger | cap | source |
|---------|----:|--------|
| Any anchor's fastest mode > `COMMUTE_CAP_MULT × budget` (1.5×) | `0.45` | `commute_fit` |
| A weight-5 §3.1 pref resolves to `0.0` | (vetoed in §4 instead) | — |
| A weight-5 §3.2/3.3/3.4 pref resolves to `≤ 0.2` | `0.50` | `preferences_fit` |
| A weight-5 §3.2/3.3/3.4 pref is unknown | `0.60` | `preferences_fit` |
| A weight-4 pref resolves to `≤ 0.1` | `0.70` | `preferences_fit` |
| `vibe_fit.scam_severity ≥ 0.7` | `0.30` | `vibe_fit` |

Caps stack via `min`. Caps are scanned only across **non-missing**
components (so a missing `vibe_fit` cannot impose a stale 0.30 cap).

---

## 10. Explainability contract

The drawer expects every `ComponentScore` to carry useful evidence. The
v2 contract:

- `evidence[0]` is always the headline. Every entry ends with a
  provenance tag: `[google]`, `[listing]`, `[llm]`, `[engine]`.
- For multi-anchor / multi-pref components, `evidence[1:]` are the
  per-anchor / per-pref breakdowns (capped at 6).
- `match_reasons` ← every `evidence[0]` from a live component scoring
  `≥ 0.7` (cap 6), plus the LLM's `green_flags` (cap 3 of those).
- `mismatch_reasons` ← every `evidence[0]` from a live component
  scoring `≤ 0.3` (cap 6), plus the LLM's `red_flags` (cap 3 of those).
- `score_reason` is one sentence: highest-scoring + lowest-scoring live
  component (only the lowest if `≤ 0.4`), plus a tail naming the cap
  source when `capped < raw`:

  ```
  "Score 84%: strong commute fit (Marienplatz: 18 min by transit
  [google]); weak preferences fit (no balcony [listing])
  (capped at 0.50 by missing must-have furnished)"
  ```

The cap-source string is built from the `(component, cap_value, reason)`
triple that produced the binding cap. The implementation always knows
which cap won (it's `min(caps)`) and stores the source in the result.

---

## 11. Test surface

Three test files live under `backend/tests/`:

- `test_evaluator.py` (rewritten): one row per curve boundary, one row
  per missing-data path, hard-filter cases, composition arithmetic with
  and without caps, the `quality` exclusion from `live`, the §5.4
  OR-condition.
- `test_evaluator_resolvers.py` (new): preference resolution per family
  — structured booleans (incl. `non_smoking` inversion), distance
  bands using the pinned `PLACE_DISTANCE_BANDS`, regex word-boundary
  scans (incl. `Bahnhof` and `unruhig` non-matches), LLM dict feedback.
- `test_evaluator_integration.py` (new): pure-Python end-to-end on
  fabricated `Listing`/`SearchProfile`/`travel_times`/`nearby_places`,
  with `vibe_fit` mocked, asserting full `EvaluationResult` for ten
  realistic listing/profile combinations.

A `scripts/check_engine.py` smoke run wires the engine to the live
APIs against ten Munich listings pulled from the shared DB so a human
can eyeball the rankings, including an A/B against the current
`evaluator.py` so we can prove v2 is not regressive.

---

## 12. What we're deliberately NOT doing in v2

- No learning. Weights are hand-picked, not fit to feedback.
- No per-user weight overrides. Everyone shares §7's table.
- No second LLM pass (e.g. self-critique or top-N rerank).
- No vision-LLM image scoring.
- No per-anchor weight in the UI. The matcher's commute aggregator
  uses `0.7·min + 0.3·mean` uniformly across anchors.
- Time-of-day commute robustness (9 am vs 8 pm comparison) is reserved
  for v3 — the `commute.travel_times` API supports it but the engine
  ignores it for v2 to keep latency down.

## 13. Data contracts & migrations

The implementation adds five new fields. All are nullable on existing
rows; the engine reads them defensively (legacy `None` → "unknown" /
`missing_data=True` per the table in §8).

| field | type | location | filled by |
|-------|------|----------|-----------|
| `Listing.price_basis` / `ListingRow.price_basis` | `"warm" \| "kalt_uplift" \| "unknown"` | `models.py`, `db_models.py` | scraper enricher (`enricher.py`); legacy rows backfilled to `"unknown"` by `migrate_matcher_v2.py` |
| `Listing.deposit_months` / `ListingRow.deposit_months` | `Optional[float]` | `models.py`, `db_models.py` | scraper enricher only when description states the deposit explicitly |
| `Listing.furniture_buyout_eur` / `ListingRow.furniture_buyout_eur` | `Optional[int]` | `models.py`, `db_models.py` | scraper enricher only when description states the Ablöse explicitly |
| `SearchProfile.desired_min_months` / `SearchProfileRow.desired_min_months` | `Optional[int]` | `models.py`, `db_models.py`, `dto.py` (DTO + body), `frontend/src/types.ts` | wizard (not yet — see ROADMAP "Wizard catch-up for matcher v2"); engine falls back to default ladder when `None` |
| `SearchProfile.flatmate_self_gender` / `_age` / `*Row.flatmate_self_*` | `Optional[Gender] / Optional[int]` | `models.py`, `db_models.py`, `dto.py`, `frontend/src/types.ts` | wizard (not yet); LLM resolves `wg_gender` / `wg_age_band` to `None` when these are unset |

Migration: run `backend/venv/bin/python -m app.scraper.migrate_matcher_v2`
once before the new backend image starts. The script is idempotent,
supports `--dry-run`, and uses `ALTER TABLE … ADD COLUMN` only when
`information_schema` says the column is missing — re-running it is
safe.
