---
title: dividend_rate_annual gets a provenance ratchet (dividend_rate_source), write-guard only
status: Accepted
date: 2026-09-01
deciders: repo owner (bug report + course correction) + Claude agent (diagnosis + implementation)
---

# dividend_rate_annual gets a provenance ratchet (dividend_rate_source), write-guard only

> **Revised same-day, before this shipped.** The first pass of this decision also used
> `dividend_rate_source` to relabel the forecast tier — `"declared"` only when
> `dividend_rate_source=="manual"`, `"cached"` otherwise. The repo owner pushed back
> immediately on seeing it live: there is no more-authoritative source for a dividend rate
> than the price provider (yfinance's `dividendRate` already **is** the company's
> current/announced rate, just relayed through a vendor) — a user retyping the identical
> number into `update_instruments` doesn't add real confidence, so gating the "declared"
> label on that action was a distinction without a difference, and it made almost every
> real holding read as "unconfirmed" indefinitely (nobody manually re-enters a dividend
> rate in normal use). That labeling split has been REVERTED:
> `forward_rate_for_position()` again labels `"declared"` for any present
> `dividend_rate_annual`, regardless of `dividend_rate_source`. The `"cached"` tier is
> dead code again (as it was before this ADR, now for a considered reason instead of an
> MVP shortcut) and no longer appears in the frontend's legend. What's described below as
> the "Decision" — the field, the ratchet, the write-guard behavior — is UNCHANGED and
> still shipped; only the "Consuming site" labeling logic reverted. See `backend/services/
> income.py::forward_rate_for_position` for the current (reverted) logic.

## Context

The Investments → Income sub-tab shows a "Declared rate" (green dot) badge on a holding
whenever `forward_rate_for_position()` finds a non-null `instrument.dividend_rate_annual`
and labels the tier `"declared"`. In practice this field is written by TWO different
callers that share it with no way to tell them apart:

1. **System enrichment** — `services/prices._backfill_dividend_rates()` (hot-path,
   opportunistic, from `yf.Ticker(...).info["dividendRate"]`) and
   `services/prices.enrich_dividend_meta()` (ADR 0020's daily scheduled job, same
   underlying yfinance fields) — both write `dividend_rate_annual` unconditionally,
   silently overwriting whatever was there before, including a user-set value.
2. **User override** — the only user-facing write path is the `update_instruments` AI
   tool → `services/instruments.update_instrument()`, which lists
   `dividend_rate_annual` as a plain whitelisted metadata field with no distinguishing
   marker.

Because both paths write the identical field, `forward_rate_for_position()` had no way
to know which one produced the current value, so it MVP-labeled the tier `"declared"`
whenever the field was non-null — regardless of provenance. Since the enrichment job
runs on essentially every yahoo-priced dividend payer, nearly every such holding showed
the green "Declared rate" dot even though no human ever confirmed the figure. This is
the exact conflation this codebase already solved once, for `sector`/`region`, via
`classification_source` (`backend/models/instrument.py`, `backend/services/
classification.py`, `backend/api/instruments.py::set_classification`) — a companion
provenance string with a one-way ratchet, enforced by the enricher refusing to touch
anything at or above the user tier.

A second, coupled defect the same conflation caused: because enrichment overwrote the
field unconditionally, a user-set `dividend_rate_annual` (e.g. correcting a stale or
missing yfinance figure) would be silently clobbered by the next price refresh — with
no way to protect it. Fixing the label without also fixing this write-side bug would
leave "declared" meaning nothing in practice (a user-set rate wouldn't survive to be
displayed as declared for long).

## Decision

We will add `dividend_rate_source: str = ""` to `Instrument` (NOT to
`InstrumentCreate` — same asymmetry as `classification_source`/`region`, which are also
absent from the resolve-time creation subset; nothing creates an instrument with a
dividend rate pre-attached today). A **two-tier** ratchet is enough here — unlike
`classification_source`'s four tiers (`""` → `heuristic` → `source` → `manual`), a
dividend rate has no heuristic/source distinction, just "did a human confirm it, or did
enrichment write it":

```
"" (unset / pre-migration) → "enriched" (system-written) → "manual" (user-confirmed)
```

**Write sites**, mirroring `enrich_instrument_classification()`'s ratchet-respecting
skip and `set_classification()`'s stamp exactly:

- `_backfill_dividend_rates()` and `enrich_dividend_meta()` (`services/prices.py`) skip
  writing `dividend_rate_annual` on any row already at `dividend_rate_source=="manual"`,
  and stamp `dividend_rate_source="enriched"` on every row they DO write. (In
  `enrich_dividend_meta`, only the rate itself is ratchet-guarded — the other dividend
  calendar fields it refreshes, e.g. `next_ex_date`, have no manual-override provenance
  of their own and continue to refresh unconditionally.)
- `services/instruments.update_instrument()` stamps `dividend_rate_source="manual"` in
  the SAME store write whenever a caller passes a non-null `dividend_rate_annual` — this
  is the only user-facing path for the field, so an explicit value is by definition
  user-confirmed. No new tool parameter; the AI tool's field description
  (`_INSTR_DIVIDEND_DESC` in `backend/ai/tools/portfolio.py`) is corrected to say the
  rate will no longer be silently overwritten, instead of warning that it might be.

