---
number: 0009
title: Watchlist terminology rename (collection → watchlist) + membership consolidation + unique names
status: Accepted
date: 2026-06-19
deciders: Repo owner (interactive) + Claude (implementation)
supersedes: 0003
---

# 0009. Watchlist terminology, membership consolidation, and unique names

## Context

The watchlist sub-grouping feature (ADR 0003) shipped under the noun **"collection"**, but the
codebase ended up using **three names for one concept**: the entity layer says `collection`
(~125 mentions: `create_watchlist_collection`, `collections` data key, `/api/watchlist/collections`),
the membership + resolver layer says `list` (`list_ids`, `list_id`, `resolve_list_id`,
`add_to_list`/`remove_from_list`, the UI's "Create new list"), and the whole feature is the
"watchlist". The repo owner asked: *"why call it collection — isn't a watchlist already a list?"*

Three related problems surfaced together:

1. **Vocabulary inconsistency** — one concept, three names, is confusing for the user and the model.
2. **Redundant membership paths** — a ticker could be added to a second list via BOTH
   `add_watchlist_item(list_name=…)` (link-or-create) AND a newly-added
   `update_watchlist_item(add_to_list=…)`. Two ways to do one thing.
3. **Duplicate collection names were allowed**, making name-based resolution (`resolve_list_id`,
   which matches name OR id and returns the first match) silently ambiguous.

ADR 0003's hazards still apply: `watchlist.json` holds real user data (topics, price alerts),
has multiple readers (`services/watchlist.py`, `WatchlistStore`, the REST router), and every save
fires `core.io.fire_write_hook` → a checkpoint. Any schema/key change is destructive if mis-done.

## Decision

1. **Terminology: "watchlist" (plural watchlists).** A named group of tickers/topics is *a
   watchlist*; there is no separate container noun. This matches the broker convention
   (TradingView/Yahoo) and the owner's mental model. The default watchlist stays **"My holdings"**
   (auto-synced from portfolio holdings, `id="default"`, cannot be deleted).

2. **Clean break + ONE-TIME data migration, then NO back-compat at all.** Old tool names / REST
   paths are removed, not aliased. Stored `watchlist.json` was migrated **once** (`collections`→
   `watchlists`, each item's `list_ids`→`watchlist_ids`, plus a one-time de-dup of any legacy
   folded-equal names) and that migration code was then **deleted**. The store now has a single
   on-disk shape and no legacy-shape handling, no `list_id` promotion, no dedup: `_ensure_shape`
   only seeds a fresh install with the default watchlist. Rationale: this is a self-hosted
   single-user app — the migration ran once against the owner's live data (verified: 6 watchlists,
   55 items, zero legacy remnants), so keeping the migration/back-compat code would be permanent
   dead weight. The owner explicitly chose a clean break with no backward-compatibility.

3. **Membership consolidation (Option A).** Membership is owned by exactly two tools:
   `add_watchlist_item(watchlist=…)` (link an existing ticker into another watchlist, or create
   it) and `remove_watchlist_item(watchlist=…)` (drop from one watchlist by name-or-id; deletes
   the item if it was its last). `update_watchlist_item` is **settings-only** (alerts/news/label/
   note) and its description redirects membership intent to the two tools. This removes the
   `add_to_list`/`remove_from_list` params and the `set_list_membership` store method added
   earlier in the same session. Trade-off accepted: a "move" is now two calls, and the model is
   steered off `update_watchlist_item` for membership by description rather than capability.

4. **Unique watchlist names (case- and whitespace-folded).** Enforced at the `WatchlistStore`
   create/rename choke (`ConflictError` → tool `{ok:false}` / REST `409`). The default
   "My holdings" counts. Renaming a watchlist to its own current name is not a collision. The
   `_migrate` step also **de-duplicates pre-existing duplicate names** (suffixing `… 2`, `… 3`)
   so legacy data becomes unambiguous and an identity-rename on it no longer wrongly conflicts.
   Blank/whitespace-only names are rejected; stored names are folded on resolution (parity with
   the uniqueness check).

### Tool / surface mapping

| Before | After |
|---|---|
| `list_watchlist_collections` | `list_watchlists` |
| `create_watchlist_collection` | `create_watchlist` |
| `update_watchlist_collection` | `update_watchlist` |
| `delete_watchlist_collection` | `delete_watchlist` |
| `get_watchlist(list_name=…)` | `get_watchlist(watchlist=…)` — no arg = all items |
| `add_watchlist_item(list_name=…)` | `add_watchlist_item(watchlist=…)` |
| `remove_watchlist_item(list_id=…)` | `remove_watchlist_item(watchlist=…)` (name-or-id) |
| `update_watchlist_item(add_to_list/remove_from_list)` | **removed** (settings-only) |
| `/api/watchlist/collections` | `/api/watchlists` |
| data: `collections`, item `list_ids` | `watchlists`, item `watchlist_ids` |
| UI "Collection" / "Create new list" | "Watchlist" / "New watchlist" |

## Consequences

- **Breaking:** saved agents/skills/scheduled tasks referencing old tool names or the old REST
  path stop working until updated (accepted — clean break). The `test_char_tool_dispatch` frozen
  tool-name set, `headless_scope`, and `agent_caps` are updated to the new names.
- **Migration was the risk center, now retired.** The one-time migration was developed
  characterization-test-first (seed legacy JSON → assert migrated shape, dedup, single persist),
  applied to the live data, verified clean, and then removed along with its tests. The store now
  carries no legacy-shape code; a future fresh install just seeds the default watchlist.
- **Frontend** is renamed in lockstep (labels + API paths + types) and gains the missing 409
  feedback on name collisions; clears the UI Quality Gate (qa-e2e + ui-gate + screenshots).
- Supersedes **ADR 0003** (which named the default "Watchlist" and introduced the `list_id`/
  `collections` vocabulary).
