# 2026-07-31 — Unvested equity (bucket="locked") is excluded from net worth

**Status:** Accepted
**Owner:** the maintainer (design approved in-session; the exclusion rule itself was specified,
not discovered — see Context)
**Touches:** `backend/services/ledger.py` (`_market_value_base`, `net_worth`, `_breakdown_as_of`,
new `_unvested_value_base`/`_locked_row_value`), `backend/services/portfolio_history.py`
(`holdings_as_of`, `holdings_series`, new `_locked_position_ids`), `backend/ai/tools/accounts.py`
(`get_net_worth`), `backend/ai/tools/portfolio.py` (`get_net_worth_history`),
`backend/ai/prompts.py` (system-prompt portfolio snapshot)

## Context

`backend/models/position.py` defines `Bucket = liquid | contingent | locked`.
`backend/services/equity.py` mints a `bucket="locked"` position holding a grant's UNVESTED
units (`_create_locked_position`); each vest moves units into a `bucket="liquid"` sibling
position. A `bucket="locked"` position is **not owned** — it is forfeited if the grantee
leaves the company before it vests — but a prior audit found the net-worth fold counted it as
owned anyway:

- `ledger._market_value_base()` (feeds `net_worth()`) summed every `positions_service.hydrated()`
  row with a market `asset_class`, with no bucket check.
- `ledger._breakdown_as_of()` (feeds `net_worth_history()`) banded market rows by `asset_class`
  only, same gap, in both its rows-path branches.

Every other bucket-aware module already filtered correctly — `macro_exposure.py`,
`risk_signals.py`, `asset_detail.py` all gate on `bucket == "liquid"` (or explicitly split
liquid/locked); the frontend's `netWorth.tsx` bucket bar (`classBreakdown`) already reads the
real per-position `bucket` field correctly. Net worth was the outlier.

On a representative book this wrongly inflated net worth by **≈S$50,000** — an ONWAI grant of
2400 units, tranche `2026-06-15` vested (600.00 units → a liquid position), the remaining
1800.00 units sitting in a locked position that `_market_value_base`/`_breakdown_as_of` counted
as owned. Their separately-held Meridian Bank ONWAI position (`pos_6d7185670000`, 1500 units, `liquid`) is
a genuinely, fully-owned holding and must be unaffected by this change.

**Scope, deliberately narrow.** The owner will design lockup / "vested-but-not-sellable"
modeling (a `sellable_from` field, or real semantics for the currently-unpopulated `contingent`
bucket) separately, later. This decision is ONLY: unvested (`locked`) is excluded from net
worth; vested-but-locked-up equity is a future, unrelated feature. `services/equity.py`'s vest
path is untouched.

### A second, deeper leak found while auditing (not in the original bug report)

