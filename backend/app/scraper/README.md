# Scraper container — multi-source contract

> The scraper container ([`./main.py`](./main.py), [`./agent.py`](./agent.py)) is the **sole writer** of `ListingRow` and `PhotoRow`. This file defines the contract every per-source scraper must respect; the per-site files document how each source meets it. Today only [wg-gesucht](./SOURCE_WG_GESUCHT.md) is wired; [TUM Living](./SOURCE_TUM_LIVING.md) and [Kleinanzeigen](./SOURCE_KLEINANZEIGEN.md) are documented but not yet implemented.

## Sources

| Prefix           | Site                                       | Recon doc                                          | Status         |
| ---------------- | ------------------------------------------ | -------------------------------------------------- | -------------- |
| `wg-gesucht`     | `https://www.wg-gesucht.de`                | [`./SOURCE_WG_GESUCHT.md`](./SOURCE_WG_GESUCHT.md) | live in v1 (WG vertical only) |
| `tum-living`     | `https://living.tum.de`                    | [`./SOURCE_TUM_LIVING.md`](./SOURCE_TUM_LIVING.md) | doc only       |
| `kleinanzeigen`  | `https://www.kleinanzeigen.de`             | [`./SOURCE_KLEINANZEIGEN.md`](./SOURCE_KLEINANZEIGEN.md) | doc only |

## Identifier convention (no double entries)

`ListingRow.id` is a single `str` primary key (see [`../wg_agent/db_models.py`](../wg_agent/db_models.py)). To make collisions across sources structurally impossible, every scraper writes a **namespaced** id:

```
ListingRow.id = f"{source}:{external_id}"
```

| Example external id (per source)                  | Persisted `ListingRow.id`                          |
| ------------------------------------------------- | -------------------------------------------------- |
| `12345678` (wg-gesucht numeric ad id)             | `wg-gesucht:12345678`                              |
| `cf76dd26-0bbb-45af-b74d-14f5face8ba0` (TUM UUID) | `tum-living:cf76dd26-0bbb-45af-b74d-14f5face8ba0`  |
| `3362398693` (Kleinanzeigen numeric ad id)        | `kleinanzeigen:3362398693`                         |

Each per-site doc specifies how its `external_id` is extracted (DOM attribute, URL regex, or JSON field).

**Why a string prefix and not a `(source, external_id)` composite key:** zero schema change (the existing `id: str` PK still works), zero migration of the API URLs / SSE payloads / frontend types, and `id.split(":", 1)[0]` recovers the source from any code path. The trade-off vs a real `source` column is documented in [`../../../docs/DECISIONS.md`](../../../docs/DECISIONS.md) (ADR pending).

### Dedup is automatic

`repo.upsert_global_listing` ([`../wg_agent/repo.py`](../wg_agent/repo.py)) does `session.get(ListingRow, listing.id)` first, then either updates the row in place (preserving `first_seen_at`, bumping `last_seen_at` / `scraped_at`) or inserts a new one. Because the id is the dedup key, **two scrape passes that surface the same listing produce one row, not two** — across all sources, automatically. No per-source dedup logic is allowed; everything goes through `upsert_global_listing`.

### Today's state vs target state

The `wg-gesucht` scraper currently emits the **bare** numeric id (e.g. `"12345678"`), not the namespaced form (`"wg-gesucht:12345678"`). The namespacing is a pending refactor — see the TODO list at the bottom of [`./SOURCE_WG_GESUCHT.md`](./SOURCE_WG_GESUCHT.md). Until that lands, all `ListingRow` rows are wg-gesucht and the prefix is implicit. New sources must emit the namespaced form from day one.

## Listing kind: WG vs full flat

Each scraped listing must declare what it represents — a room in a shared flat (`'wg'`) or an entire apartment (`'flat'`) — so the matcher can honor `SearchProfile.mode` ([`../wg_agent/models.py`](../wg_agent/models.py), `Literal["wg", "flat", "both"]`).

**Target schema:** add a `kind: Literal['wg', 'flat']` column to `ListingRow` and a matching field on the domain `Listing` model. Each per-source scraper sets `kind` from the search vertical it iterated — the listing-detail page does **not** need to be parsed to determine kind. Per-source mapping:

| Source           | `kind='wg'` selector                                                            | `kind='flat'` selector                                                  |
| ---------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `wg-gesucht`     | `/wg-zimmer-in-…` URL pattern (category `0`) — wired today                      | flat-vertical category id **not yet verified** — see TODO in source doc |
| `tum-living`     | GraphQL `housingType == "ROOM_IN_SHARED_APARTMENT"`                             | GraphQL `housingType == "APARTMENT"` (treat other values as `'flat'`)   |
| `kleinanzeigen`  | `/s-auf-zeit-wg/…/c199…` URL pattern                                            | `/s-mietwohnung/…/c203…` URL pattern                                    |

Sources that support both verticals iterate them in two passes per cycle (one with `kind='wg'`, one with `kind='flat'`). The matcher then filters by `SearchProfile.mode` when reading the global pool.

