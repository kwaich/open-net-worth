# 2026-07-31 — Commitment coverage is plan-fidelity, not a household overlay

**Status:** Accepted
**Amends:** `backend/services/commitments.py::coverage()` (the "at risk" verdict shipped in the
2026-07-22 commitments-tab redesign)

## Context

Owner report on real data: a commitment's detail screen badged **"Short S$62,500 by Sep 30,
2026"**. Both numbers were wrong, and wrong in different ways.

Tracing `coverage()` against a real production `commitments.json` + ledger:

1. **Wrong metric.** The per-commitment verdict overlaid this commitment's own
   `projected_events()` (installments out; only `asset_sale`/`loan`/`windfall` funding_plan
   entries in — `income`/`cash` "Set-aside" entries were deliberately excluded as
   "already-in-the-baseline") onto the FULL HOUSEHOLD projection
   (`services/cashflow.build_projection`): general recurring income/expenses, plus a 3-month
   historical-average adjustment. So the badge didn't answer "will this specific plan work" —
   it answered "is the household solvent under a skeptical recompute of the average savings
   rate," which silently overrode the user's own explicit Set-aside entries. Owner: *"it
   shouldn't calculate [or be compared against] anything"* beyond the plan itself.
2. **Self-inconsistent pairing**, independent of #1. The old loop computed `worst` (the max
   shortfall dollar amount across ALL of a commitment's installment months, each checked
   against the household projection) and `earliest` (the earliest at-risk installment date) as
   two SEPARATE reductions over the same loop, with no requirement they describe the same
   month. Real numbers: the true near-term gap under the OLD household model was ~S$1,200 in
   2026-09; the true total gap across the whole plan was ~S$10,300.00; the displayed pairing —
   S$62,500 (a 2028-09 Handover-installment household reading) glued to Sep 2026 (an unrelated
   near-term installment's date) — matched neither.

## Decision

**`coverage()`'s per-commitment verdict becomes PLAN-FIDELITY ONLY**: today's real cash
(`cashflow.cash_balance_base` — the same figure the household projection starts from) plus
THIS commitment's own dated events, nothing else.

- **Inflows: every `funding_plan` entry, every type** — `asset_sale`/`loan`/`windfall` AND
  `income`/`cash` (a "Set-aside" is the user's real plan and must count), each on its
  `available_date`, valued via the existing `live_source_value` (the frozen stored
  amount+currency, never a live price).
- **Outflows: every unpaid installment** (no `paid_txn_id`), on its `due_date`.
- **No household modeling.** No general recurring income/expense baseline, no historical
  3-month-average override. `services.cashflow.build_projection` is untouched — it still
  drives the general Cash Flow tab and `projected_events()`'s existing callers
  (`api/cashflow.py`, `ai/tools/ledger.py`), whose blended household behavior is correct for
  those surfaces and out of scope here.
- **Floored at $0** (real insolvency), never the household `low_balance_threshold`
  (`LOW_BALANCE_THRESHOLD_DEFAULT` = 5000, or a caller override) — that floor is a household
  cushion preference, not a property of whether this one plan's cash goes negative.
- **Same-day tie-break: funding lands before a same-day outflow.** Money should be available
  for the payment it's meant to cover — this is what makes the tie-break case ("a loan draw
  funds the exact installment it's for") read as covered instead of a false gap.
- **Self-consistent by construction.** `shortfall_base`/`earliest_shortfall_date` are read off
  ONE running-balance trace: the FIRST event where the balance goes negative. There is no
  second reduction to disagree with the first — bug #2 cannot recur structurally, not just by
  convention.

Implementation: `_plan_fidelity_events` (builds the (date, signed delta) pairs) and
`_plan_fidelity_verdict` (walks them, floor $0) in `backend/services/commitments.py`, called
from `coverage()`'s per-commitment loop for `_PROJECTED_KINDS` (`purchase`/`loan_repayment`;
`goal`/`auto_pay` stay unassessed, unchanged).

### Portfolio-level fields keep their existing (household) meaning — and now compute all four
### together, off the same basis

`total_shortfall_base`, `min_balance_base`, and `timeline` are unchanged: still the full
household-blended cash-only projection (income/expense baseline + commitment overlay),
floor-aware via `low_balance_threshold`/`floor`. That is a legitimate, different question
("is the household solvent") from "does this one plan work," and it is what the Overview tab's
cashflow widget (`frontend/src/components/tabs/overview/widgets/overviewCashflowCore.tsx`)
already reads that way ("Your commitments run you $X short … on current cash + plan").

Top-level `at_risk`/`earliest_shortfall_date` used to be **folded from the per-commitment
loop** (OR across commitments / earliest across commitments). Switching only the per-commitment
half would have broken that fold's own consistency — `at_risk` would silently stop agreeing
with `total_shortfall_base` (a household total > 0 while every individual plan is fine, or vice
versa), which is the identical bug class this ADR fixes, just relocated. So the portfolio block
is now derived **entirely from the household timeline**, same basis as `total_shortfall_base`/
`min_balance_base`: `at_risk` = the timeline ever dips below `threshold`; `earliest_shortfall_date`
= the first such month (`YYYY-MM-01`). All four portfolio fields now come from one reduction —
self-consistent for the same reason the per-commitment fix is.

### Frontend: "which commitment is driving this" moves client-side

`coverageEngine.ts`'s `deriveClientCoverage` used to find the at-risk commitment to name in the
masthead ribbon / feed the fix-proposal engine by matching `cov.earliest_shortfall_date`
(portfolio) against a commitment's own `earliest_shortfall_date` — exact-string equality that
only worked because both were computed off the SAME household reduction. Now that the two bases
genuinely differ (plan-fidelity vs. household), that match will essentially never hit. Replaced
with a direct, self-contained read of `cov.commitments[]`: the at-risk commitment with the
soonest own `earliest_shortfall_date` (ties broken by the larger `shortfall_base`, preserving
the prior tie rule). The household verdict at the top of the masthead (`derived.minBal < floor`)
was already independently computed from `timeline` and is untouched.

## Consequences

- The commitment-detail badge and masthead ribbon now report a number and date that both
  describe the literal same event in that commitment's own plan — never a mismatched pairing.
- A commitment funded by cash the user explicitly set aside (`income`/`cash` funding_plan
  entries) is now credited for it; previously those were invisible to the per-commitment check
  and could produce a false "at risk" even when the user's actual plan was sound.
- A commitment can show "fully funded" (plan-fidelity) while the portfolio banner still reads
  "at risk" (household model) — this is intentional: two different questions, both honestly
  labeled, no longer accidentally forced to agree by sharing a computation.
- `floor`/`low_balance_threshold` no longer affect the per-commitment verdict at all — only the
  portfolio one. A commitment's plan-fidelity balance dipping to exactly $1 is "fine"; dipping
  to $-1 is not. No cushion preference applies at this level.
- A `type=income`/`cadence=monthly` "Set-aside" funding_plan entry is modeled as ONE inflow of
  its stored `amount` on its single `available_date` — not as a recurring monthly accrual. This
  matches the literal `funding_plan` schema (one amount, one date per entry) and the explicit
  scope of this fix; a true recurring-accrual trace (crediting the monthly rate every month
  until the installment, or reading the synced envelope's real `accumulated` balance) is a
  separate, larger change and is deliberately out of scope.
- `coverage()`'s docstring and module-level invariants list were updated to spell out that the
  per-commitment and portfolio halves of the response now measure different things by design —
  future readers must not assume they can cross-reference by date/id as they used to.

## Agent provenance

Root-caused directly against the owner's real `~/.open-net-worth/data/commitments.json` and
ledger during an extended debugging session (not reproduced from a synthetic fixture first):
recomputing `coverage()`'s household projection by hand against the stored funding_plan/
installments surfaced both the excluded Set-aside entries and the `worst`/`earliest` pairing
mismatch as two independent defects in the same function. The plan-fidelity redesign and the
$0 floor were the owner's explicit decision, made directly in that session.

Pinned by `tests/test_commitments_service.py`:
`test_coverage_plan_fidelity_pairing_is_self_consistent` (a same-day loan draw fully covers a
near-term installment while an unrelated, unfunded, much-later Handover installment is the real
gap — asserts the reported date+amount describe THAT installment, not a mismatched pairing),
`test_coverage_plan_fidelity_counts_income_set_aside_as_inflow` (a `type=income` Set-aside now
closes a gap the old `_NEW_MONEY_TYPES` filter would have missed), and the updated
`test_coverage_is_floor_aware` (the per-commitment verdict no longer moves with `floor`; the
portfolio verdict still does).
