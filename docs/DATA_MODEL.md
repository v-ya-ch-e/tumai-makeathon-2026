# Data model

MySQL tables mirror [`db_models.py`](../backend/app/wg_agent/db_models.py). Domain aggregates used by the agent are [`Hunt` and nested types](../backend/app/wg_agent/models.py). All ORM ↔ domain conversion goes through [`repo.py`](../backend/app/wg_agent/repo.py) (plus a few direct `Session.get` calls in [`api.py`](../backend/app/wg_agent/api.py) for listing detail assembly).

Post-[ADR-018](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only), the write ownership is split:

- The **scraper** container (see [`backend/app/scraper/`](../backend/app/scraper/)) is the sole writer of `ListingRow` and `PhotoRow`. It keys listings by wg-gesucht's numeric id — one row per listing across all users.
- The **backend** container writes only `ListingScoreRow` (per-hunt), `AgentActionRow` (per-hunt), and everything user / hunt / profile / credentials related.

A `ListingScoreRow` row is the **hunt ↔ listing membership record**: the frontend's matched-listings view ([`list_listings_for_hunt`](../backend/app/wg_agent/repo.py)) joins `listingscorerow JOIN listingrow`, so a listing only appears in a hunt after the matcher has scored it (including `score=0.0` veto rows).

## Entities

### UserRow

Local account: unique username plus demographics. One row per person using the app.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `username` | `str` | Primary key; chosen handle. |
| `age` | `int` | 16–99 in API validation. |
| `gender` | `str` | Stores `Gender` enum value as string. |
| `created_at` | `datetime` | UTC timestamp when the row was inserted. |

- **SET**: [`repo.create_user`](../backend/app/wg_agent/repo.py) on `POST /api/users`.
- **READ**: [`repo.get_user`](../backend/app/wg_agent/repo.py) for profile fetch and as a guard on nested routes.

### WgCredentialsRow

Optional wg-gesucht credentials as a single Fernet ciphertext blob (JSON inside). Separate from profile so it can be deleted independently.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `username` | `str` | PK + FK → `userrow.username`. |
| `encrypted_payload` | `bytes` | Fernet output from [`crypto.encrypt`](../backend/app/wg_agent/crypto.py). |
| `saved_at` | `datetime` | Last successful upsert time. |

- **SET**: [`repo.upsert_credentials`](../backend/app/wg_agent/repo.py) on `PUT /api/users/{username}/credentials`.
- **READ**: Row existence and `saved_at` via [`repo.credentials_status`](../backend/app/wg_agent/repo.py) for `GET .../credentials` (plaintext never returned).

### SearchProfileRow

One-to-one requirements/preferences schedule slice persisted for the wizard. Maps to domain [`SearchProfile`](../backend/app/wg_agent/models.py) with extra defaults filled in code when building the domain object.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `username` | `str` | PK + FK → `userrow.username`. |
| `price_min_eur` | `int` | Lower rent bound. |
| `price_max_eur` | `Optional[int]` | Upper bound; `None` triggers defaults in repo when building `SearchProfile`. |
| `main_locations` | `JSON` / `list[PlaceLocation]` | User-picked places from Google Places Autocomplete. Each element is `{label, place_id, lat, lng, max_commute_minutes}`; the first entry's `label` seeds `SearchProfile.city` for the wg-gesucht search URL builder. `lat`/`lng` feed commute-based scoring. `max_commute_minutes` (5–240, nullable) is a per-location soft upper bound the scorer compares against the fastest mode. |
| `has_car` | `bool` | Commute / POI hint. |
| `has_bike` | `bool` | Same. |
| `mode` | `str` | `"wg"`, `"flat"`, or `"both"`. |
| `move_in_from` | `Optional[date]` | |
| `move_in_until` | `Optional[date]` | |
| `preferences` | `JSON` / `list[PreferenceWeight]` | Weighted preference tags from the UI. Each element is `{key, weight}` where `key` is a snake_case tag (e.g. `gym`, `furnished`) and `weight` is 1–5 (5 = must-have). `repo.get_search_profile` tolerates legacy bare-string elements by promoting them to `weight=3`. |
| `rescan_interval_minutes` | `int` | Used when spawning hunts and periodic loops. |
| `schedule` | `str` | `"one_shot"` or `"periodic"`. |
| `updated_at` | `datetime` | Bumped on upsert. |

