---
title: Lots get the historical-rate lock — realized gains convert each leg at its own date
status: Accepted
date: 2026-07-15
deciders: orchestrating agent (Claude) + repo owner (investment data-structure review, 2026-07-15)
---

# Lots get the historical-rate lock — realized gains convert each leg at its own date

## Context

The ledger already has a historical-rate lock (ADR `2026-07-09`): a transaction's
`base_amount` is stamped at write time and TRUSTED while its `base_currency` matches the
current base — a completed foreign transaction's base value is a historical fact, not
revalued as the market drifts. The lot layer never got the same treatment:

- `compute_disposal` converted a consumed lot's basis into the proceeds currency at the
  **current** rate, and `realized_pnl` re-converted every event's native gain to base at
  the **current** rate on every read (its docstring cited `txn_base_amount` while
  implementing the pre-lock semantics). A realized gain on an SGD-bought, USD-sold
  holding therefore drifted with today's FX forever.
- The industry/tax standard (IRS, HMRC, ATO; Sharesight/IBKR reporting) converts **each
  leg at its own transaction date**: cost at the acquisition-date rate, proceeds at the
  disposal-date rate. The difference between that and a single-rate conversion is the FX
  gain/loss on the basis — real money the old method silently dropped.
- Owner directive (2026-07-15): the data model must be accurate enough to support broker
  and exchange integrations; store facts once, at the moment they're true.

## Decision

We will stamp FX facts at both lot legs' write time and trust them on read:

1. **Acquire** (`apply_lot_change` type=acquire): every lot is stamped with `fx_rate`
   (the exact cost_currency→base multiplier in effect at write time, from a new
   `_rate_between` helper that avoids `to_base`'s 2-dp amount rounding) and
   `base_currency`. Write-time == acquisition-date under prompt entry — the same
   accepted approximation the ledger uses. Synthetic `opening` lots (unknown dates)
   bypass the choke and stay deliberately unstamped.
2. **Dispose** (`compute_disposal`): when EVERY consumed lot has a known cost and a
   trusted stamp (`fx_rate` present, `base_currency` == current base), the event's
   `realized_gain_base` is the per-leg figure: net proceeds at the disposal-date rate
   minus Σ basis × each lot's acquire-rate. Otherwise it falls back to converting the
   native gain at the disposal rate (the pre-lock number). Each consumption line carries
   an audit copy `fx_rate_acquire`. The native `realized_gain` (proceeds currency) is
   unchanged.
3. **Read** (`realized_pnl`): the stored `realized_gain_base` is TRUSTED while the
   event's `base_currency` matches the current base; only a base-currency change or a
   legacy/unstamped event triggers a recompute from the native gain at current rates.

No data migration: legacy lots/events simply lack stamps and keep the old behavior.

## Consequences

### Positive
- Realized base gains stop drifting with today's FX; the FX component of a
  cross-currency gain is captured, matching what a tax authority or broker statement
  would report.
- The lot layer now follows the same mental model as the ledger — one rule
  ("stamped facts are trusted while the base matches") across both money spines.
- Facts-not-verdicts compatible: the stamps are facts; a jurisdiction that wants the
  "local gain at disposal rate" method can still derive it from the same stored fields.

### Negative
- The native gain × disposal rate no longer necessarily equals `realized_gain_base` on
  stamped events (the difference IS the basis FX gain) — a UI showing both numbers side
  by side must expect that.
- Backdated entries stamp today's rate, not the true historical rate (same accepted
  limitation as the ledger); a future dated-FX lookup could tighten this.
- Mixed books (stamped + legacy events) sum two methodologies into one total until old
  events age out or are re-derived.

### Follow-ups
- Tax engine (`services/tax.py`) consumes `realized_pnl` and inherits the lock
  automatically; revisit if a jurisdiction needs the split FX-gain component broken out.
- Consider a dated-FX table so backdated trades can stamp their true-date rate.

## Alternatives considered
- **Keep recomputing at current rates** — rejected: provably wrong for tax reporting and
  drifts forever; contradicted the ledger's own ADR.
- **Convert basis at disposal-date rate (single-rate, stamped)** — rejected as the only
  method: drops the basis FX gain; kept as the fallback for unstamped lots.
- **Full dated-FX lookup table (fetch historical rates per acquisition date)** —
  deferred: new data dependency and backfill problem; write-time stamping gets the
  prompt-entry case exactly right today and the table can be added later without
  changing the schema.

## Agent provenance
- **Decided by:** orchestrating agent (Claude Fable 5) during the 2026-07-15 investment
  data-structure hardening (`lane/inv-hardening`), following the owner's "fix all of
  them / industry-accurate model" directive.
- **Driving artifacts:** investment data-structure review (this session); ADR
  `2026-07-09` historical-rate lock (ledger); `docs/review/inv1-tax-lots.md`.
- **Why this way:** the ledger had already settled the same question; extending its
  rule to lots is one mental model instead of two, with zero migration.