`ledger._breakdown_as_of()`'s lot-backed bands (equity/crypto/commodity, for the common case
where `net_worth_history()` has price history — i.e. every "today" point) don't read
`rows`/`bucket` at all; they come from `portfolio_history.portfolio_value_series()`, which
reconstructs quantity by sweeping **`LotStore().all()` aggregated by `instrument_id`**, with no
join back to the owning position's `bucket`. `equity.py`'s current code never lot-backs a locked
position (`_create_locked_position`'s comment: "no lot — no acquired_at"), so in the common case
this path was already accidentally correct. But this book's data has an "opening" lot
(`4438a071…`, `original_qty=2400`, `remaining_qty=1800.00`, no `acquired_at`) attached directly
to the locked position (`position_id=3301270b…`) — almost certainly a legacy artifact from
before that "no lot" rule existed, since `update_position()` refuses a direct quantity write
once any lot exists for a position, which is exactly what `_vest_apply`'s locked-quantity
decrement does on every vest. That stray lot was inflating `net_worth_history()`'s reconstructed
equity band by **more** than the current unvested value (removing it dropped the band by
≈S$118k, not ≈S$89k) — i.e. it was silently wrong beyond just this bug. It also means the
**next** vest on this grant (2026-09-15) would likely hit that same "quantity is lot-derived,
can't be hand-edited" guard and fail, unrelated to this fix.

Deleting that stray lot is a live-data repair, not a code change, and is explicitly **out of
scope for this change** (the "no destructive edits to shared live data" guardrail applies, and
the owner didn't ask for a data migration). Instead, `portfolio_history.py` is made
bucket-aware at the read path — it no longer *assumes* "locked positions never have lots" holds;
it enforces the exclusion directly. This both fixes the reported bug's full magnitude on a
representative book and is structurally correct even if equity.py's invariant is ever violated
again (by more legacy data, a future migration, or a bug).

## Decision

We will exclude every `bucket="locked"` position from every net-worth total and every
asset-class band, in all three read paths that fold market positions into net worth, and report
its value separately as `unvested_base` instead of dropping it silently:

1. **`ledger._market_value_base(base)`** skips `bucket="locked"` rows. A new
   **`ledger._unvested_value_base(base)`** sums exactly those skipped rows (mirror image, same
   `MARKET_ASSET_CLASSES` gate). **`ledger.net_worth()`** gains a top-level `unvested_base` field
   (sibling to `net_worth`/`breakdown`), computed via the new helper — `net_worth` itself is
   unchanged in shape, just smaller by the excluded amount.
2. **`ledger._breakdown_as_of()`** — both rows-path branches (`market_overlay` present or not)
   route a `bucket="locked"` row into a running `unvested_base` total via the new
   **`_locked_row_value()`** helper (same "absent when unpriced on this date" convention as
   every other band) instead of into a `classes` band. The returned dict gains
   `unvested_base` as a sibling of `classes`/`liabilities`/`accounts`/`net_worth` — **never**
   summed into `classes`, so `Σclasses + liabilities == net_worth` stays true (verified: this
   sum is exactly how `net_worth` is computed in that function). `net_worth_history()`'s
   docstring and the `get_net_worth_history` tool description are updated to name the new field.
3. **`portfolio_history.holdings_as_of()` / `holdings_series()`** gain a `locked_position_ids`
   parameter (defaults to resolving `PositionStore().query_positions(bucket="locked")`,
   injectable for tests like `lots`/`disposals` already are) and skip any lot or disposal event
   whose `position_id` is in that set. This is the fix for the "second, deeper leak" above —
   it's the single shared primitive behind `net_worth_history()`, `returns_service`,
   `risk_metrics`, and `daily_total_value`, so all four now consistently exclude unvested
   equity from reconstructed holdings, not just net worth.

The AI surface is updated to match: `get_net_worth`'s and `get_net_worth_history`'s tool
descriptions now say explicitly that unvested equity is excluded from net worth and reported
separately as `unvested_base`, and never to describe it as already-owned. `ai/prompts.py`'s
per-turn portfolio snapshot (the system-prompt "Total NW" line Lucius sees every turn) skips
`bucket="locked"` rows in its own `by_class` breakdown loop too — that loop reconciles to
`net_worth()`'s total by construction (per its own comment), so leaving it unfixed would have
made the prompt's per-class breakdown overstate the (now-correct) total it's supposed to sum to.
A new `unvested_total` is folded into a one-line "Unvested equity: … NOT part of Total NW" note,
shown only when non-zero.

**Frontend:** `netWorth.tsx`'s `classBreakdown()`/bucket bar already reads each row's real
`bucket` field (`buckets.liquid/contingent/locked`) independently of the backend fold this ADR
touches — it was already correct and needs no change. `usePortfolio.ts`'s `total` (the Overview
hero figure) is a **separate, pre-existing, frontend-only sum** over portfolio rows that was
never part of the audited bug report (which named `_market_value_base`/`_breakdown_as_of`
specifically) and does not go through `net_worth()`/`net_worth_history()` at all. Filtering it
here would be a second, undirected scope expansion into a widget the owner didn't flag — **left
alone deliberately**, flagged as a follow-up below rather than fixed silently.

## Consequences

### Positive
- Net worth (dashboard-equivalent `net_worth()`, `net_worth_history()`, and every AI tool/prompt
  surface that reads them) now matches what every other bucket-aware module already believed:
  unvested equity isn't owned. On a representative book this drops net worth by ≈S$89k
  (`net_worth()`) / ≈S$118k (`net_worth_history()`, which also picks up the stray-lot fix) — see
  Evidence.
- `unvested_base` is a genuinely useful, honestly-labeled planning figure (not silently dropped).
- The `portfolio_history.py` fix makes "unvested equity never counts" a structural read-path
  guarantee rather than an assumption resting on `equity.py` never lot-backing a locked
  position — it survives future data drift, not just future code correctness.
- A book with no equity grants (no `locked` positions) is a byte-for-byte no-op — pinned by
  `test_net_worth_unvested_base_zero_when_no_locked_positions`,
  `test_breakdown_as_of_no_locked_positions_unaffected`,
  `test_holdings_as_of_no_locked_positions_unaffected`.

### Negative
- `returns_service`/`risk_metrics`/`daily_total_value` (all built on
  `portfolio_history.portfolio_value_series`) now also exclude any lot ever attached to a locked
  position, not just net worth — a deliberate, consistent side effect (unvested equity isn't
  "invested capital" for TWR/risk purposes either), but it widens this change's blast radius
  beyond the literal net-worth calculation the bug report named.
- `net_worth()` and `net_worth_history()`'s "today" net worth remain two independently-computed
  numbers that don't exactly agree (a pre-existing divergence, not introduced or fixed here) —
  see Follow-ups.
- The book still carries the stray "opening" lot on the locked position
  (`4438a071…`) — this change makes it harmless for net worth, but it remains a live-data
  anomaly (and a likely future vest-time crash) that nobody has repaired.

### Follow-ups
- **Data repair (not done here):** delete or re-date the orphan lot `4438a071-cdab-4c04-9fe9-
  81bb53efe047` on the owner's locked ONWAI position — it violates `equity.py`'s own invariant and
  will likely make the 2026-09-15 vest fail `update_position`'s lot-derived-quantity guard.
  Flagged for the owner; not touched here (live financial data, out of scope, no destructive
  action taken without explicit direction).
- **`usePortfolio.ts`'s `total`** (the Overview hero "Net worth" figure) sums portfolio rows
  client-side with no bucket filter at all — independent of every fix in this ADR. If the owner
  wants the Overview headline to also exclude unvested equity, that's a separate, explicitly
  scoped frontend change (not assumed here).
- **`net_worth()` vs. `net_worth_history()` reconciliation** — the two fold accounts/positions
  differently enough (property banding, lot-reconstruction vs. current-quantity) that their
  totals don't match exactly even before this change; not investigated further here.
- Lockup / "vested-but-not-sellable" modeling (a `sellable_from` field, or real `contingent`
  semantics) is explicitly the owner's next, separate piece of work.

## Alternatives considered

- **Fold `unvested_base` into a `classes` band** (e.g. `classes["unvested"]`) instead of a
  separate top-level field — rejected: `classes` is summed to produce `net_worth`
  (`Σclasses + liabilities == net_worth`, used by every `classes`-reading consumer), so anything
  landing in `classes` is, by construction, back inside net worth. The whole point is exclusion.
- **Assume `portfolio_history.py` never needs a bucket check** (rely on `equity.py`'s "no lot for
  a locked position" comment holding forever) — rejected once a representative book's data showed a
  counter-example; enforcing it at the read path is strictly more robust for the same amount of
  code, and it's the one shared primitive four other consumers already depend on.
- **Silently delete the stray orphan lot as part of this change** — rejected: this task's
  instructions are explicit that destructive edits to live financial data are not to be made
  out-of-band, and the owner didn't ask for a data migration; documenting it as a flagged
  follow-up preserves the decision for the owner instead of making it for them.
- **Also filter `usePortfolio.ts`'s `total`** — rejected as scope creep: the audited bug named
  two specific backend functions; the frontend hero figure is a different, independent
  computation the audit never flagged, and narrowing it would ripple into every other consumer
  of that hook's `rows`/`total` (allocation donut, investment-book split) with no explicit ask.

## Evidence

`tests/test_char_ledger_math.py::test_net_worth_excludes_locked_bucket_from_market_positions`,
`::test_net_worth_includes_contingent_bucket_in_market_positions`,
`::test_net_worth_unvested_base_zero_when_no_locked_positions`;
`tests/test_positions_stores.py::test_breakdown_as_of_excludes_locked_bucket_rows_path`,
`::test_breakdown_as_of_excludes_locked_bucket_market_overlay_path`,
`::test_breakdown_as_of_no_locked_positions_unaffected`;
`tests/test_portfolio_history.py::test_holdings_as_of_excludes_lot_on_locked_position`,
`::test_holdings_as_of_defaults_locked_position_ids_from_position_store`,
`::test_holdings_as_of_no_locked_positions_unaffected`,
`::test_holdings_series_excludes_lot_on_locked_position`.

Representative data (a cloned demo book, read-only verification, FX cache warm):
`net_worth()`: **S$132,000.00 → S$107,000.00** (Δ ≈ −S$89,000.00), `unvested_base` =
**S$50,000.00**, `breakdown.market_positions`: S$97,000.00 → S$71,500.00 (same delta modulo a
live price tick between the two script runs). Meridian Bank ONWAI position `pos_6d7185670000`
(1500 units, `bucket=liquid`) unchanged in both runs. `net_worth_history()` latest point:
**S$143,000.00 → S$108,000.00** (larger drop — the `portfolio_history.py` fix also removes the
stray orphan lot's contribution); `classes.equity`: S$104,500.00 → S$69,500.00.

## Agent provenance

- **Decided by:** implementing Claude agent (Sonnet 5), on `fix/approvals-never-expire`, from an
  owner-approved bug report + fix design (which bug to fix, which bucket rule, which fields to
  add were specified by the owner/orchestrator; this ADR's job was verifying scope, tracing every
  consumer, and implementing).
- **Lane / branch:** `fix/approvals-never-expire` (no dedicated worktree for this sub-task).
- **Alternatives weighed:** see Alternatives considered above — the `portfolio_history.py`
  bucket-awareness and the "don't touch live data" call were both judgment calls made mid-task
  when real-data verification revealed the stray lot; not part of the original bug report.
- **Driving artifacts:** owner bug report (net worth wrongly inflated by unvested RSUs, ≈S$50,000
  on a representative book); prior audit naming `ledger._market_value_base`/`_breakdown_as_of` as the two
  buggy call sites; `docs/adr/2026-07-31-liquid-net-worth-bands.md` (sibling same-session ADR,
  followed for `classes`-shape and real-data-verification conventions).
- **Why this way (one line):** exclude at every read path that folds positions into net worth
  (rows AND lots), report the excluded value honestly instead of dropping it, and leave live-data
  repair and the unrelated frontend `usePortfolio.ts` total as flagged, not silently fixed,
  follow-ups.
