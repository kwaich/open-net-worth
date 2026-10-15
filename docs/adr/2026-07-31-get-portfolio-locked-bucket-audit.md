# 2026-07-31 — get_portfolio's bucket/entitlement blind spot, and a sibling-tool audit

**Status:** Accepted
**Owner:** the maintainer (bug + fix design specified in-session; this ADR's job was auditing the
sibling tools, implementing, and — mid-task — reconciling with a concurrent data-model change)
**Touches:** `backend/ai/tools/portfolio.py` (`get_portfolio`, `GetPortfolioInput`,
`get_returns`/`get_fixed_income` descriptions), `backend/services/positions.py` (new
`owned_vs_unvested_base`), `backend/services/fixed_income.py` (`summary`),
`backend/services/returns.py` (`cash_flows`), `backend/services/portfolio_history.py`
(`_locked_position_ids` → public `resolve_locked_position_ids`)

## Context

`docs/adr/2026-07-31-unvested-equity-excluded-from-net-worth.md` (same day, earlier) fixed
`get_net_worth`/`get_net_worth_history`: a `bucket="locked"` position (unvested equity-grant
units — forfeited if the grantee leaves, so not owned yet) was being folded into net worth as
if owned. That ADR named two call sites and fixed them, plus a second leak found while auditing
(`portfolio_history.py`'s lot-backed bands).

`get_portfolio` (`backend/ai/tools/portfolio.py`) was **not** touched by that fix and turned out
to carry the identical bug, in a different shape. Verified against a real production book: it
returns 35 position rows including one unvested-equity row (1,466.25 ONWAI units, ≈S$68k) with
**no totals field at all** — so a model answering "what's my portfolio worth?" has to sum
`price × quantity` across every row itself, with nothing stopping it from including the unvested
row. Worse, the tool's own description said returning every row "is the right call for a
portfolio total" with no mention that one row isn't owned. Same bug class, different tool: the
first fix closed two computed totals; this one is a raw-row tool where the model does the
summing, so a description gap is a live money bug just the same.

The task also asked for an audit of `get_returns` and `get_fixed_income` — the other two tools in
the same file that aggregate/total positions. Reading (not assuming from the name):

- **`get_fixed_income`** → `fixed_income_service.summary()` iterates
  `positions_service.hydrated()` filtering only on `asset_class == "bond"`, with **no
  entitlement check at all**. On a real production book this is a no-op today (the only unvested
  position is equity, a grant, not a bond) — but `add_positions` accepts an entitlement override
  freely on any `asset_class` (nothing in `positions_service.apply_position_change("add")`
  refuses it outside the grant machinery), so an unvested bond is reachable through the ordinary
  tool surface, not just legacy data. Real, if currently dormant, gap.
- **`get_returns`** → `portfolio_returns_with_benchmark` → `portfolio_returns`. Its **value
  series** (`value_series()` → `portfolio_history.portfolio_value_series` → `holdings_series`)
  was already entitlement-aware from the sibling ADR's `portfolio_history.py` fix — start/end
  value and TWR's chain correctly exclude an unvested position's lots. But its **cash flows**
  (`returns.cash_flows`, feeding both TWR's flow-neutralization and MWR/XIRR) walked
  `LotStore().all()` / `RealizedEventStore().query_events()` directly, with **no entitlement
  check**. `lots_service.apply_lot_change("acquire")` (the `acquire_lots` tool's choke point) has
  no such guard either — nothing stops a real `acquire_lots` call against an unvested position's
  `position_id` today. Had that happened, the resulting lot would show up in `cash_flows` as a
  real acquisition outflow with **no matching value** in the (already-fixed) value series it's
  supposed to neutralize — corrupting TWR and MWR/XIRR both. On a real production book this is
  also currently a no-op (the one stray legacy lot on the unvested ONWAI position, flagged as an
  unrepaired data anomaly by the sibling ADR, has no `acquired_at` and is dropped by
  `cash_flows`'s own date filter regardless) — but it is the same class of gap the sibling ADR
  closed in `portfolio_history.py`, in a sibling code path that audit didn't reach.

### Mid-task complication: a concurrent field-model migration landed underneath this work

While this audit was in progress, a **separate concurrent session** (working on the equity
vest/release feature) landed `docs/adr/2026-07-31-split-position-bucket` — replacing the single
overloaded `bucket` field (`liquid`/`contingent`/`locked`) with two orthogonal fields:
`vesting_state` (`vested`/`unvested` — entitlement, STORED, the ONE axis net worth keys on) and
`liquidity` (`liquid`/`semi_liquid`/`illiquid` — DERIVED, never stored). `Bucket` is now
`DEPRECATED`, kept only so legacy rows still translate (`position_store.vesting_state_of`).
That session's sweep landed live in `backend/models/position.py`, `backend/db/position_store.py`,
and `backend/services/positions.py` — including, partway through, this task's own newly-added
`positions_service.owned_vs_unvested_base` (their sweep found and updated it to check
`vesting_state` instead of `bucket`, correctly).

This is exactly the axis this task's fix is about — old `bucket="locked"` **is** the new
`vesting_state="unvested"`; `liquid`/`contingent` **are** `vested`. The fix's substance is
unaffected. But it forced two rounds of adaptation, done in place rather than reverted, because
reverting a live migration mid-flight is worse than adapting to it:

1. `services/fixed_income.py` and `services/returns.py`'s **prose** (docstrings/comments) still
   said `bucket="locked"` after the other session's code-level sweep updated the actual checks to
   `vesting_state == "unvested"` — corrected here for accuracy.
2. `ai/tools/portfolio.py` — the file this task's fix lives in — was **not** part of the other
   session's sweep and still filtered/described a `bucket` field that no longer exists on any
   `hydrated()` row (that field literally is not emitted any more, replaced by `vesting_state`/
   `liquidity`). Left as-is, this task's own fix would have shipped with a **structurally
   dead filter parameter** — `get_portfolio(bucket="locked")` would always match zero rows,
   silently. `GetPortfolioInput.bucket` was renamed to `vesting_state` (values `vested`/
   `unvested`), `_filter_positions`/`_POSITION_FILTER_KEYS` updated to match, and every
   `bucket`-referencing sentence in `get_portfolio`/`get_returns`/`get_fixed_income`'s
   descriptions updated to `vesting_state` terminology. Verified against real data post-fix:
   `get_portfolio(vesting_state="unvested")` now correctly returns exactly the one unvested ONWAI
   row (see Evidence) — before this correction it would have returned zero, silently.

Left untouched, deliberately: `add_positions`/`update_positions`/`remove_positions` (the WRITE
tools in the same file) still describe/accept a `bucket` argument that the write path now
silently ignores (`add_position()`'s signature dropped the `bucket` kwarg entirely) or refuses
outright (`update_position`'s field allowlist no longer includes `bucket`). This is a real,
now-live regression, but it is squarely the other session's migration finishing its own sweep —
fixing it here would mean designing how an AI-tool caller requests an unvested grant position
outside the grant flow, which is equity-grant territory this task was explicitly told not to
touch. Flagged below, not fixed. `backend/ai/tools/accounts.py`'s `get_net_worth` description and
`backend/services/ledger.py`'s docstrings also still say `bucket="locked"` in prose (their code
was already migrated by the other session) — outside this task's assigned tool set, left alone.

## Decision

1. **`get_portfolio` gains a server-computed `summary` field** — `{owned_base, unvested_base,
   base_currency}`, via a new `positions_service.owned_vs_unvested_base(rows, base=None)` that
   reuses `ledger.row_base_value` and `ledger.MARKET_ASSET_CLASSES` (the exact per-row valuation
   and asset-class gate `ledger._market_value_base`/`_unvested_value_base` use for the whole-book
   fold), computed over whatever rows the call actually returns. This is the "computed field is a
   guarantee, description is only guidance" call: description text alone leaves the model to
   re-derive the entitlement rule and re-implement the sum every time; a summary the server
   already split cannot be gotten wrong the way a hand-rolled sum can. It's **server-chosen and
   additive** per the harness invariant (no caller `fields`/projection parameter — the split
   itself, not row content, is what's server-decided) — consistent with `get_transactions`'s
   `summary_only` and the labelling convention in `docs/INVARIANTS.md`.

   This required **retiring the historical bare-list shape** for an unfiltered call (the code's
   own prior comment: "an unfiltered call has nothing to disclose... showing == total by
   definition" — true for `showing`/`total`, but `summary` has something to disclose on every
   call, filtered or not). `get_portfolio` now always returns `{positions, showing, total,
   summary[, note]}`. Checked for breakage first: no application code parses the tool's raw JSON
   result as a list (it is read as text by the model, or treated as an opaque string by
   `assistant.py`'s truncation/shrink logic) — the only real consumer of the old shape was the
   pinned characterization test `test_get_portfolio_serialises_rows`
   (`tests/test_char_tool_dispatch.py`), updated in the same change, plus three shape-sensitive
   tests in `tests/test_cost_diet_tools.py`.

2. **`get_portfolio`'s description and its filter field** now state the entitlement semantics
   explicitly (`vested` = owned, count normally; `unvested` = forfeited if the grantee leaves,
   exclude from any total) and point at `summary.owned_base`/`unvested_base` and at
   `get_net_worth`/`get_net_worth_history`'s `net_worth`/`unvested_base` as the correct source,
   rather than leaving the model to improvise a total from raw rows. The filter field itself is
   named `vesting_state` (not the deprecated `bucket`) to match what `hydrated()` rows actually
   carry post-migration (see complication above).

3. **`fixed_income.summary()`** now skips `vesting_state="unvested"` rows (mirrors the net-worth
   fold). **`returns.cash_flows()`** now skips any lot/disposal whose `position_id` is an
   unvested position, via a newly-**public** `portfolio_history.resolve_locked_position_ids()`
   (renamed from the module-private `_locked_position_ids`, reused rather than duplicated so the
   two modules can never drift on what "unvested" means; name kept as `resolve_locked_position_ids`
   rather than renamed again post-migration — see that function's own docstring for why). Both
   are the same fix shape as the sibling ADR: enforce the exclusion at the read path rather than
   assume the write path prevents the case, because nothing currently does.

4. **`get_returns`/`get_fixed_income`'s tool descriptions** each gained one sentence noting the
   exclusion — for discoverability, not because the model has to reason about entitlement itself
   here (both already return a computed total, not raw rows to sum).

## Consequences

### Positive
- `get_portfolio` can no longer produce a total that silently includes unvested equity, whether
  the model reads `summary` (guaranteed correct) or sums rows itself after reading the now-honest
  description. Reconciles with `get_net_worth`: on a real production book, `get_portfolio()`'s
  unfiltered `summary.owned_base`/`unvested_base` equal `get_net_worth()`'s
  `breakdown.market_positions`/`unvested_base` exactly (see Evidence) — pinned by
  `test_owned_vs_unvested_base_reconciles_with_ledger_net_worth`.
- `get_fixed_income` and `get_returns` are now provably entitlement-safe in every part of their
  computation, not just the parts the first ADR's audit happened to reach.
- `get_portfolio`'s `vesting_state` filter is functional post-migration (verified against real
  data) instead of silently dead — closing a regression the concurrent migration would otherwise
  have introduced into this exact tool.
- A book with no unvested positions is unaffected (all three fixes are true no-ops when
  `resolve_locked_position_ids()` returns empty).

### Negative
- `get_portfolio`'s response shape changed for the unfiltered call (bare list → envelope object)
  — a deliberate, one-time break from the prior shape, mitigated by confirming no non-test
  consumer parsed it structurally.
- The `fixed_income.py`/`returns.py` fixes address a currently-dormant reachability gap (no real
  data manifests a difference today) rather than an observed live bug — judged worth closing now
  since the reachability is real (a plain `add_positions`/`acquire_lots` call, not a hypothetical).
- This change landed on top of a live, in-flight migration in a shared file it doesn't own
  (`services/positions.py`); the golden schema file
  (`tests/golden/tool_input_schemas.json`) had to be regenerated twice as a result, each time by
  diffing only `get_portfolio`'s own entry against `git show HEAD:...` rather than the live
  working tree, to avoid capturing the other session's in-flight, uncommitted tool schemas.
  `add_positions`/`update_positions`/`remove_positions` in the same file are left with a stale,
  now partly non-functional `bucket` argument — see Follow-ups.

### Follow-ups
- **Not fixed here (out of scope, equity-adjacent):** `add_positions`/`update_positions`'s
  `bucket` argument is now dead (`add_position()` silently ignores it, defaulting every add to
  `vested`) or refused (`update_position` raises on an unknown field). This predates and is
  independent of this task's fix, caused by the concurrent `split-position-bucket` migration
  finishing its sweep of `db`/`services` before reaching the AI tool write-surface. Whoever
  completes that migration needs to update `ai/tools/portfolio.py`'s `add_positions`/
  `update_positions`/`remove_positions` (field names, descriptions, the grant-managed-field
  guard) to the new `vesting_state`/`liquidity` model.
- **Not fixed here:** `ai/tools/accounts.py`'s `get_net_worth` description and
  `backend/services/ledger.py`'s docstrings still say `bucket="locked"` in prose (their
  underlying code was already migrated by the other session to check `vesting_state`). Outside
  this task's assigned tool set (`get_portfolio`/`get_returns`/`get_fixed_income`); flagged for
  whoever finishes the prose sweep.
- `add_positions`/`acquire_lots` still have no guard preventing an unvested add or a lot
  acquisition against an unvested position outside the grant machinery — this ADR closes the
  *read* side (nothing downstream miscounts it) but does not add a *write*-side refusal.

## Alternatives considered

- **Rely on description text alone for `get_portfolio`** (skip the `summary` field) — rejected:
  the sibling ADR's own bug was exactly code failing to apply an entitlement rule it had the
  field to check; a raw-row tool asking the model to re-derive and re-sum every turn is the same
  failure mode with extra steps. A server-computed, additive field removes the failure mode
  instead of hoping the prose prevents it.
- **Keep the bare-list shape for unfiltered `get_portfolio` and bolt `summary` onto the filtered
  branch only** — rejected: the unfiltered call is precisely the "portfolio total" case per the
  tool's own description; a summary that's absent on exactly that call is not useful for the
  problem it exists to solve.
- **Revert/ignore the concurrent `bucket`→`vesting_state` migration and keep shipping `bucket`**
  — rejected: `hydrated()` rows no longer carry a `bucket` key at all post-migration, so this
  would ship a filter that always matches zero rows, a worse and more confusing bug than the one
  being fixed. Adapting to the new field name was the only option that kept the fix honest.
- **Also fix the write-side `add_positions`/`update_positions` `bucket` regression** — rejected
  as scope creep on top of an already-expanded task: it requires equity-grant-flow design
  decisions (how does a non-grant caller request an unvested add?) that belong to the session
  already owning that migration, not a read-path audit reacting to it mid-flight.

## Evidence

`tests/test_positions_stores.py::test_owned_vs_unvested_base_splits_by_vesting_state`,
`::test_owned_vs_unvested_base_excludes_non_market_asset_class`,
`::test_owned_vs_unvested_base_reconciles_with_ledger_net_worth`;
`tests/test_fixed_income.py::test_summary_excludes_locked_bucket_bond`;
`tests/test_returns_service.py::test_cash_flows_excludes_lot_and_disposal_on_locked_position`;
`tests/test_char_tool_dispatch.py::test_get_portfolio_serialises_rows`;
`tests/test_cost_diet_tools.py::test_get_portfolio_unfiltered_returns_positions_and_summary`.

Real data (`~/.open-net-worth/data`, read-only verification via `execute_tool`, FX cache warm):
`get_portfolio()` unfiltered (35 positions, one unvested: ONWAI 1500.00 units) →
`summary = {"owned_base": 195000.00, "unvested_base": 67500.00, "base_currency": "SGD"}`.
`get_net_worth()` → `{"net_worth": 295000.00, "unvested_base": 67500.00, "breakdown":
{"market_positions": 195000.00, ...}}`. `owned_base == market_positions` and both
`unvested_base` figures match exactly — a model summing `get_portfolio`'s `summary` fields
reconciles bit-for-bit with `get_net_worth`'s totals. `get_portfolio(vesting_state="unvested")`
returns exactly the one ONWAI row (`showing=1` of `total=35`), confirming the renamed filter is
live, not dead.

## Agent provenance

- **Decided by:** implementing Claude agent (Sonnet 5), on `fix/approvals-never-expire`, from an
  owner-directed audit task (fix `get_portfolio`'s description; audit `get_returns`/
  `get_fixed_income` for the same class of bug and fix what's found; decide whether to add a
  computed summary field and justify the call).
- **Lane / branch:** `fix/approvals-never-expire` (no dedicated worktree for this sub-task).
  A separate concurrent session was actively editing the equity vest/release feature during this
  work; the five files it was told never to touch (`backend/models/equity.py`,
  `backend/services/equity.py`, `backend/api/equity.py`, `backend/ai/tools/equity.py`,
  `backend/db/vest_event_store.py`) were not touched. That session's work turned out to have
  expanded, mid-task, into a position-model migration (`db/position_store.py`,
  `backend/models/position.py`, `backend/services/positions.py`) outside the originally-named
  five files — described in Context above; handled by adapting rather than fighting it, per the
  standing instruction to treat another lane's landed, ADR-backed change as ground truth.
- **Alternatives weighed:** see Alternatives considered above.
- **Driving artifacts:** `docs/adr/2026-07-31-unvested-equity-excluded-from-net-worth.md`
  (sibling same-day ADR, followed for the exclusion rule and real-data-verification convention);
  `docs/adr/2026-07-31-split-position-bucket` (concurrent sibling ADR from the other session,
  defining the `vesting_state`/`liquidity` split this work had to adapt to).
