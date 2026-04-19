# Data model

MySQL tables mirror [`db_models.py`](../backend/app/wg_agent/db_models.py). Domain aggregates used by the agent live in [`models.py`](../backend/app/wg_agent/models.py). All ORM ↔ domain conversion goes through [`repo.py`](../backend/app/wg_agent/repo.py) (plus a few direct `Session.get` calls in [`api.py`](../backend/app/wg_agent/api.py) for listing detail assembly).

Post-[ADR-018](./DECISIONS.md#adr-018-separate-scraper-container--global-listingrow-mysql-only), the write ownership is split:

- The **scraper** container (see [`backend/app/scraper/`](../backend/app/scraper/)) is the sole writer of `ListingRow` and `PhotoRow`. Per [ADR-020](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing), it keys listings by `f"{source}:{external_id}"` (e.g. `"wg-gesucht:12345678"`, `"tum-living:cf76dd26-…"`, `"kleinanzeigen:3362398693"`) — one row per listing per source, dedup-collision-proof across sources.
- The **backend** container writes only `UserListingRow` (per-user), `UserActionRow` (per-user), and everything user / profile / credentials related.

A `UserListingRow` row is the **user ↔ listing membership record**: the frontend's matched-listings view ([`list_user_listings`](../backend/app/wg_agent/repo.py)) joins `userlistingrow JOIN listingrow`, so a listing only appears in a user's dashboard after the matcher has scored it (including `score=0.0` veto rows). Listings that disappear from a source eventually fall out of the matcher's working set via the per-stub freshness stop ([ADR-026](./DECISIONS.md#adr-026-drop-the-deletion-sweep-stop-pagination-on-the-first-stale-stub)) — the row stays in the pool with its last known `scraped_at`, but no new `UserListingRow` rows are written for it once it's outside `SCRAPER_MAX_AGE_DAYS`.

## Entities

### UserRow

Local account: unique username plus demographics. One row per person using the app.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `username` | `str` | Primary key; chosen handle. |
| `email` | `Optional[str]` | Unique, indexed. Optional on signup; used for duplicate detection on `POST /api/users` + `PUT /api/users/{username}`. |
| `age` | `int` | 16–99 in API validation. |
| `gender` | `str` | Stores `Gender` enum value as string. |
| `created_at` | `datetime` | UTC timestamp when the row was inserted. |

- **SET**: [`repo.create_user`](../backend/app/wg_agent/repo.py) on `POST /api/users`; [`repo.update_user`](../backend/app/wg_agent/repo.py) on `PUT /api/users/{username}`.
- **READ**: [`repo.get_user`](../backend/app/wg_agent/repo.py) for profile fetch and as a guard on nested routes; [`repo.get_user_by_email`](../backend/app/wg_agent/repo.py) for duplicate-email detection.

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
| `rescan_interval_minutes` | `int` | Used when spawning the per-user matcher loop. |
| `schedule` | `str` | `"one_shot"` or `"periodic"`. |
| `updated_at` | `datetime` | Bumped on upsert. |

- **SET**: [`repo.upsert_search_profile`](../backend/app/wg_agent/repo.py) on `PUT /api/users/{username}/search-profile` (also auto-spawns the per-user agent as a side effect).
- **READ**: [`repo.get_search_profile`](../backend/app/wg_agent/repo.py) for the matcher pass, agent resumption, and `GET` search profile.

### ListingRow

**Global** normalized listing — one row per `(source, external_id)` across `wg-gesucht`, `tum-living`, and `kleinanzeigen`. Single-column PK on `id`; the scraper container is the sole writer.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | Namespaced listing id `f"{source}:{external_id}"` per [ADR-020](./DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing). Examples: `"wg-gesucht:12345678"`, `"tum-living:cf76dd26-0bbb-45af-b74d-14f5face8ba0"`, `"kleinanzeigen:3362398693"`. Sole primary key. The source is recoverable from any code path via `id.split(":", 1)[0]`. |
| `url` | `str` | Canonical detail URL as string (column type: `TEXT` so wg-gesucht / Kleinanzeigen long slug URLs aren't truncated). |
| `title` | `Optional[str]` | `TEXT`. |
| `price_eur` | `Optional[int]` | |
| `size_m2` | `Optional[float]` | |
| `wg_size` | `Optional[int]` | Total flatmates including the new tenant. wg-gesucht reads `(\d+)er WG`; Kleinanzeigen reads `Anzahl Mitbewohner` and adds 1; TUM Living leaves it `None` (the GraphQL API does not expose flatmate count). See [SCRAPER.md](./SCRAPER.md) for the per-source field maps. |
| `city` | `Optional[str]` | `TEXT`. Parsed from each source's locality block; fed into `brain.vibe_score`'s listing summary. |
| `district` | `Optional[str]` | `TEXT`. Used in `evaluator.hard_filter`'s avoid-districts veto. |
| `address` | `Optional[str]` | `TEXT`. Street + number when the source exposes it (wg-gesucht, TUM Living); `None` for Kleinanzeigen, which only publishes PLZ + district. |
| `lat` | `Optional[float]` | wg-gesucht: `map_config.markers` script block → [`geocoder.geocode`](../backend/app/wg_agent/geocoder.py) fallback. TUM Living: `coordinates.x` (the GraphQL field IS the latitude). Kleinanzeigen: `<meta property="og:latitude">`. `None` when the source doesn't expose them on a particular listing. |
| `lng` | `Optional[float]` | Same per-source origin as `lat`; paired for commute-aware scoring. |
| `available_from` | `Optional[date]` | |
| `available_to` | `Optional[date]` | |
| `description` | `Optional[str]` | `TEXT` (the prior `VARCHAR(255)` silently truncated 2 KB+ bodies; widened in the multi-source rollout — see [SCRAPER.md "Migration verification"](./SCRAPER.md#migration-verification)). Filled after deep scrape: wg-gesucht `#ad_description_text`, TUM Living `furtherEquipmentEn`, Kleinanzeigen `#viewad-description-text`. |
| `furnished` | `Optional[bool]` | `True` / `False` / `None` (unknown). Feeds `evaluator.hard_filter` (weight-5 "must-have furnished" veto) and `evaluator.preference_fit`. |
| `pets_allowed` | `Optional[bool]` | Same shape as `furnished`; feeds the same evaluator paths for weight-5 "must-have pets" vetoes. |
| `smoking_ok` | `Optional[bool]` | Same shape; feeds the smoking-preference evaluator paths. |
| `languages` | `Optional[list[str]]` | Languages spoken in the WG (wg-gesucht only; TUM Living and Kleinanzeigen don't expose this). JSON column. |
| `kind` | `str` | `"wg"` (room in a shared flat) or `"flat"` (whole apartment). Indexed. Set by each per-source scraper from the search vertical it iterated; the detail page is never re-parsed for kind. Defaults to `"wg"` for legacy rows. Per [ADR-021](./DECISIONS.md#adr-021-listing-kind-as-a-first-class-column); honored at read time by `repo.list_scorable_listings_for_user(mode=...)` so the matcher respects the wizard's `SearchProfile.mode`. |
| `scrape_status` | `str` | `"stub"` (partial, don't score yet), `"full"` (description + coords present), or `"failed"` (scrape exception). Indexed. The matcher only iterates `status="full"` rows. (A legacy `"deleted"` value may still exist on rows tombstoned by the pre-[ADR-026](./DECISIONS.md#adr-026-drop-the-deletion-sweep-stop-pagination-on-the-first-stale-stub) sweep; new code never writes it.) |
| `scraped_at` | `Optional[datetime]` | UTC timestamp of the last `upsert_global_listing`. Indexed so the scraper can cheaply find stale rows via `list_stale_listings`. |
| `scrape_error` | `Optional[str]` | `TEXT`. Free-text breadcrumb (often a Python traceback) when `scrape_status == "failed"`. |
| `first_seen_at` | `datetime` | Preserved across upserts. |
| `last_seen_at` | `datetime` | Bumped on each upsert. |
| `deleted_at` | `Optional[datetime]` | **Deprecated.** Pre-[ADR-026](./DECISIONS.md#adr-026-drop-the-deletion-sweep-stop-pagination-on-the-first-stale-stub) the scraper's per-source deletion sweep stamped this when a listing missed `SCRAPER_DELETION_PASSES` consecutive search passes. New code never reads or writes it; the column is kept on the schema only for backward compatibility with rows already tombstoned. |

- **SET**: [`repo.upsert_global_listing`](../backend/app/wg_agent/repo.py) from [`ScraperAgent._scrape_and_persist`](../backend/app/scraper/agent.py). The matcher never writes this table.
- **READ**: [`repo.list_user_listings`](../backend/app/wg_agent/repo.py) (joined through `UserListingRow`), [`repo.list_scorable_listings_for_user`](../backend/app/wg_agent/repo.py) in the matcher (with `mode` filter for [ADR-021](./DECISIONS.md#adr-021-listing-kind-as-a-first-class-column)), direct `session.get(ListingRow, listing_id)` in [`api._get_listing_detail`](../backend/app/wg_agent/api.py).

### PhotoRow

Ordered image URLs for a listing drawer. Composite PK `(listing_id, ordinal)`; photos live alongside the global listing.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `listing_id` | `str` | Part of PK; FK → `listingrow.id`. |
| `ordinal` | `int` | Part of PK; zero-based order. |
| `url` | `str` | Absolute image URL. |

- **SET**: [`repo.save_photos`](../backend/app/wg_agent/repo.py) from the scraper right after `upsert_global_listing`.
- **READ**: `select(PhotoRow)...` in [`api._get_listing_detail`](../backend/app/wg_agent/api.py), `_cover_photo_url` inside `repo._listing_from_row`.

### UserListingRow

Latest scorecard payload per `(username, listing_id)` — also the user ↔ listing membership record. Every listing the per-user matcher has touched for a user has a row here, including vetoed listings (`score=0.0`, `veto_reason` set).

| Field | Type | Notes |
| ----- | ---- | ----- |
| `username` | `str` | PK part; FK → `userrow.username`. |
| `listing_id` | `str` | PK part; FK → `listingrow.id`. |
| `score` | `float` | 0..1 in domain; stored as given. |
| `reason` | `Optional[str]` | Human-readable explanation. |
| `match_reasons` | `JSON` / `list` | |
| `mismatch_reasons` | `JSON` / `list` | |
| `travel_minutes` | `Optional[JSON]` | Fastest `{mode, minutes}` per `main_location.place_id` when commute data was available at score time. Shape: `{"<place_id>": {"mode": "BICYCLE", "minutes": 18}}`. Populated by `UserAgent.run_match_pass` from the full `commute.travel_times` matrix; read back by `_get_listing_detail` and re-keyed by label for the drawer. |
| `nearby_places` | `Optional[JSON]` | Persisted nearby-place facts for place-like preferences. Shape: `[{key, label, searched, distance_m, place_name, category}]`. Populated by `UserAgent.run_match_pass` from [`places.nearby_places`](../backend/app/wg_agent/places.py); read back by `_get_listing_detail` for the drawer's nearby-preferences section. |
| `components` | `Optional[JSON]` | Scorecard breakdown: `list[{key, score, weight, evidence, hard_cap?, missing_data}]`, one entry per `evaluator` component (price, size, wg_size, availability, commute, preferences, vibe). NULL on vetoed listings and on rows written before `components` existed. Rendered as per-component bars in `ListingDrawer`. |
| `veto_reason` | `Optional[str]` | Set when `evaluator.hard_filter` short-circuited evaluation (over budget, wrong city, avoid-district, etc.). Mutually exclusive with a populated `components` list; score is pinned at 0.0. |
| `scored_against_scraped_at` | `Optional[datetime]` | `ListingRow.scraped_at` at the moment the matcher produced this score. Lets the UI say "scored against listing data from N hours ago" and lets a future rescore path detect stale scores. |
| `scored_at` | `datetime` | |

- **SET**: [`repo.save_user_match`](../backend/app/wg_agent/repo.py) immediately after `evaluator.evaluate` in `UserAgent.run_match_pass`.
- **READ**: Joined in `repo.list_user_listings` via `_listing_from_row` (which rehydrates `components` via `_components_from_row`) and in `_get_listing_detail` (the detail endpoint also resolves `place_id` to `main_location.label` for the `travel_minutes_per_location` DTO field, rehydrates `components` via `_components_dto_from_row`, and returns `nearby_places` as `nearby_preference_places` for the drawer).

### UserActionRow

Append-only audit log for UI and debugging. `kind` is a plain string (not a DB enum) so new action types do not require migrations.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `Optional[int]` | Autoincrement primary key. |
| `username` | `str` | FK → `userrow.username`, indexed. |
| `kind` | `str` | [`ActionKind.value`](../backend/app/wg_agent/models.py). |
| `summary` | `str` | Short line for the log. |
| `detail` | `Optional[str]` | Optional stack or extra text. |
| `listing_id` | `Optional[str]` | FK → `listingrow.id` (nullable). Actions that reference a listing point into the global pool. |
| `at` | `datetime` | Timestamp. |

- **SET**: [`repo.append_user_action`](../backend/app/wg_agent/repo.py) from API agent-control paths and from `UserAgent` / `PeriodicUserMatcher`.
- **READ**: [`repo.list_actions_for_user`](../backend/app/wg_agent/repo.py); the SSE path also reloads actions from DB while draining the live queue.

## ER diagram

Every relationship below is declared as a SQL-level foreign key on MySQL via the SQLModel `Field(foreign_key=...)` annotations in [`db_models.py`](../backend/app/wg_agent/db_models.py), picked up by `SQLModel.metadata.create_all`. The scraper writes `ListingRow` + `PhotoRow`; the per-user matcher writes `UserListingRow` + `UserActionRow`. `UserListingRow` is the user ↔ listing membership record.

```mermaid
erDiagram
  UserRow ||--o| WgCredentialsRow : "optional"
  UserRow ||--|| SearchProfileRow : "one"
  UserRow ||--o{ UserListingRow : "scored"
  UserRow ||--o{ UserActionRow : "logs"
  ListingRow ||--o{ UserListingRow : "matched to users"
  ListingRow ||--o{ PhotoRow : "ordered urls"
  ListingRow ||--o{ UserActionRow : "referenced by"
```

## The three-layer rule (in detail)

```mermaid
flowchart LR
  UI["React (TS types in frontend/src/types.ts)"] --> DTO["API DTOs<br/>(Pydantic, in backend/app/wg_agent/dto.py)"]
  DTO --> Domain["Domain models<br/>(Pydantic, in backend/app/wg_agent/models.py)"]
  Domain --> Repo["repo.py<br/>(conversion boundary)"]
  Repo --> Tables["SQLModel tables<br/>(backend/app/wg_agent/db_models.py)"]
  Tables --> MySQL[("MySQL")]
```

### React ([`types.ts`](../frontend/src/types.ts))

The browser owns camelCase TypeScript types that mirror JSON after client-side normalization. Components and hooks never import SQLAlchemy or SQLModel. Network I/O goes through [`api.ts`](../frontend/src/lib/api.ts), which applies `toCamel` / `toSnake` so field names stay consistent with Python’s snake_case on the wire. This layer is disposable at build time: it has no direct knowledge of the table layout.

### API DTOs ([`dto.py`](../backend/app/wg_agent/dto.py))

Pydantic models such as `UserDTO`, `ListingDTO`, `ActionDTO`, and `UpsertSearchProfileBody` define the HTTP contract: snake_case field names in JSON, validation on input bodies, and explicit conversion helpers (`user_to_dto`, `listing_to_dto`, `upsert_body_to_search_profile`, …). Route handlers in [`api.py`](../backend/app/wg_agent/api.py) call these helpers rather than returning SQLModel rows. This keeps OpenAPI and static typing aligned with what the React client actually sends and receives.

### Domain models ([`models.py`](../backend/app/wg_agent/models.py))

`SearchProfile`, `Listing`, `UserProfile`, `AgentAction`, and related types describe agent semantics (scoring fields on `Listing`, action kinds). They are plain Pydantic models with **no** SQLModel mixins. [`brain.py`](../backend/app/wg_agent/brain.py), [`browser.py`](../backend/app/wg_agent/browser.py), and [`periodic.py`](../backend/app/wg_agent/periodic.py) consume and produce these types only.

### Rows ([`db_models.py`](../backend/app/wg_agent/db_models.py)) and [`repo.py`](../backend/app/wg_agent/repo.py)

`*Row` classes map 1:1 to tables. [`repo.py`](../backend/app/wg_agent/repo.py) is the only module that routinely converts between `*Row` instances and domain models (narrow public surface: `create_user`, `get_user`, `get_user_by_email`, `update_user`, `upsert_search_profile`, `get_search_profile`, `upsert_credentials`, `delete_credentials`, `credentials_status`, `upsert_global_listing`, `save_user_match`, `save_photos`, `list_user_listings`, `list_scorable_listings_for_user`, `list_stale_listings`, `append_user_action`, `list_actions_for_user`, `mark_listing_deleted`, `list_active_listing_ids`, `list_usernames_with_search_profile`, `row_to_domain_listing`, plus internal helpers). Exceptions: `api._get_listing_detail` reads `ListingRow` / `PhotoRow` / `UserListingRow` directly for the listing drawer endpoint.

## Example JSON for each entity

Values below are illustrative; timestamps are ISO-8601 strings as JSON would show after `model_dump(mode="json")`.

**UserRow**

```json
{
  "username": "lea",
  "email": "lea@example.com",
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

**ListingRow**

```json
{
  "id": "wg-gesucht:13115694",
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
  "kind": "wg",
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
  "listing_id": "wg-gesucht:13115694",
  "ordinal": 0,
  "url": "https://www.wg-gesucht.de/gal/…/thumb.jpg"
}
```

**UserListingRow**

```json
{
  "username": "lea",
  "listing_id": "wg-gesucht:13115694",
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

**UserActionRow**

```json
{
  "id": 42,
  "username": "lea",
  "kind": "evaluate",
  "summary": "Scored wg-gesucht:13115694: 0.82",
  "detail": null,
  "listing_id": "wg-gesucht:13115694",
  "at": "2024-01-02T05:02:15"
}
```

## Field lifecycle for `ListingRow` (scraper) and `UserListingRow` (per-user matcher)

**Scraper container** (sole writer of `ListingRow` + `PhotoRow`):

1. **Source dispatch** — [`ScraperAgent.run_once`](../backend/app/scraper/agent.py) iterates the registry from [`backend/app/scraper/sources/`](../backend/app/scraper/sources/) (selected by `SCRAPER_ENABLED_SOURCES`, default `wg-gesucht`). For each source, for each `kind` in the supported set ∩ `SCRAPER_KIND` (default `both`).
2. **Search stub** — `Source.search_pages(kind=..., profile=...)` yields one batch of domain `Listing` stubs per source page, sorted newest-first by the source URL (wg-gesucht `sort_column=0&sort_order=0`; kleinanzeigen `/sortierung:neuste/`; tum-living `orderBy: MOST_RECENT`). Stubs carry the namespaced `id` and the final `kind` (immutable downstream).
3. **Per-stub freshness stop** — Before doing any work for a stub, the agent compares `stub.posted_at` against `now - SCRAPER_MAX_AGE_DAYS`. If stale, the entire `(source, kind)` walk halts (newest-first sort guarantees the rest is also stale). Stubs without `posted_at` (kleinanzeigen — date is detail-only) skip this gate; the post-scrape variant in step 5 catches them.
4. **Refresh gate** — `ScraperAgent._needs_scrape` skips listings whose `scrape_status == 'full'` and whose `scraped_at` is within the source's `refresh_hours` (24h for wg-gesucht / Kleinanzeigen, 48h for TUM Living).
5. **Deep scrape + persist** — `Source.scrape_detail(stub)` fills description, address/district, photos, etc. On HTTP failure the stub is persisted with `scrape_status='failed'` and `scrape_error`. On block-page detection (each source declares its own `looks_like_block_page`), the unmodified stub is persisted with `scrape_status='stub'`. On success, `repo.upsert_global_listing` writes/merges `ListingRow` (including `kind`) with `scrape_status='full'` when description + coords are present (otherwise `'stub'`), preserving `first_seen_at` and bumping `last_seen_at` + `scraped_at`. `repo.save_photos` replaces `PhotoRow`s. Then the same per-stub freshness check runs against `enriched.posted_at` to halt the walk for kleinanzeigen.

**Backend container, per user** (matcher, writes only `UserListingRow` + `UserActionRow`):

1. **Candidate fetch** — `repo.list_scorable_listings_for_user(username, status='full', mode=sp.mode)` returns global listings not yet scored for this user, optionally filtered by `kind` when the user's `SearchProfile.mode != 'both'` (per [ADR-021](./DECISIONS.md#adr-021-listing-kind-as-a-first-class-column)).
2. **Log new_listing** — one `UserActionRow` per candidate via `repo.append_user_action(ActionKind.new_listing)`.
3. **Evaluate** — `evaluator.evaluate` runs `hard_filter`, all deterministic components, and the narrow `brain.vibe_score` LLM call on the in-memory `Listing` rehydrated via `repo.row_to_domain_listing`.
4. **Persist** — `repo.save_user_match` writes `UserListingRow` with `score`, components, veto, and `scored_against_scraped_at = row.scraped_at`. Vetoes still write a row so the UI can show the rejection reason.
5. **Re-read** — `GET /api/users/{username}/listings` calls `repo.list_user_listings`, which joins `UserListingRow JOIN ListingRow`. `GET /api/listings/{id}?username=` reads `ListingRow` by id and `UserListingRow` by `(username, id)`.

```mermaid
sequenceDiagram
  participant SA as ScraperAgent
  participant SRC as Source plugin (wg-gesucht / tum-living / kleinanzeigen)
  participant Matcher as UserAgent (matcher)
  participant Evaluator as evaluator.evaluate
  participant Brain as brain.vibe_score
  participant DB as MySQL

  loop per source × per kind in (kind_supported ∩ SCRAPER_KIND)
    SA->>SRC: search_pages(kind, profile)
    SRC-->>SA: Listing stubs page (newest-first, namespaced id + kind)
    alt stub.posted_at older than SCRAPER_MAX_AGE_DAYS
      SA->>SA: stop (source, kind) walk
    else fresh
      SA->>SRC: scrape_detail(stub)
      SRC-->>SA: enriched Listing
      SA->>DB: upsert_global_listing (ListingRow, status=full) + save_photos (PhotoRow)
      opt enriched.posted_at older than SCRAPER_MAX_AGE_DAYS (kleinanzeigen)
        SA->>SA: stop (source, kind) walk
      end
    end
  end

  Matcher->>DB: list_scorable_listings_for_user(username, mode=sp.mode)
  DB-->>Matcher: ListingRow candidates (kind filter)
  Matcher->>DB: append_user_action(new_listing)
  Matcher->>Evaluator: hard_filter + components
  Evaluator->>Brain: vibe_score (only if not vetoed)
  Brain-->>Evaluator: VibeScore
  Evaluator-->>Matcher: EvaluationResult
  Matcher->>DB: save_user_match (UserListingRow, scored_against_scraped_at)
  Matcher->>DB: append_user_action(evaluate) — "Scored" or "Rejected"
```
