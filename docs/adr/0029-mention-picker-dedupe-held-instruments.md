# 0029 — Mention picker: dedupe held instruments out of the Security list

- Status: Accepted
- Date: 2026-06-25
- Deciders: user + implementing agent
- Related: ADR 0026 (server-authoritative mention registry); `backend/services/mentions/providers.py` (`search_instrument`); `tests/test_mention_instrument_dedup.py`

## Context

The `@`-mention picker exposes two overlapping entity types for investments:

- **Holding** (`search_holding`) — the user's actual open positions, sourced from `positions_service.hydrated()`. Rows carry rich context: account name and quantity. Labeled "HOLDING" in the picker.
- **Instrument** (`search_instrument`) — every instrument record in `InstrumentStore`, owned or not. Intended for market/catalog lookups (watchlist tickers, instruments under research, sold positions). Labeled "SECURITY" in the picker.

Because `search_instrument` returned the full catalog unconditionally, any instrument the user owned appeared **twice** in the picker for the same name — once as a Holding (rich context) and once as a bare Security (catalog entry). Users found this confusing: picking the "wrong" row produced a less useful mention, and the duplicate made it harder to trust the picker's list.

## Decision

**Dedupe `search_instrument` against the live position set.** Before emitting rows, `search_instrument` builds `held_ids` from `positions_service.hydrated()` (keyed on `instrument_id`, the same field `search_holding` reads) and skips any instrument whose `id` is in that set. A held instrument therefore shows up **only** as a Holding; Securities are reserved for instruments the user does not currently hold.

Implementation site: the `search_instrument` function in `backend/services/mentions/providers.py`. The `positions_service` import mirrors the existing local-import pattern in `search_holding` and `search_allocation_slice` — no layering violation (`services/mentions` → `services/positions` is a peer-layer call within `services/`).

The two lists are now **complementary and disjoint**: Holding ∪ Security = every instrument the system knows about, Holding ∩ Security = ∅.

## Consequences

**Benefits**
- The picker is less cluttered: a user typing "AAPL" sees exactly one row (the Holding, with account+qty context) rather than two rows for the same stock.
- The two lists have clear, non-overlapping semantics: Holdings = positions you carry today; Securities = instruments you can look up or add.

**Trade-off to revisit**
After this change, you can no longer use the `@` picker to mention the *market/instrument-level view* of a name you already hold — the Security row for that instrument is gone. For example, `@AAPL` as a market-data subject (price history, analyst ratings) must come through the Holding row, whose resolver is position-scoped. If a user wants to discuss an instrument they hold from a pure market perspective, they lose the direct Security mention path. If this becomes a pain point, Alternative (a) below is the path back.

## Alternatives considered

**(a) Keep both rows but relabel** — "Your position in AAPL" vs "AAPL market view". Rejected for now: adds visual clutter and introduces a two-row decision every time; the market-level use case has not surfaced as a real need in practice. Revisit if users request it.

**(b) Drop Securities from `@` entirely** — rejected: unowned instruments (watchlist tickers, catalog lookups, potential additions) have genuine mention value; removing them would hollow out the Security provider.

---

**Agent provenance:** This decision was made mid-task by the implementing agent at the explicit direction of the user ("dedupe — a held instrument should appear ONLY as a Holding; 'Security' rows are reserved for instruments the user does NOT currently hold"). The choice of `instrument_id` as the key (not symbol) was made to be precise: two instruments with the same symbol would each be independently gated by their own id.
