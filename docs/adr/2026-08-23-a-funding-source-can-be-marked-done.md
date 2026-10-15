---
title: A funding source can be marked done, and "missed" is derived
status: Accepted
date: 2026-08-23
deciders: repo owner + implementing agent (Claude)
---

# A funding source can be marked done, and "missed" is derived

## Context

Owner, on why the Commitments verdict is wrong:

> "The status of commitments should be based on whether all actions / funding plan and stuff
> are done, not based on cash flow." — "because I modeled it, that's why we have to do it."

The owner modelled Parkview Residences in full: 10 unpaid installments, 41 funding rows,
dated actions. Every execution fact in that model was invisible to the verdict.

`Installment` has carried `status` + `paid_txn_id` since day one. `CommitmentAction` has
`status: pending | done | missed`. `FundingSource` had **neither** — the full schema was id,
type, label, amount, currency, ref_position_id, sell_quantity, available_date, ref_account_id,
repayment_commitment_id, note, `disabled`, cadence.

So `_plan_fidelity_verdict` walks the dated plan and nothing else. Miss every set-aside from
now to 2028 and the commitment stays green: a missed set-aside does not remove the row, the row
keeps its date, and it keeps counting. The only thing that ever removed a funding row was
`disabled: true`. **26 of Parkview's rows — S$143,754, 37% of a S$385,323 plan — were
permanently unfalsifiable promises.**

The alternative considered and rejected was making the verdict forecast-driven (cap each
set-aside at projected free cash flow). The owner killed it in one line: *"otherwise it will
keep saying at risk for years."* Parkview runs to Sep 2028; a projection-based red badge cannot
be cleared by any action, and an alert you cannot act on is wallpaper.

## Decision

`FundingSource` gains execution state, mirroring what installments already have:

```
"status": "planned" | "done"     # default "planned"
"realized_txn_id": str | None    # ledger anchor, mirrors installment.paid_txn_id
```

**`missed` is deliberately NOT a stored value.** It is derived at read time by
`services.commitments.funding_source_overdue(source, today)`:

```
not disabled and status != "done" and available_date and available_date < today
```

A stored `missed` is only true if something sweeps the table every day — which is precisely
how `Installment.status` (storing `due`/`upcoming`) goes stale. The derived form is correct the
moment the clock passes the date, with nothing to run. A `disabled` source is never overdue: it
was consciously pulled from the plan, not silently missed.

No Alembic revision. `CommitmentsStore` is a `BookPayloadTableStore`, so `funding_plan[]` is
JSON inside the payload. Legacy rows hydrate on read (`_hydrate_funding`) via overrides on
`all()` and `find_by_id()` — `query()`/`count()` route through `all()`, so those two cover
every read path. Chosen over a backfill migration so a portable-bundle import of an older book
is self-healing rather than dependent on having run a revision.

`update_funding_source` REFUSES an unknown status (`ValidationError` → HTTP 422) rather than
coercing it. Create still coerces to `planned`, matching how `type` already falls back.

## Consequences

- A funding plan is now falsifiable: marking a source done/missed can change the verdict. It
  provably could not before.
- This is the enabling change for the two-status design in
  `docs/superpowers/specs/2026-08-23-commitment-status-is-execution-not-forecast-design.md`
  (execution status + funding status, side by side, neither hiding the other). The remaining
  phases — `execution_status()`, the funding-status margin fields, and the UI — are not shipped
  by this ADR.
- `status` reaches Lucius through the existing `update_funding_source` tool (extended schema, no
  new tool, no rename, so no alias needed).
- Marking a set-aside done is manual for now. Auto-detecting one from the ledger is deferred;
  `detect_unmarked_payments` is the template when we do it.

## Agent provenance

Reached by tracing the owner's "why do we have $24K funding in Feb?" question down to
`_plan_fidelity_verdict`, then testing the claim that the verdict responds to execution — it
does not. The derived-vs-stored `missed` split was an implementation call made against the
observed staleness of `Installment.status`, not a requirement the owner stated.