- **SET**: [`repo.upsert_search_profile`](../backend/app/wg_agent/repo.py) on `PUT /api/users/{username}/search-profile`.
- **READ**: [`repo.get_search_profile`](../backend/app/wg_agent/repo.py) for hunt creation, resumption, and `GET` search profile.

### HuntRow

One agent run or long-lived periodic job for a user.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | Primary key; 12 hex chars from UUID (`repo.create_hunt`). |
| `username` | `str` | FK → `userrow.username`, indexed. |
| `status` | `str` | Domain [`HuntStatus`](../backend/app/wg_agent/models.py): `pending`, `running`, `done`, `failed`. |
| `schedule` | `str` | `"one_shot"` or `"periodic"` (echoed on DTO). |
| `started_at` | `datetime` | Creation time. |
| `stopped_at` | `Optional[datetime]` | Set when status moves to `done` or `failed`. |

- **SET**: Insert in [`repo.create_hunt`](../backend/app/wg_agent/repo.py); status updates via [`repo.update_hunt_status`](../backend/app/wg_agent/repo.py) from API and periodic runner.
- **READ**: [`session.get(HuntRow, id)`](../backend/app/wg_agent/api.py), [`repo.get_hunt`](../backend/app/wg_agent/repo.py), [`repo.list_hunts_by_status`](../backend/app/wg_agent/repo.py).

### ListingRow

