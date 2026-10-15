# 2026-07-04 — move_money caps at AVAILABLE, not this-month Assigned (FEB-02)

**Status:** accepted (orchestrator decision, 2026-07-04, verified bug-sweep fix wave) ·
amends the handoff-port RFC's original `move_money`/RTA-picker spec
(`.claude/rfcs/budget-envelopes-handoff-port.md` items #9 / Lane BE §8)
**Where:** `backend/services/planning.py` (`_op_move_money`, `_op_move_money_rta`,
`_check_move_cap`), `backend/db/budget_assignments_store.py` (`set_assigned`)

## Decision

Moving money OUT of an envelope row — envelope→envelope, or envelope→`__rta` (release) — is
capped at the row's **AVAILABLE** for the viewed month, not this-month **Assigned**:

```
amount ≤ available(month, row)   # was: amount ≤ assigned(month, row)
```

`assigned` is therefore allowed to go **NEGATIVE** as a result of a move: moving carried-over
money (available > 0, this month's own assigned == 0) drives the row's stored assigned below
zero. `BudgetAssignmentsStore.set_assigned` now stores a negative amount as-is — only an
**exact** zero pops the entry (it used to pop on `amt <= 0`, silently forbidding negative
assigned). The `__rta`-sentinel "release to RTA" half of the move keeps the identical cap
(same math, same helper — `_check_move_cap`); "fund from RTA" is unchanged (no cap, RTA may
go negative, matches `assign`).

## Why

The original handoff RFC (and its own Lane BE contract) specified the cap as "assigned"
(`to `__rta` decrements source assigned, capped at assigned`). That is **not** YNAB-correct:
in YNAB, money that carried forward from a prior month into an envelope's Available is fully
movable — the whole point of rollover. Capping at this-month Assigned instead refuses a very
common, harmless action (move an envelope's leftover balance elsewhere) whenever this
month's own Assigned happens to be 0 or small, even though the envelope visibly holds plenty
of Available.

The availability identity already has exactly the right shape to absorb a negative Assigned
safely: `available(m, cat) = max(0, available(m-1, cat)) + assigned(m, cat) + activity(m,
cat)`. A negative `assigned` in month `m` behaves exactly like an in-month overspend already
does — it lowers `available(m)` without corrupting `m-1`'s carry (the `max(0, …)` term only
ever looks at the PREVIOUS month's own value) and without leaking into `m+1` beyond what
`available(m)` itself carries forward.

## Consequences

- The refusal message changed shape: `"cannot move more than '{src}' has available this
  month ({available:.2f})"` (was: `"cannot move more than is assigned to '{src}'"`).
  `BUD-MOVE-NO-NEGATIVE` (B2) is renamed `BUD-MOVE-CAP-AVAILABLE` in
  `docs/qa/acceptance/budgeting.md` / `tests/test_budgeting_acceptance.py` to reflect the new
  invariant; all `move_money`-refusal tests across `tests/test_planning_service.py`,
  `tests/test_envelope_flip.py`, and `tests/test_budgeting_acceptance.py` were updated to
  match (the OUTCOME is unchanged in every case those tests exercise — this month's own
  assigned happens to equal available there, since there's no carry/activity — only the
  *reason* the refusal fires changed).
- `RTA = cash − baseline − Σ available` is untouched: moving money between two rows in the
  same month nets to zero (Σ available unchanged), so RTA never moves from a move — proved
  by a dedicated regression (`test_BUD_MOVE_CAP_AVAILABLE_allows_negative_assigned_for_carried_money`).
- `_future_assigned` (a raw sum over `assignments_store` values) needs no special-casing — a
  negative entry sums correctly on its own.
- `auto_assign_preview`'s `reset_assigned` mode still zeroes every non-zero (including
  negative) assigned entry back to 0 via `_set_assigned_amt` — unaffected.
- `BudgetAssignmentsStore.set_assigned`'s "pop on non-positive" behavior is now "pop on exact
  zero" — the one other caller of this store method that could pass a negative
  (`_op_move_money`/`_op_move_money_rta`) is the intended new caller; `assign` itself still
  explicitly refuses an explicit negative amount at the API boundary (`_op_assign`), so a
  negative assigned can only ever arise from a move, never a direct assign.

## Provenance (Agent)

Implemented by Claude as one of 8 verified findings + 2 extras in a backend fix-lane sweep of
the Budget subtab (worktree `agent-a94abf15928f98b9d`, off `feature/plan-redesign` @ 2af27c14).
The cap-at-available rule and the negative-assigned-is-legal consequence were an explicit
orchestrator decision overriding the original RFC's assigned-cap wording; the store-level
zero-vs-negative distinction and the row-available helper (`_check_move_cap`, shared by both
`_op_move_money` and its `__rta` half) were designed by the agent to keep the change to one
small, testable seam.
