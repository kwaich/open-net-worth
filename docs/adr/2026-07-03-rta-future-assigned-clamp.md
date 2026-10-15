# 2026-07-03 — RTA future-assigned deduction is clamped to the month's own remainder

**Status:** accepted (owner decision, 2026-07-03) · amends the raw-subtraction decision of 2026-07-02
**Where:** `backend/services/planning.py` (`_display_rta`)

## Decision

The displayed Ready-to-Assign still deducts money assigned to future months (a dollar
promised twice is the bug YNAB parity exists to prevent), but the deduction is **capped at
the viewed month's own positive identity remainder**:

```
deducted = min(future_assigned, max(0, identity(month)))
displayed_rta = identity(month) − deducted
```

`assigned_in_future_months` in the `budget_month` payload carries the **deducted** amount,
not the raw future sum.

## Why (the owner's report)

With the raw subtraction, **browsing any past month showed a deep-negative RTA** — the raw
sum deducted ALL later months' assignments (funded by later months' income) from that
month's *historical* cash frame. Frame-mixing, always negative, meaningless.

The owner's rule: *future months can only eat what the viewed month actually has left*, and
the "Assigned in future months" line should *only show when there was a remainder it
consumed*.

## Consequences

- A past month with leftover shows it consumed down to (at worst) **0** — never dragged
  negative by promises later income paid for.
- The popover line renders only when the deduction is real (frontend already hides the
  term at 0 — `budgetRta.tsx`; no frontend change needed).
- A genuinely over-assigned month (identity < 0 on its own) keeps its true negative —
  the clamp deducts nothing there, and never masks real overspend.
- A pure future over-promise shows red **in the future month itself** (its own Σ available
  exceeds cash), not in the current month. *Deliberate deviation from nYNAB*, which paints
  the one global RTA red everywhere.
- The exposed identity `rta == on_budget_cash − baseline − Σ available −
  assigned_in_future_months` now holds **exactly on every month** (the field is the
  deducted term), where before it only held when solvent.
- `auto_assign_preview` cap behavior is unchanged (proof: when identity > 0 and future ≤
  identity the values are equal; in every other case both raw and clamped are ≤ 0 → no
  assignment either way).

Tests: `tests/test_rta_balance_identity.py` (`test_past_month_never_negative_from_future_assignments`,
`test_future_line_hides_when_month_had_no_leftover`, `test_genuine_overassignment_keeps_its_true_negative`,
`test_pre_floor_month_not_dragged_negative_by_assignments`, `test_identity_exposure_holds_exactly_when_clamped`).

## Provenance (Agent)

Implemented by Claude from the owner's two-message spec ("otherwise the last months'
amounts always negative" + "it should only show if we have remaining amount to assign for
next month"). The clamp was chosen over re-anchoring past months at the current-month
frontier (full nYNAB "one global number") because it satisfies both asks with one rule,
keeps every month's popover self-consistent (no frontier frame injected into a past month's
breakdown), and preserves the 2026-07-02 behavior unchanged in every solvent case — all
three pre-existing future-assigned tests pass untouched.