**Today's state:** `kind` does not exist as a column. `SearchProfile.mode` exists but only the evaluator branches on it; the scraper always treats listings as WGs. Adding the column is part of the same refactor as id namespacing.

## Per-source scraper contract

Each source's scraper module must expose, at minimum:

1. An async **search** function that accepts the equivalent of a `SearchProfile` and yields stub `Listing` objects. The stub must carry the **namespaced `id`**, the canonical `url`, and the `kind` it was scraped with. Other fields (`title`, `price_eur`, `address`, …) are best-effort stubs that the detail pass overwrites.
2. An async **detail** function that accepts a stub `Listing` and returns it enriched (description, photos, lat/lng, structured booleans, …). The function must **never re-key** the listing — `id` and `kind` are immutable from the moment the stub is created.
3. A **block-page detector** analogous to `_looks_like_block_page` in [`../wg_agent/browser.py`](../wg_agent/browser.py). When a fetch returns an anti-bot interstitial, the detail pass must return the unmodified stub (so the loop persists what it has) rather than crashing.

[`./agent.py`](./agent.py) holds the source-agnostic loop (`run_once` → search → diff → enrich → upsert → sweep deletions). When new sources land, the loop iterates them in sequence per pass and dispatches to the right `search`/`detail` pair based on the source token.

## Refresh, deletion, and pacing

These behaviors apply to **every** source — the per-site docs only specify the source-specific constants:

- **Refresh:** a listing is re-fetched only if `scrape_status != 'full'` or `scraped_at < now - SCRAPER_REFRESH_HOURS` (`ScraperAgent._needs_scrape`). Tune the refresh window in [`./SOURCE_*.md`](./) per source — TUM Living can tolerate 48h, wg-gesucht 24h, Kleinanzeigen 24h.
- **Deletion sweep:** `ScraperAgent._sweep_deletions` diffs the search-result ids against `repo.list_active_listing_ids` and tombstones listings missing for `SCRAPER_DELETION_PASSES` consecutive passes. Same rule per source; the scoping to per-source actives lives in `repo` (a future change).
- **Pacing:** each source declares its own request-pacing constant (see "Anti-bot posture" in each per-site doc). The cross-source loop does not interleave sources within one pass — it iterates them sequentially so per-source pacing is local.

## Multi-source rollout (done; how to deploy)

Items 1–4 from the original "pending refactors" list are now landed (see [ADR-020](../../../docs/DECISIONS.md#adr-020-multi-source-listing-identifiers-via-string-namespacing) + [ADR-021](../../../docs/DECISIONS.md#adr-021-listing-kind-as-a-first-class-column) and [`docs/MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md)). The remaining work is one-shot DB surgery on the shared RDS, then flipping an env var:

1. **Stop the scraper + backend containers** so they don't race the namespacing UPDATE on `listingrow.id`.
2. **Run the idempotent migration script** ([`./migrate_multi_source.py`](./migrate_multi_source.py)). It widens the seven text columns to `TEXT`, adds the `kind` column + index, namespaces every existing wg-gesucht id (with matching FK rewrites in one transaction), and flips every `'full'` row to `'stub'` so the next pass re-fetches the now-untruncated descriptions:
   ```bash
   # Dry-run first.
   cd backend && venv/bin/python -m app.scraper.migrate_multi_source --dry-run

   # Then for real (stops at any error and rolls back the transaction).
   venv/bin/python -m app.scraper.migrate_multi_source
   ```
   The script is idempotent — re-running after a partial failure skips already-done steps. Use `--skip-rescrape` if you want to widen + namespace without forcing a full rescrape.
3. **Restart the containers.** With the default `SCRAPER_ENABLED_SOURCES=wg-gesucht`, behavior is unchanged. Set `SCRAPER_ENABLED_SOURCES=wg-gesucht,tum-living,kleinanzeigen` to opt in to the new sources.

After ~2 hours of cycling at default cadence, [`MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md) G9 is satisfied: previously-truncated descriptions are progressively replaced with the full `parse_listing_page` output.

## See also

- [`./SOURCE_WG_GESUCHT.md`](./SOURCE_WG_GESUCHT.md), [`./SOURCE_TUM_LIVING.md`](./SOURCE_TUM_LIVING.md), [`./SOURCE_KLEINANZEIGEN.md`](./SOURCE_KLEINANZEIGEN.md) — per-site recon and field maps.
- [`../../../docs/WG_GESUCHT.md`](../../../docs/WG_GESUCHT.md) — original wg-gesucht site recon.
- [`../../../docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md) — runtime shape of the scraper container alongside the FastAPI matcher.
- [`../../../docs/DATA_MODEL.md`](../../../docs/DATA_MODEL.md) — `ListingRow` columns and the three-layer rule.
- [`../../../docs/DECISIONS.md`](../../../docs/DECISIONS.md) — ADR log; multi-source ADRs pending.