**Global** normalized wg-gesucht listing. Single-column PK on `id`; the scraper container is the sole writer.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | wg-gesucht listing id. Sole primary key. |
| `url` | `str` | Canonical or long URL as string. |
| `title` | `Optional[str]` | |
| `price_eur` | `Optional[int]` | |
| `size_m2` | `Optional[float]` | |
| `wg_size` | `Optional[int]` | |
| `city` | `Optional[str]` | Parsed from the Adresse panel; fed into `brain.vibe_score`'s listing summary. |
| `district` | `Optional[str]` | Munich Bezirk / district name; used in `evaluator.hard_filter`'s avoid-districts veto. |
| `address` | `Optional[str]` | Street + number. Used as the geocoder fallback query when the map pin is missing. |
| `lat` | `Optional[float]` | Populated during `anonymous_scrape_listing`: first from the listing's `map_config.markers` script block via `browser._parse_map_lat_lng` (no external call, landlord-precise), falling back to [`geocoder.geocode`](../backend/app/wg_agent/geocoder.py) for listings that don't ship a map pin. `None` when both paths are unavailable. |
| `lng` | `Optional[float]` | Same origin as `lat`; paired with it for commute-aware scoring. |
| `available_from` | `Optional[date]` | |
| `available_to` | `Optional[date]` | |
| `description` | `Optional[str]` | Filled after deep scrape. |
| `furnished` | `Optional[bool]` | `True` / `False` / `None` (unknown). Feeds `evaluator.hard_filter` (weight-5 "must-have furnished" veto) and `evaluator.preference_fit`. |
| `pets_allowed` | `Optional[bool]` | Same shape as `furnished`; feeds the same evaluator paths for weight-5 "must-have pets" vetoes. |
| `smoking_ok` | `Optional[bool]` | Same shape; feeds the smoking-preference evaluator paths. |
| `languages` | `Optional[list[str]]` | Languages spoken in the WG (e.g. `["Deutsch", "Englisch"]`). Fed into the `brain.vibe_score` prompt. JSON column. |
| `scrape_status` | `str` | `"stub"` (partial, don't score yet), `"full"` (description + coords present), or `"failed"` (scrape exception). Indexed. The matcher only iterates `status="full"` rows. |
| `scraped_at` | `Optional[datetime]` | UTC timestamp of the last `upsert_global_listing`. Indexed so the scraper can cheaply find stale rows via `list_stale_listings`. |
| `scrape_error` | `Optional[str]` | Free-text breadcrumb when `scrape_status == "failed"`. |
| `first_seen_at` | `datetime` | Preserved across upserts. |
| `last_seen_at` | `datetime` | Bumped on each upsert. |

- **SET**: [`repo.upsert_global_listing`](../backend/app/wg_agent/repo.py) from [`ScraperAgent._scrape_and_save`](../backend/app/scraper/agent.py). Hunts never write this table.
- **READ**: [`repo.list_listings_for_hunt`](../backend/app/wg_agent/repo.py) (joined through `ListingScoreRow`), [`repo.list_scorable_listings`](../backend/app/wg_agent/repo.py) in the matcher, direct `session.get(ListingRow, listing_id)` in [`api._get_listing_detail`](../backend/app/wg_agent/api.py).

### PhotoRow

Ordered image URLs for a listing drawer. Composite PK `(listing_id, ordinal)`; photos live alongside the global listing.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `listing_id` | `str` | Part of PK; FK → `listingrow.id`. |
| `ordinal` | `int` | Part of PK; zero-based order. |
| `url` | `str` | Absolute image URL. |

- **SET**: [`repo.save_photos`](../backend/app/wg_agent/repo.py) from the scraper right after `upsert_global_listing`.
- **READ**: `select(PhotoRow)...` in [`api._get_listing_detail`](../backend/app/wg_agent/api.py), `_cover_photo_url` inside `repo._listing_from_row`.

### ListingScoreRow

Latest scorecard payload per `(listing_id, hunt_id)` — also the hunt ↔ listing membership record. Every listing the matcher has touched for a hunt has a row here, including vetoed listings (`score=0.0`, `veto_reason` set).

| Field | Type | Notes |
| ----- | ---- | ----- |
| `listing_id` | `str` | PK part; FK → `listingrow.id`. |
| `hunt_id` | `str` | PK part; FK → `huntrow.id`. |
| `score` | `float` | 0..1 in domain; stored as given. |
| `reason` | `Optional[str]` | Human-readable explanation. |
| `match_reasons` | `JSON` / `list` | |
| `mismatch_reasons` | `JSON` / `list` | |
| `travel_minutes` | `Optional[JSON]` | Fastest `{mode, minutes}` per `main_location.place_id` when commute data was available at score time. Shape: `{"<place_id>": {"mode": "BICYCLE", "minutes": 18}}`. Populated by `HuntEngine.run_find_only` from the full `commute.travel_times` matrix; read back by `_get_listing_detail` and re-keyed by label for the drawer. |
| `nearby_places` | `Optional[JSON]` | Persisted nearby-place facts for place-like preferences. Shape: `[{key, label, searched, distance_m, place_name, category}]`. Populated by `HuntEngine.run_find_only` from [`places.nearby_places`](../backend/app/wg_agent/places.py); read back by `_get_listing_detail` for the drawer's nearby-preferences section. |
| `components` | `Optional[JSON]` | Scorecard breakdown: `list[{key, score, weight, evidence, hard_cap?, missing_data}]`, one entry per `evaluator` component (price, size, wg_size, availability, commute, preferences, vibe). NULL on vetoed listings and on rows written before `components` existed. Rendered as per-component bars in `ListingDrawer`. |
| `veto_reason` | `Optional[str]` | Set when `evaluator.hard_filter` short-circuited evaluation (over budget, wrong city, avoid-district, etc.). Mutually exclusive with a populated `components` list; score is pinned at 0.0. |
| `scored_against_scraped_at` | `Optional[datetime]` | `ListingRow.scraped_at` at the moment the matcher produced this score. Lets the UI say "scored against listing data from N hours ago" and lets a future rescore path detect stale scores. |
| `scored_at` | `datetime` | |

- **SET**: [`repo.save_score`](../backend/app/wg_agent/repo.py) immediately after `evaluator.evaluate` in `HuntEngine.run_find_only`.
- **READ**: Joined in `repo.list_listings_for_hunt` via `_listing_from_row` (which rehydrates `components` via `_components_from_row`) and in `_get_listing_detail` (the detail endpoint also resolves `place_id` to `main_location.label` for the `travel_minutes_per_location` DTO field, rehydrates `components` via `_components_dto_from_row`, and returns `nearby_places` as `nearby_preference_places` for the drawer).

### AgentActionRow

Append-only audit log for UI and debugging. `kind` is a plain string (not a DB enum) so new action types do not require migrations.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `Optional[int]` | Autoincrement primary key. |
| `hunt_id` | `str` | FK → `huntrow.id`, indexed. |
| `kind` | `str` | [`ActionKind.value`](../backend/app/wg_agent/models.py). |
| `summary` | `str` | Short line for the log. |
| `detail` | `Optional[str]` | Optional stack or extra text. |
| `listing_id` | `Optional[str]` | FK → `listingrow.id` (nullable). Actions that reference a listing point into the global pool. |
| `at` | `datetime` | Timestamp. |

- **SET**: [`repo.append_action`](../backend/app/wg_agent/repo.py) from API boot/stop paths and from `HuntEngine` / `PeriodicHunter`.
- **READ**: [`repo.list_actions_for_hunt`](../backend/app/wg_agent/repo.py); SSE path also reloads actions from DB while draining the live queue.

### MessageRow

Reserved for outbound/inbound landlord messages. Table is created by `SQLModel.metadata.create_all` on startup; no repository helpers in v1.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `Optional[int]` | Autoincrement PK. |
| `listing_id` | `str` | Indexed; FK → `listingrow.id`. |
| `hunt_id` | `str` | Indexed; FK → `huntrow.id`. |
| `direction` | `str` | Planned: `outbound` / `inbound`. |
| `text` | `str` | Message body. |
| `sent_at` | `datetime` | |

- **SET / READ**: Not used by the current JSON API or periodic hunter.

## ER diagram

Every relationship below is declared as a SQL-level foreign key on MySQL via the SQLModel `Field(foreign_key=...)` annotations in [`db_models.py`](../backend/app/wg_agent/db_models.py), picked up by `SQLModel.metadata.create_all`. The scraper writes `ListingRow` + `PhotoRow`; hunts write `ListingScoreRow` + `AgentActionRow`. `ListingScoreRow` is the hunt ↔ listing membership record.

```mermaid
erDiagram
  UserRow ||--o| WgCredentialsRow : "optional"
  UserRow ||--|| SearchProfileRow : "one"
  UserRow ||--o{ HuntRow : "has many"
  HuntRow ||--o{ ListingScoreRow : "scored"
  HuntRow ||--o{ AgentActionRow : "logs"
  ListingRow ||--o{ ListingScoreRow : "matched to hunts"
  ListingRow ||--o{ PhotoRow : "ordered urls"
  ListingRow ||--o{ AgentActionRow : "referenced by"
  ListingRow ||--o{ MessageRow : "reserved v2"
  HuntRow ||--o{ MessageRow : "reserved v2"
```

## The three-layer rule (in detail)

```mermaid
flowchart LR
  UI["React (TS types in frontend/src/types.ts)"] --> DTO["API DTOs<br/>(Pydantic, in backend/app/wg_agent/api.py)"]
  DTO --> Domain["Domain models<br/>(Pydantic, in backend/app/wg_agent/models.py)"]
  Domain --> Repo["repo.py<br/>(conversion boundary)"]
  Repo --> Tables["SQLModel tables<br/>(backend/app/wg_agent/db_models.py)"]
  Tables --> MySQL[("MySQL")]
```

### React ([`types.ts`](../frontend/src/types.ts))

The browser owns camelCase TypeScript types that mirror JSON after client-side normalization. Components and hooks never import SQLAlchemy or SQLModel. Network I/O goes through [`api.ts`](../frontend/src/lib/api.ts), which applies `toCamel` / `toSnake` so field names stay consistent with Python’s snake_case on the wire. This layer is disposable at build time: it has no direct knowledge of the table layout.

### API DTOs ([`dto.py`](../backend/app/wg_agent/dto.py))

Pydantic models such as `UserDTO`, `HuntDTO`, and `UpsertSearchProfileBody` define the HTTP contract: snake_case field names in JSON, validation on input bodies, and explicit conversion helpers (`user_to_dto`, `hunt_to_dto`, `upsert_body_to_search_profile`, …). Route handlers in [`api.py`](../backend/app/wg_agent/api.py) call these helpers rather than returning SQLModel rows. This keeps OpenAPI and static typing aligned with what the React client actually sends and receives.

### Domain models ([`models.py`](../backend/app/wg_agent/models.py))

`SearchProfile`, `Listing`, `Hunt`, `AgentAction`, and related types describe agent semantics (scoring fields on `Listing`, action kinds, hunt status). They are plain Pydantic models with **no** SQLModel mixins. [`brain.py`](../backend/app/wg_agent/brain.py), [`browser.py`](../backend/app/wg_agent/browser.py), and [`periodic.py`](../backend/app/wg_agent/periodic.py) consume and produce these types only.

### Rows ([`db_models.py`](../backend/app/wg_agent/db_models.py)) and [`repo.py`](../backend/app/wg_agent/repo.py)

`*Row` classes map 1:1 to tables. [`repo.py`](../backend/app/wg_agent/repo.py) is the only module that routinely converts between `*Row` instances and domain models (narrow public surface: `create_user`, `get_user`, `upsert_search_profile`, `get_search_profile`, `upsert_credentials`, `delete_credentials`, `credentials_status`, `create_hunt`, `get_hunt`, `update_hunt_status`, `append_action`, `upsert_listing`, `save_score`, `save_photos`, `list_hunts_by_status`, `list_listings_for_hunt`, `list_actions_for_hunt`, plus internal helpers). Exceptions: `api._get_listing_detail` reads `ListingRow` / `PhotoRow` / `ListingScoreRow` directly for the listing drawer endpoint.

## Example JSON for each entity

Values below are illustrative; timestamps are ISO-8601 strings as JSON would show after `model_dump(mode="json")`.

**UserRow**

```json
{
  "username": "lea",
  "age": 23,
  "gender": "female",
  "created_at": "2024-01-02T03:04:05"
}
```

**WgCredentialsRow** (API never returns this; shape is the decrypted JSON inside the blob)

```json
{
  "username": "lea",
  "encrypted_payload": "gAAAAABl…",
  "saved_at": "2024-01-02T04:00:00"
}
```

**SearchProfileRow**

```json
{
  "username": "lea",
  "price_min_eur": 400,
  "price_max_eur": 950,
  "main_locations": [
    {
      "label": "Technische Universität München, Arcisstraße 21",
      "place_id": "ChIJ2V-Mo_l1nkcRfZixfUq4DAE",
      "lat": 48.1497,
      "lng": 11.5679,
      "max_commute_minutes": 25
    },
    {
      "label": "Sendling, München",
      "place_id": "ChIJsendlingPlaceId",
      "lat": 48.1168,
      "lng": 11.5483,
      "max_commute_minutes": null
    }
  ],
  "has_car": true,
  "has_bike": false,
  "mode": "flat",
  "move_in_from": null,
  "move_in_until": null,
  "preferences": [
    { "key": "park", "weight": 5 },
    { "key": "gym", "weight": 2 }
  ],
  "rescan_interval_minutes": 60,
  "schedule": "periodic",
  "updated_at": "2024-01-02T03:04:05"
}
```

**HuntRow**

```json
{
  "id": "a1b2c3d4e5f6",
  "username": "lea",
  "status": "running",
  "schedule": "one_shot",
  "started_at": "2024-01-02T05:00:00",
  "stopped_at": null
}
```

**ListingRow**

```json
{
  "id": "13115694",
  "url": "https://www.wg-gesucht.de/13115694.html",
  "title": "Room near Laim S-Bahn",
  "price_eur": 795,
  "size_m2": 14.0,
  "wg_size": 4,
  "city": "München",
  "district": "Laim",
  "address": "Fürstenrieder Straße 32",
  "lat": 48.1432,
  "lng": 11.5033,
  "available_from": "2026-05-01",
  "available_to": null,
  "description": "Bright room, shared kitchen…",
  "furnished": true,
  "pets_allowed": false,
  "smoking_ok": false,
  "languages": ["Deutsch", "Englisch"],
  "scrape_status": "full",
  "scraped_at": "2024-01-02T05:02:10",
  "scrape_error": null,
  "first_seen_at": "2024-01-02T05:01:00",
  "last_seen_at": "2024-01-02T05:02:10"
}
```

**PhotoRow**

```json
{
  "listing_id": "13115694",
  "ordinal": 0,
  "url": "https://www.wg-gesucht.de/gal/…/thumb.jpg"
}
```

**ListingScoreRow**

```json
{
  "listing_id": "13115694",
  "hunt_id": "a1b2c3d4e5f6",
  "score": 0.82,
  "reason": "Score 0.82: strong price: €795 within comfortable band; weak commute: 42 min vs budget 40",
  "match_reasons": ["€795 within comfortable band"],
  "mismatch_reasons": ["Sendling: 42 min (transit) vs budget 40 min"],
  "travel_minutes": {
    "ChIJ2V-Mo_l1nkcRfZixfUq4DAE": { "mode": "BICYCLE", "minutes": 18 },
    "ChIJsendlingPlaceId": { "mode": "TRANSIT", "minutes": 14 }
  },
  "nearby_places": [
    {
      "key": "gym",
      "label": "Gym",
      "searched": true,
      "distance_m": 240,
      "place_name": "Fit Star",
      "category": "sport.fitness.fitness_centre"
    },
    {
      "key": "supermarket",
      "label": "Supermarket",
      "searched": true,
      "distance_m": null,
      "place_name": null,
      "category": null
    }
  ],
  "components": [
    {
      "key": "price",
      "score": 1.0,
      "weight": 2.0,
      "evidence": ["€795 within comfortable band (≤ €765)"],
      "hard_cap": null,
      "missing_data": false
    },
    {
      "key": "commute",
      "score": 0.45,
      "weight": 2.0,
      "evidence": ["Sendling: 42 min (transit) vs budget 40 min"],
      "hard_cap": null,
      "missing_data": false
    },
    {
      "key": "vibe",
      "score": 0.7,
      "weight": 1.0,
      "evidence": ["warm wording about shared dinners"],
      "hard_cap": null,
      "missing_data": false
    }
  ],
  "veto_reason": null,
  "scored_against_scraped_at": "2024-01-02T05:02:10",
  "scored_at": "2024-01-02T05:02:15"
}
```

**AgentActionRow**

```json
{
  "id": 42,
  "hunt_id": "a1b2c3d4e5f6",
  "kind": "evaluate",
  "summary": "Scored 13115694: 0.82",
  "detail": null,
  "listing_id": "13115694",
  "at": "2024-01-02T05:02:15"
}
```

**MessageRow** (schema-only example)

```json
{
  "id": 1,
  "listing_id": "13115694",
  "hunt_id": "a1b2c3d4e5f6",
  "direction": "outbound",
  "text": "Hallo, ich interessiere mich für die Wohnung…",
  "sent_at": "2024-01-02T06:00:00"
}
```

## Field lifecycle for `ListingRow` (scraper) and `ListingScoreRow` (hunt)

**Scraper container** (sole writer of `ListingRow` + `PhotoRow`):

1. **Search stub** — `browser.anonymous_search` returns domain `Listing` objects with id, url, title, partial card fields.
2. **Refresh gate** — `ScraperAgent._needs_scrape` skips listings whose `scrape_status == 'full'` and whose `scraped_at` is within `SCRAPER_REFRESH_HOURS`.
3. **Deep scrape** — `browser.anonymous_scrape_listing` fills description, address/district, availability, etc. On HTTP failure the stub is persisted with `scrape_status='failed'` and `scrape_error`.
4. **Persist** — `repo.upsert_global_listing` writes/merges `ListingRow` with `scrape_status='full'` when description + coords are present (otherwise `'stub'`), preserving `first_seen_at` and bumping `last_seen_at` + `scraped_at`. `repo.save_photos` replaces `PhotoRow`s.

**Backend container, per hunt** (matcher, writes only `ListingScoreRow`):

1. **Candidate fetch** — `repo.list_scorable_listings(hunt_id, status='full')` returns global listings not yet scored for this hunt.
2. **Log new_listing** — one `AgentActionRow` per candidate via `repo.append_action(ActionKind.new_listing)`.
3. **Evaluate** — `evaluator.evaluate` runs `hard_filter`, all deterministic components, and the narrow `brain.vibe_score` LLM call on the in-memory `Listing` rehydrated via `repo.row_to_domain_listing`.
4. **Persist** — `repo.save_score` writes `ListingScoreRow` with `score`, components, veto, and `scored_against_scraped_at = row.scraped_at`. Vetoes still write a row so the UI can show the rejection reason.
5. **Re-read** — `GET /api/hunts/{id}` rebuilds DTOs via `repo.get_hunt` → `list_listings_for_hunt`, which joins `ListingScoreRow JOIN ListingRow`. `GET /api/listings/{id}?hunt_id=` reads `ListingRow` by id and `ListingScoreRow` by `(id, hunt_id)`.

```mermaid
sequenceDiagram
  participant SA as ScraperAgent
  participant WG as wg-gesucht.de
  participant Engine as HuntEngine (matcher)
  participant Evaluator as evaluator.evaluate
  participant Brain as brain.vibe_score
  participant DB as MySQL

  SA->>WG: anonymous_search + anonymous_scrape_listing
  WG-->>SA: enriched Listing
  SA->>DB: upsert_global_listing (ListingRow, status=full) + save_photos (PhotoRow)

  Engine->>DB: list_scorable_listings(hunt_id)
  DB-->>Engine: ListingRow candidates
  Engine->>DB: append_action(new_listing)
  Engine->>Evaluator: hard_filter + components
  Evaluator->>Brain: vibe_score (only if not vetoed)
  Brain-->>Evaluator: VibeScore
  Evaluator-->>Engine: EvaluationResult
  Engine->>DB: save_score (ListingScoreRow, scored_against_scraped_at)
  Engine->>DB: append_action(evaluate) — "Scored" or "Rejected"
```
