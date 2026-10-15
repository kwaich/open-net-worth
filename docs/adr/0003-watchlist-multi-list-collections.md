---
number: 0003
title: Watchlist multi-list collections — flat-with-list_id, one idempotent migration choke
status: Superseded by 0009
date: 2026-06-17
deciders: Intel-redesign orchestration (Claude) + repo owner (async)
---

# 0003. Watchlist multi-list collections

## Context

The watchlist was a single flat list persisted to `watchlist.json` as `{items, config}`.
Users asked for **multiple named lists** ("My Stocks" vs "Watch later" vs "High conviction"),
managed identically by the UI and by Lucius. Two hazards shaped the design:

1. **Three writers touch `watchlist.json`** — `backend/services/watchlist.py`
   (`read_watchlist`/`write_watchlist`, used by `intel_agent`/`asset_detail`/AI tools),
   a byte-identical duplicate in the REST router `backend/api/watchlist.py`, and
   `WatchlistStore` (`backend/db/watchlist_store.py`, used by the `get/add_watchlist_item`
   tools). Any schema change risks one path writing the old shape over the new.
2. The file holds **real user-entered data** (topics, price alerts) and every save fires
   `core.io.fire_write_hook` → a checkpoint snapshot. A wrong migration is destructive.

## Decision

- **Schema: flat-with-`list_id`** —
  `{collections:[{id,name,description,is_auto_sync,kind,rule?,order,config?}], items:[{...,list_id}], global_config}`.
  Chosen over a separate `collections.json` store because the file already has three
  readers/writers; a second file would double the torn-write / lost-update surface.
- **One idempotent `_migrate(raw)->raw` choke in `WatchlistStore`.** It creates the default
  collection `{id:"default",name:"Watchlist",is_auto_sync:true}`, stamps `list_id="default"`
  on every item lacking one, and renames `config`→`global_config`. It is pure, total (never
  raises on readable-but-malformed input), and a no-op on already-migrated data. The REST
  router's duplicate `read/write_watchlist` is collapsed to delegate to the service so all
  paths funnel through the single choke. Persist-on-migrate fires exactly one save.
- **`default` cannot be deleted** (409 / `ValidationError`); deleting any other list
  **reassigns its items to `default`** (never orphans an item).
- **v1 implements `kind="manual"` only.** `kind="smart"` + `rule` are validated-but-inert in
  the schema so the smart-list feature lands later without a second migration.
- **Auto-sync targets the default list only** in v1 (holdings sync into `default`, not every list).
- **Tools**: `get_watchlist`/`add_watchlist_item` gain an optional `list_name` (resolved
  case-insensitively to an id; unknown → default, never auto-creates). New
  `list_watchlist_collections` is **headless-allowed**; `create|update|delete_watchlist_collection`
  are **headless-forbidden** (omitted from `HEADLESS_ALLOWED_TOOLS`, default-DENY).

## Consequences

### Positive
- Multiple named lists with no second file and no new lock surface.
- `as_dict()` stays back-compat (`config` = `global_config`), so in-flight callers don't break.
- Migration is safe to re-run and soak-tested against a copy of live data.

### Negative / known edges
- Per-collection `config` is reserved-null in v1; the scheduler still reads one `global_config`
  (different scan intervals per list is a future change).
- `list_name` resolution silently falls back to default on an unknown name (avoids an AI typo
  spawning phantom lists) — the tool result notes the fallback.

## Alternatives considered
- **Separate `collections.json` keyed by FK** — rejected (doubles torn-write surface on a
  file with three writers).
- **Nested `{collections:[{...,items:[]}]}`** — rejected (re-keys every item; heavier migration;
  breaks the flat `query_items` helpers).

## Agent provenance
- **Decided by:** the Intel-redesign orchestration agent from a `solution-architect` RFC; owner async.
- **Lane / branch:** `feature/intel-redesign`.
- **Driving artifacts:** `backend/db/watchlist_store.py` (`_migrate`, collection CRUD),
  `backend/services/watchlist.py` (`resolve_list_id`, delegates), `backend/api/watchlist.py`
  (`/api/watchlist/collections`), `backend/ai/tools/watchlist.py`, `backend/core/headless_scope.py`.
- **Why this way (one line):** add lists with the cheapest safe migration on a thrice-written
  live-data file, funneling every path through one idempotent choke.
