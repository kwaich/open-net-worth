---
number: 2026-07-31
title: Remove the statement-import tie-out waterfall — its "adds up" tick was tautological
status: Accepted
date: 2026-07-31
deciders: repo owner (directive) + implementing agent (Claude)
---

# Remove the statement-import tie-out waterfall — its "adds up" tick was tautological

## Context

The Decide step's verdict panel carried a secondary "▸ Show the math" expander (SI-64, itself a
softening of SI-58's always-open version) that printed a waterfall from the statement total to the
ledger total, one named term per difference, ending in `= your ledger, this period … ✓ adds up`.

The owner: *"let's not show the math for now, i don't know if it's really correct, everything looks
always add up! :)) doesn't make sense"*.

They are right, and the mechanism is in `backend/services/statements/ledger_diff.py`:

```python
# ledger = (statement − unbooked + drift) + uncovered + boundary  →  solve for drift
drift = round(ledger_net - (stmt_net - unbooked) - uncovered_net - boundary_net, 2)
```

`drift` is a **residual**: it is *defined* as whatever makes the identity balance. Every other term
is measured independently, `drift` absorbs the remainder. Therefore:

- The identity can never fail — the `✓ adds up` tick carries zero information.
- Any REAL discrepancy (a parse error, a dropped row, an FX mistake) is silently absorbed into the
  `drift` term — which renders as the reassuring sentence *"matched entries kept at original
  amounts (Merge adopts settled figures)"* — while the panel still prints a tick.

So the surface was worse than useless: it looked like verification and behaved like a rounding
identity. The owner spotted that from the outside ("everything looks always add up") before anyone
read the formula.

## Decision

**Remove the waterfall from the UI. Keep the backend computation.**

- Frontend: the `▸ Show the math` toggle, the `imp-math` waterfall it revealed, its jump-terms, the
  `mathOpen` state and the whole `imp-math` / `imp-mathfold` / `imp-math-toggle` / `imp-math-jump`
  CSS rule family are deleted. The verdict panel's coverage / amounts / ledger-residue lines and
  the reconcile status are UNTOUCHED — the owner objected to the math, not to the reconciliation
  verdict.
- Backend: `_totals` stays exactly as it is. `ledger_net` is a genuine independent query,
  `uncovered_ledger_rows` returns `totals`, the LEDGER-ONLY section header still reads
  `totals[0].uncovered_net` off it, and `tests/test_ledger_diff_covered_parity.py` asserts on
  `totals` equality across the commit boundary. This is a **presentation** decision, not a data
  one.
- A comment at the `drift` computation records that it is a residual, not an independently-computed
  quantity, so the next person doesn't resurrect the panel believing the tick means something.

Two capabilities are lost with it and are accepted:

1. The waterfall's jump-terms (SI-58) were one route into the filter chips. The verdict's own
   ledger-residue line still routes to "Not on statement", and the chips themselves are unchanged.
2. SI-56's "the near-the-cut-off term force-expands the boundary bucket" is gone; the bucket's own
   toggle is now the only way in.

## Consequences

- `docs/qa/acceptance/statement-import.md`: the SI-58, SI-64 and SI-81 clauses that described the
  waterfall are struck through and marked **SUPERSEDED 2026-07-31** rather than deleted — the
  record of what was once required stays readable.
- `qa/browser-use/` now asserts the *absence* of `.imp-math` / `.imp-mathfold` instead of checking
  that it is folded away by default, so a resurrection fails the UI gate.
- Regression-pinned by `ImportModal.verdict.test.tsx` and `ImportModal.threeState.test.tsx`.

## Follow-up deliberately NOT built (needs the owner's call)

The honest version of this feature is to compute the matched-row drift **directly** from the
merged rows and compare it against this residual, surfacing a warning when the two disagree — that
is a real integrity check and would have caught exactly the class of bug the owner was worried
about. It is a new computation with its own failure modes and its own UI, out of scope for a
removal, and it should not ship as a silent side effect of one.

## Agent provenance

Implementing agent (Claude) made the presentation-vs-data split: the coordinator's directive was
"hide the expander and its panel, do not delete the backend `_totals`", and the agent additionally
(a) deleted rather than dead-coded the render path and its CSS, because the repo's `lint:classes`
reverse-pass ratchet blocks a growing dead-class count, and (b) wrote the residual note into
`ledger_diff.py` and this ADR so the reasoning survives the diff.
