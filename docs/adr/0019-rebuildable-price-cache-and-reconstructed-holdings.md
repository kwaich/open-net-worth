---
number: 0019
title: Rebuildable historical-price cache + reconstructed holdings for portfolio performance
status: Accepted
date: 2026-06-21
deciders: system-architect (Claude) + repo owner (the repo owner)
---

# 0019. Rebuildable historical-price cache + reconstructed holdings for portfolio performance

## Context

The Returns & Performance feature computes TWR / MWR / period-gain over a value series. Today that
series comes from `net_worth_history()`, which is **monthly-grained** and only carries market value
from `market_since` (the first *locally-recorded* price date) forward. Two `CLAUDE.md`/PRD invariants
bite here:

- **No-backfill (Pillar 2)** — "Market positions can't predate price collection (no backfill)"
  (`backend/services/ledger.py::net_worth_history`). A fresh book therefore has no pre-onboarding
  market value, so 7D / 1M / 1Y windows fail the sufficiency gate and show "Not enough price
  history".
- **Computed-not-stored** — derived monetary figures are never persisted; they are recomputed from
  source on every read (`backend/services/returns.py`). The value series also uses **current**
  quantities back-projected (the Pillar-2 quantity approximation), which is wrong whenever the user
  traded inside the window.

We need correct, graceful performance across fresh / mid / long books. The blockers are (a) no
historical price depth and (b) an inaccurate (current-qty) value path. The live data confirms the
quantity situation is recoverable: of 52 lots, 26 are dated `buy` lots and 26 are undated `opening`
lots, and **every non-zero position has at least a lot** — so historical quantity can be
reconstructed (opening = baseline pinned to inception, dated buys/disposals applied on date). The
RFC `.claude/rfcs/portfolio-performance-returns.md` works the full design; this ADR records the two
load-bearing decisions that bend stated invariants.

## Decision

We will:

1. **Persist a rebuildable on-disk cache of real historical daily closes** (tagged
   `source="backfill"`, distinct from `source="live"`) populated from the existing upstream history
   fetchers (`prices._yf_history` / `_cg_history`), refreshed at the trailing edge by the existing
   poller. This **bends the no-backfill (Pillar 2) invariant**: we store historical market data, but
   only *real upstream closes* — never a fabricated/derived value — and the cache is fully
   rebuildable from source, so it is a cache, not a new source of truth.

2. **Reconstruct historical holdings** via a shared `holdings_as_of` / `portfolio_value_series`
   primitive (`backend/services/portfolio_history.py`) that **both** `net_worth_history()` and the
   returns engine consume — replacing the current-qty back-projection. The **portfolio value floor
   stays at `inception`** (`min(account initial_balance_date, earliest txn)`); pre-inception
   performance remains unavailable unless the user later enters dated historical lots.

Derived figures (TWR/MWR/value series) remain **computed-not-stored** — only *raw prices* are
cached, so the computed-not-stored invariant is preserved for everything monetary/derived.

## Consequences

### Positive
- A real performance curve + figures on day one of a fresh book (clamp + relabel to "Since <date>"),
  and exact figures across all windows for dated-trade books.
- Returns are correct under buying/selling within a window (reconstructed quantities + cash-flow
  neutralization are finally consistent).
- One value/quantity model → the net-worth area chart and the return can never disagree.
- Reads stay off the network on the hot path (store-backed) and are debuggable (data on disk).

### Negative
- Bends Pillar 2: a future reader can no longer assume "no stored history". Mitigated by the
  `source="backfill"` tag and the cache being rebuildable, but the conceptual line moved.
- Adopting the shared primitive **changes existing net-worth numbers** (current-qty → reconstructed);
  requires characterization tests and intentional re-verification of the net-worth chart.
- New external-I/O surface for backfill (reuses existing `fetch_history` + net guards, so light) and
  on-disk cache size per held instrument.
- FX remains a **current-rate approximation** for historical points (flagged `fx_approximate`); not
  yet as-of-correct.

### Follow-ups
- Phase 0 (this ADR) → Phases 1–6 in `.claude/rfcs/portfolio-performance-returns.md`
  (shared primitive → adopt in `net_worth_history` → price cache/backfill → returns engine → UI →
  verify).
- Characterization tests for net worth before/after the primitive swap.
- A future ADR/RFC if/when we add as-of FX history or a dated-historical-lot data-entry feature
  (to push the portfolio floor before inception).
- Decide cache depth (5y default?) and rebuild trigger (manual endpoint vs automatic only).

## Alternatives considered

- **Live-only, computed-not-stored (no cache)** — fetch history on demand per request, cached only
  in-memory. Keeps Pillar 2 pristine, but puts a multi-instrument network fetch on every returns/
  chart request with rate-limit exposure on the hot path. Rejected for operational fragility.
- **Full first-class backfill** — persist historical closes as ordinary price history with no
  provenance distinction. Simplest downstream, but fully reverses Pillar 2 and blurs
  recorded-vs-fetched provenance. Rejected; the `source="backfill"` cache framing keeps the honesty.
- **Returns-path-only primitive** (leave `net_worth_history` on current-qty/monthly) — smaller blast
  radius, but two quantity models would visibly disagree between the chart and the return. Rejected
  for the drift.
- **Current-qty shortcut for short windows only** — cheap and ~exact when holdings are static, but
  silently wrong on a trade week and irreconcilable with cash-flow neutralization. Rejected once
  lot-reconstruction proved tractable (opening lots seed the baseline).

## Agent provenance

- **Decided by:** system-architect (Claude), in design-discussion mode with the repo owner.
- **Lane / branch:** `frontend/styling-pwa-ideal` (design phase; implementation will branch).
- **Alternatives weighed:** rebuildable cache (chosen) vs live-only vs full backfill; shared
  primitive (chosen) vs returns-path-only; reconstructed holdings (chosen) vs current-qty shortcut.
- **Driving artifacts:** `.claude/rfcs/portfolio-performance-returns.md`; live data audit of
  `lots.json` / `positions.json` (52 lots: 26 dated `buy`, 26 undated `opening`).
- **Why this way (one line):** store only *real, rebuildable* upstream prices and reconstruct
  holdings from the lot history, so performance is correct and graceful across all book ages without
  fabricating a single value.