**Consuming site (REVERTED, see notice above)** — `services/income.forward_rate_for_position()`
was changed to label the instrument-rate tier via
`"declared" if inst.get("dividend_rate_source") == "manual" else "cached"`, then reverted
back to unconditional `"declared"` for any present `dividend_rate_annual` the same day,
before shipping. `dividend_rate_source` is consumed nowhere else — it exists purely to
guard the write sites above. No other tier's label changed (`coupon`, `accretion`,
`trailing`, `none`).

No Alembic migration is needed: `instruments` is a `payload_table` (`backend/db/
tables.py`) — the whole instrument dict round-trips as one JSON `payload` column, not
per-field DB columns. `dividend_rate_source` is added purely at the store-factory layer
(`InstrumentStore._make_instrument` + a `_coerce_dividend_rate_source` normalizer,
identical in shape to `_coerce_classification_source`), the same way
`classification_source`, `bond_terms`, and `constituents` were all added previously with
zero migrations. (The task brief that kicked off this change assumed a migration was
required — checking the actual precedent it named turned up that assumption was wrong
for this table; noted here since it changes what "follow the precedent exactly" means
in practice.)

## Consequences

### Positive
- A user-set `dividend_rate_annual` now survives the next price refresh — the silent
  clobber bug this task's diagnosis surfaced as a side effect is fixed as a structural
  consequence of the ratchet, not a separate patch. This holds regardless of the labeling
  reversal above.
- Zero migration risk: purely additive at the JSON-payload layer, same blast radius as
  every prior instrument field addition.
- The forecast's "declared" label keeps meaning what a user actually reads it as ("we
  know this holding's current dividend rate"), rather than degrading to "unconfirmed" for
  virtually every real holding the moment the label was tied to a write action almost no
  one ever performs.

### Negative / accepted trade-offs
- The `dividend_rate_source` field and its write-guard now exist for exactly one
  narrow purpose — protecting a manual correction from a later enrichment overwrite —
  and are otherwise inert (not read by the forecast, not surfaced in the UI). That's a
  smaller payoff than the original two-tier confidence label this ADR set out to build,
  but the alternative (keeping the label split) actively misinformed users about
  well-established holdings' rates.
- Not verified against a live user portfolio (no access to real account data in this
  session, and CLAUDE.md prohibits reproducing against real rows) — verified instead via
  the unit-test suite (`tests/test_income_service.py`,
  `tests/test_prices_dividend_meta.py`, `tests/test_update_instrument.py`).

### Follow-ups
- None outstanding. The matching frontend work (3-tier legend: declared / fixed income /
  trailing, no "cached"/"price-source" tier) was built in parallel on this same branch.

## Alternatives considered

- **Add a third tier mirroring `heuristic`/`source`** (e.g. distinguish "enriched from
  `dividendRate`" vs. "enriched from `trailingAnnualDividendRate`"). Rejected: no
  consumer needs that distinction — the forecast only ever asks "system or human", and
  `classification_source`'s extra tiers exist because sector/region genuinely have a
  provider-vs-heuristic split that a dividend rate does not.
- **Infer provenance heuristically** (e.g. "declared" if the value doesn't match the
  currently cached yfinance figure). Rejected: fragile — a coincidental match (or a stale
  cache) produces a wrong label either direction, and it re-introduces exactly the kind
  of guessed fact `docs/adr/2026-08-29-instrument-quote-currency-is-never-fabricated.md`
  argues against for a different field. An explicit, ratchet-tracked write is the only
  honest source of truth.
- **Add the field via an Alembic migration**, as the initiating task brief specified.
  Rejected once the precedent it named was actually read: `instruments` is a
  `payload_table`, so no per-field DB column exists to migrate; a migration here would
  be a no-op against a table that has no `dividend_rate_source` column to add.

## Agent provenance

- **Decided by:** Claude agent (backend-only implementation lane), following an explicit
  task brief that pre-diagnosed the root cause and named the `classification_source`
  precedent to mirror.
- **Lane / branch:** `fix/income-source-labels-and-colors` (backend half; a separate
  agent handled the matching frontend work on the same branch).
- **Alternatives weighed:** three-tier ratchet vs. two-tier (two-tier — no consumer needs
  the extra distinction); heuristic inference of provenance vs. explicit tracked writes
  (explicit — matches the fabrication-avoidance stance already set for
  `quote_currency`); Alembic migration vs. payload-layer addition (payload-layer, once
  `instruments`' actual table shape was confirmed).
- **Driving artifacts:** `backend/models/instrument.py`, `backend/db/
  instrument_store.py` (`_coerce_dividend_rate_source`), `backend/services/prices.py`
  (`_backfill_dividend_rates`, `enrich_dividend_meta`), `backend/services/instruments.py`
  (`update_instrument`), `backend/services/income.py`
  (`forward_rate_for_position`), `backend/ai/tools/portfolio.py`
  (`_INSTR_DIVIDEND_DESC`); precedent read in full before writing anything:
  `backend/services/classification.py`, `backend/api/instruments.py::set_classification`.
- **Why this way (one line):** two provenances sharing one field is exactly the bug
  `classification_source` already solved for sector/region — reuse that ratchet shape
  instead of inventing a new mechanism for the same problem.
