# 2026-07-03 — Transfers may carry a category; the activity fold decides what counts

**Status:** accepted (owner decision, 2026-07-03)
**Where:** `backend/services/ledger.py` (`create_transaction`/`bulk_create_transactions`/
`bulk_update_transactions`), `backend/services/rules.py` (`apply_rules`/
`apply_rules_to_history`), `backend/services/scheduling.py`
(`_validate_transaction_template`), `backend/services/planning.py`
(`_activity_by_category`), `backend/ai/tools/_shared.py` (`_txn_data_from_ai`),
`backend/ai/tools/ledger.py`, `backend/ai/prompts.py`.

## Context

`kind=transfer` rows have always been stripped to `category=None` at the write choke
point (`_NO_CATEGORY_KINDS`), refused a categorized read anywhere else. The reason was
structural: before the envelope⊥category split, a category key WAS the budget row —
`available(m, cat) = ... + activity(m, cat)` read `t.get("category")` directly. If a
transfer had been allowed a category, EVERY transfer label (e.g. tagging an inter-account
move "loan payment") would have doubled as a budget-row reference, and the fold had no
way to tell "this transfer moves money the plan should absorb" from "this transfer's
label is incidental" — a labeled transfer would have silently drained an envelope for
money that either never left the plan (both legs on-budget — net zero) or never entered
it (both legs off-budget). Banning the category outright was the only write-time
guarantee available.

**Post envelope⊥category split** (`.claude/rfcs/envelope-category-full-split.md`), a
category is a pure, inert transaction label — it has no budget meaning on its own.
Budgetability comes from an explicit **link** (`envelope.category_keys`); an unlinked
category has no row at all (`docs/adr/2026-07-02-investment-first-class-kind.md` already
made this exact case for `kind=investment`, which has carried an optional category since
that ADR). The structural reason the transfer ban existed is gone — a transfer's category
is just as inert as any other kind's until something reads it.

## Decision

1. **Write path:** `kind=transfer` validates its category exactly like an expense's —
   optional, and if provided must exist in the live category registry (unknown → the same
   `ValidationError` an expense/income/investment row gets; never silently coerced or
   stripped). `kind=adjustment` is UNCHANGED — a balance correction is still never a
   spending label, so it is still force-stripped to `None`.
2. **Read path (the actual safety mechanism):** `planning._activity_by_category` folds a
   categorized transfer by an XOR rule on which leg sits on an on-budget account
   (`_plan_leg_ok`, same on-budget guard every other kind uses):
   - Exactly ONE leg on-budget → the category absorbs THAT leg's own native amount
     (converted to base) — from-leg on-budget drains the envelope like a budgeted
     expense (e.g. a loan payment leaving on-budget cash to an off-budget lender);
     to-leg on-budget credits the envelope like an income refund (e.g. a reimbursement
     arriving from an off-budget counterparty).
   - BOTH legs on-budget → folds NOTHING. The cash side already nets to zero moving
     money between two on-budget accounts (this is the case the old invariant was
     protecting against), so there is no money for the category to absorb — the label
     is inert by construction, not by refusal.
   - NEITHER leg on-budget → folds nothing (not plan money, same as an uncategorized
     transfer between two tracking accounts today).
   - UNCATEGORIZED transfer → unchanged from today: excluded entirely, the cash side
     alone moves Ready-to-Assign directly.

   This read-time rule is the entire safety net now — there is no write-time
   "is this transfer allowed to be categorized" conditional. A categorized transfer to a
   category with no linked envelope falls through to the same synthetic `__unbudgeted__`
   group any other labeled-but-unlinked spending lands in (drains RTA directly, same as
   an unlinked expense category) — no special-casing needed, it falls out of the existing
   `_unbudgeted_lines` machinery automatically.
3. **Everywhere else** the old ban was duplicated (`services/rules.py`'s
   `_NO_CATEGORY_KINDS`, the recurring-transaction template guard in
   `services/scheduling.py`, the AI mapper `_txn_data_from_ai`, tool descriptions,
   system-prompt copy) now only refuses `adjustment`.

## Why the fold — not a write-time flag

An alternative was a write-time "is this leg on-budget" check at create time, refusing a
category unless exactly one leg qualified. Rejected: on-budget-ness is an ACCOUNT
property that can change after the transaction is written (`on_budget` override, account
type edits), and the balance-based RTA identity is already fully read-time /
computed-not-stored (`docs/adr/2026-07-02-balance-based-rta.md`). A write-time gate would
drift from the live account state the moment an account's on-budget flag changed;
computing the fold at read time, from current account state, is both simpler (one rule,
no new validation branch) and consistent with every other Planning invariant in this
codebase.

## Consequences

- A budgeted one-leg-on-budget transfer is RTA-neutral (the envelope absorbs the cash
  drop) — the loan-payment case this decision was made for.
- An uncategorized one-leg transfer is unchanged — drains/credits RTA directly.
- A categorized both-on-budget transfer moves neither RTA nor any envelope — the label is
  purely informational.
- A categorized transfer to an unlinked category surfaces in the Unbudgeted group.
- `adjustment` is untouched in every path.

Tests: `tests/test_enforce_category.py`, `tests/test_bulk_transactions.py`,
`tests/test_rules_service.py`, `tests/test_recurring_transaction.py`,
`tests/test_char_tool_dispatch.py`, `tests/test_rta_balance_identity.py` (the four
consequences above, mirroring the categorized-investment identity tests from
`docs/adr/2026-07-02-investment-first-class-kind.md`).

## Provenance (Agent)

Implemented by Claude from the owner's chat instruction to retire the "transfers carry
category=None" invariant now that the envelope⊥category split removed the structural
coupling that motivated it, with the XOR-on-on-budget-leg fold rule and "adjustment
unchanged" specified directly by the owner. The XOR fold mirrors the existing categorized-
investment fold in `planning._activity_by_category` (buy drains / sell credits) rather
than inventing a new pattern — a transfer's two legs play the role of an investment's
single cash leg plus its implicit off-budget position leg.
