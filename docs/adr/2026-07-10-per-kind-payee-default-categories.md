# 2026-07-10 — Payee default categories are per-kind, not one that bleeds across kinds

**Status:** accepted (owner decision, 2026-07-10)
**Where:** `backend/models/payee.py` (`Payee.default_categories`),
`backend/db/payee_store.py` (`set_default_category_for_kind`/`set_default_categories`,
expense-slot mirror), `backend/services/payee_registry.py`
(`default_category_for(payee_id, kind)`, `learn_category(..., kind)`,
`normalize_default_categories`, `set_default_categories`,
`migrate_default_categories_from_history`, `apply_default_category_to_transactions`,
`list_registry` projection), `backend/services/ledger.py`
(`create_transaction`/`bulk_create_transactions` payee-default fill),
`backend/api/payees.py` (`PayeeUpdate.default_categories`),
`backend/ai/tools/payees.py` (`set_payee_default_category` `kind` param),
`backend/main.py` (boot migration), and the frontend payee drawer
(`PayeesModal.tsx`, `types.ts`, `useAccountsLedger.ts`, `AccountsTab.tsx`,
`accounts.css`).

## Context

A payee carried ONE `default_category` (auto-learned, or set in the payee drawer), which
the create choke applied whenever a new expense/income row from that payee had no explicit
or rule-supplied category (`explicit > rule > payee-default > None`). In practice a
merchant's learned default is almost always an **expense** category — a merchant is where
you *spend*. But the fill applied it to **income** as well, so income from a payee you also
buy from was silently stamped with an expense category. Observed on a real production book:
**23 of 40 income rows** carried an expense category (Transport, Shopping, Dining out …).
Transfers and investments hit the same class of bug through the rules engine (a rule with
no `kind` filter fires on every kind) and through the frontend pickers, which offered
income/expense categories on an investment "buy" (e.g. defaulting to "Stock sales").

Categories are kind-typed (`expense|income|transfer|investment`), but nothing about the
auto-defaulting respected that typing. Reframed correctly: a payee's default is
**direction-specific** — the category you want on a *refund from* Grab is not the one you
want on a *ride with* Grab.

The owner rejected two intermediate proposals: (1) blocking expense-categories-on-income
(income **+** an expense category is a legitimate **refund**/contra-expense, `isRefundTxn`),
and (2) a global per-kind default in the profile. The chosen shape is a per-**payee**,
per-**kind** default.

## Decision

1. **Data model.** A payee holds `default_categories = {expense|income|transfer|investment
   → category id}`. The legacy scalar `default_category` is retained as the **expense
   slot** and is *mirrored* from `default_categories["expense"]` on every write, so
   pre-per-kind readers (mentions sublabels, older API consumers) keep resolving it.
   `adjustment` never carries a category and never appears in the map.

2. **Apply at the choke, per kind.** `payee_registry.default_category_for(payee_id, kind)`
   returns `default_categories[kind]` (for `expense`, falling back to the legacy scalar for
   an un-migrated payee). `create_transaction`/`bulk_create_transactions` fill a still-blank
   category from the payee's default **for the transaction's own kind** — covering every
   categorizable kind that carries a `payee_id`, never cross-kind. Precedence is unchanged:
   `explicit > rule > payee (per-kind) > None`. An expense default can no longer land on an
   income/transfer/investment row.

3. **Learning is per kind.** `learn_category(payee_id, category, kind)` records first-write-
   wins **per kind**, so categorizing an income refund teaches the income slot without
   clobbering the expense slot (and vice-versa).

4. **Manual/deliberate cases are untouched.** A refund (income + expense category), a
   categorized transfer (ADR 2026-07-03), or a categorized investment (ADR 2026-07-02) all
   still work — set by hand, or auto-applied when the user gives that payee an income/
   transfer/investment default. The choke's reserved-category guard still runs after the
   fill.

5. **Migration is data-driven** (`migrate_default_categories_from_history`, boot-ordered
   after `migrate_drop_payee_txn_count`, idempotent, best-effort, non-fatal): for each
   payee, each *missing* per-kind slot is filled from the category it most often carried for
   that kind in its own history; the expense slot seeds from the legacy scalar when history
   has no expense evidence. This preserves refund payees' income defaults ("Refund from
   GRAB" → income: Transport) and merchants' expense defaults ("Grab" → expense: Transport)
   without either bleeding onto the other.

6. **Surfaces.** `PATCH /api/payees/{id}` accepts `default_categories` (validated + resolved,
   unknown kinds/ids dropped); the AI `set_payee_default_category` tool gains a `kind` param
   (default `expense`); `GET /api/payees` returns `default_categories`; the payee drawer
   renders one category picker per kind (each surfacing its own kind's categories first),
   and "Apply to existing" stamps each kind's rows with that kind's default.

## Consequences

- **Invariant tightened.** The CLAUDE.md precedence "`explicit > rule > payee-default >
  None`" now reads the payee-default **per kind**. The old behavior (one default applied to
  expense *and* income) is gone.
- **`apply_default_category_to_transactions` changed semantics:** a payee with only the
  legacy scalar stamps its **expense** rows only (income no longer inherits it). Its unit
  test was updated accordingly.
- **Accepted edge:** a refund-only payee whose migration seeds an expense slot (from its
  legacy scalar) would auto-apply that category to a future *expense* from the same payee.
  Harmless in practice (such payees have no expenses) and easily overridden in the drawer.
- **Not addressed here:** the rules engine still fires on any kind unless a rule sets
  `match.kind`; that is existing, user-controllable behavior (scope a rule to a kind). This
  ADR is limited to the payee-default path, which was the dominant source of the bug.
