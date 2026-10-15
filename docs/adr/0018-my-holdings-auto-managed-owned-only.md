# 0018 — "My holdings" is an auto-managed, owned-only, locked watchlist

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-06-20 |
| **Relates to** | ADR 0009 (watchlist membership), ADR 0014 (price-alert decouple) |

## Context

The default watchlist (`id: "default"`, `is_auto_sync: true`, displayed "My holdings") was a **hybrid**:
`sync_from_holdings` *added* owned tickers to it, but it also accepted **manual** add/remove and was the
silent fallback for any no-list add (including the old alert auto-follow, ADR 0014). Two problems:

1. It accumulated tickers the user doesn't own (e.g. a `GRAB` alert auto-followed it in) and never pruned
   them, so "My holdings" did not actually equal what you hold.
2. Removing such a ticker (or any item) from it could delete data, and the list's meaning was muddy —
   part-portfolio-mirror, part-manual-watchlist.

A prior decision (a code comment in `MonitorsWorkspace`) deliberately kept it a hybrid so a single-list
user could still add tickers. That trade-off is no longer needed: users create their own watchlists.

## Decision

"My holdings" becomes a **purely auto-managed mirror of owned positions**, and is **locked** against
manual mutation.

1. **Key rename `default` → `my_holdings`** (one constant `MY_HOLDINGS_ID`), with a one-time startup
   migration that renames the watchlist id and rewrites every item's `watchlist_ids`. Idempotent.
2. **Authoritative sync.** `sync_from_holdings` now reconciles My holdings to **exactly** the owned set
   (positions with `quantity > 0`): owned tickers added, non-owned **pruned**. A ticker also on a
   user watchlist only loses its My-holdings membership (the item survives); if My holdings was its only
   list, the item is dropped. This runs on the existing on-read sync path (GET /api/watchlist, the tab
   poll, the manual Sync, Lucius reads, intel scans) — no new event wiring; selling a ticker drops it on
   the next read, and the legacy GRAB-style leftovers self-clean.
3. **Locked against manual writes.** The store refuses adding to or removing from any `is_auto_sync`
   watchlist (mirrors the existing `delete_watchlist` refusal of the default list). Enforced once at the
   data layer so the REST API and the AI tools both inherit it.
4. **No-list adds no longer fall into My holdings.** A create with no watchlist is rejected with "choose
   or create a watchlist"; an explicit my_holdings target is rejected with "My holdings is auto-managed."
5. **Lucius knows.** The `add_watchlist_item` / `remove_watchlist_item` tool descriptions + the prompt
   state My holdings is auto-managed; on "watch X" with no list, Lucius adds to one of the user's
   watchlists or offers to create one — never My holdings.
6. **UI hides the controls.** The auto-sync list shows no add input, its membership checkbox in the
   asset-detail menu is disabled, and its items have no remove control (all keyed on `is_auto_sync`,
   which the UI already treats as the "system/DEFAULT" signal).

## Consequences

- "My holdings" now means precisely "what I own," self-healing on every sync. The `GRAB` leftover and any
  sold position drop off automatically.
- Manual watching is exclusively via user-created watchlists. A brand-new instance with only My holdings
  has no manual list until the user makes one (the "+ New watchlist" affordance) — the deliberate change
  from the prior hybrid.
- `default` as a watchlist id is retired; `my_holdings` is the auto-sync list's stable key.

## Alternatives considered

- **Keep the hybrid, only block the alert auto-follow:** rejected — it leaves the list semantically muddy
  and still accumulates manually-added non-owned tickers.
- **Seed a second general "Watchlist" default for no-list adds:** rejected (YAGNI) — users create their own
  lists; a silent second bucket re-introduces "things land somewhere I didn't choose."

## Agent provenance

Designed via brainstorming with the user after they observed an alert-added `GRAB` polluting My holdings
and asked that the list be owned-only and immutable, with the key renamed to `my_holdings`. Built E2E and
merged into the running branch.
