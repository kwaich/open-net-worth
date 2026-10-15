# ADR: Balance-based Ready-to-Assign — the identity, on-budget accounts, and credit cards in the cash side

**Date:** 2026-07-02 · **Status:** Accepted — restored 2026-09-09 by ADR [`2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`](2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category.md) after one day Superseded by [`2026-09-08-ready-to-assign-is-unassigned-cash`](2026-09-08-ready-to-assign-is-unassigned-cash.md) (that ADR is now itself Superseded). The on-budget account set stands via ADR 2026-09-07. · **Owner:** the maintainer (decisions relayed in-session) · **Implements:** `.claude/rfcs/balance-based-rta.md`

> **Partial revision (2026-07-09):** the credit-card handling below (cards in the signed cash side, no payment envelopes) is **revised for the YNAB budget engine** by ADR [`2026-07-09-ynab-engine-credit-card-payment-envelopes`](2026-07-09-ynab-engine-credit-card-payment-envelopes.md) — that engine adopts YNAB payment envelopes so card debt no longer reduces RTA. The balance-based identity and everything else here still stands (and cards-in-cash remains valid for a simpler engine).

> **Partial revision (2026-09-07):** brokerage / crypto-exchange cash is **no longer on-budget**
> and a drawn margin debit is **no longer netted into spendable cash** — ADR
> [`2026-09-07-seven-account-types-one-axis`](2026-09-07-seven-account-types-one-axis.md) makes
> `investment` an off-budget type whose settlement cash (margin included) lives in its own band.
> The balance-based RTA identity below still stands; only the on-budget membership changed.

## Decision

Ready-to-Assign is computed from the **YNAB balance identity**, not the old income-flow walk:

```
RTA(m) = on_budget_cash_as_of(m) − pre_budget_baseline − Σ available(m)
```

- `on_budget_cash_as_of(m)` — Σ SIGNED balances (base ccy, end of month m) of every **on-budget** account, converted with the same FX snapshot as envelope activity.
- `Σ available(m)` — the TRUE per-envelope value for m (current-month overspend counts negative; the `max(0,·)` clamp lives only in the month-to-month carry), plus the blank-category "Unsorted" bucket.
- `pre_budget_baseline` — a stored continuity constant; **0 for this instance** (owner chose a *fresh start*: RTA reflects all on-budget money at cutover, no captured baseline, no migration).

### On-budget derivation (`cashflow.is_on_budget`, override-able per account via `Account.on_budget`)

| On-budget | Off-budget (tracking) |
|---|---|
| checking, savings, cash, ewallet, debit_card, money_market | custody (physical metals), real_estate, retirement, vehicle, collectible, other_asset |
| **brokerage, crypto_exchange** (owner: idle broker/exchange cash is assignable; an investment buy draws RTA at the buy) | loan_receivable, any `asset_class` override, any `locked` account |
| **credit_card, line_of_credit, bnpl — SIGNED** (owner: "credit_card should be included in RTA calculation") | mortgage, personal/auto/student loans, margin liabilities |

### Credit cards: in the cash side, NO payment-envelope machinery

The RFC sketched YNAB's auto-move CC-payment envelopes with "the CC balance joins the cash side". Deriving the math shows those two together **double-count the debt** (a budgeted $50 card spend would wrongly drop RTA by $50). Two self-consistent formulations exist:

- (A) cash = assets only + per-card derived CC-payment envelopes (YNAB-literal), or
- (B) **cash includes signed card balances; envelopes fill normally on card spend; no payment envelopes** — chosen (owner's explicit ask, and strictly less machinery).

Under (B): budgeted card spend is RTA-neutral in-month; paying the card (bank→card transfer) is RTA-neutral; **standing card debt reduces RTA one-for-one** (pay-in-full semantics — the debt claims your cash honestly). Deviation from YNAB: an *unbudgeted* card overspend pulls RTA at month roll (YNAB lets it ride as debt); this is the conservative direction and matches how cash overspend already behaves.

### Consequences that now fall out with no per-kind rules

income → RTA↑ · assign → RTA↓ · budgeted spend (cash or card) → neutral · investment buy/sell → RTA↓/↑ · transfer to tracking → RTA↓ · on↔on transfer → neutral · **income landing in a tracking account no longer phantom-inflates RTA** (old flow-model bug) · account `initial_balance` is assignable (fresh-start onboarding).

### The activity guard

An expense/income leg on a **known off-budget account** never fills an envelope (`_activity_by_category` guard): envelope movement without matching on-budget cash would leak RTA in the wrong direction under the identity. Unknown/missing account ids are treated as on-budget (legacy rows).

### Other calls made here

- **IBKR margin (negative brokerage cash) stays on-budget** — borrowed money is a real claim against the pool; hiding it would inflate RTA ~S$5k. Override exists per-account.
- **Archived accounts count in `_cash_as_of`** — their balance is 0 going forward but real in past months; excluding them would corrupt historical RTA. (The `cashflow.on_budget_cash_base` runway helper keeps its archived-exclusion — different question.)
- The old explicit prior-month overspend pull is **deleted** — the identity absorbs it (in-month the negative available offsets the cash; after the reset the missing cash lowers RTA by exactly the deficit).
- `inflows_to_assign` survives as a display-only "income this month" figure.

## Adversarial-verify round (same day) — hardening decisions

A 5-lens verification fleet (executed repros, isolated clones) drove these follow-ups:

- **`bank_transfer` is on-budget** (BLOCKER): it's the store default AND all six bank connectors' account type — off-budget would zero RTA for every synced bank user.
- **Envelope activity requires a KNOWN on-budget leg** (`_plan_leg_ok`): missing/ghost account ids used to fill envelopes with no cash counterpart — *spending money raised RTA*; an account-less refund depressed it permanently. Fail-closed now; such rows are plan-invisible (they move no balance either).
- **`initial_balance_date` gates both sides**: an account contributes nothing to months before its anchor (onboarding an account no longer rewrites historical RTA), and a txn dated before its account's anchor never fills an envelope (that movement is inside the asserted opening balance).
- **Pre-floor months return RTA 0** (identity gated at the budget floor) — a pre-history month must not claim the whole bank as assignable; matches the docstring contract + nav gate.
- **Dateless txns excluded from the cash side** (they were already excluded from activity — the identity's two sides must agree).
- **Stray-category activity folds into Unsorted** (e.g. an expense mislabelled with an income-kind key): visible in the table, symmetric in the reserved sum — never a silent RTA move.
- **`inflows_to_assign` (display) applies the same plan guard** — the header never claims income RTA didn't see.
- **`budget_health` zero-band widened to ±0.05**: per-txn activity rounding vs once-per-account balance conversion accrues cent-level FX drift (measured ±0.005/txn) — a fully-assigned multi-currency book must not flip to "to_assign" over a phantom cent. KNOWN NOISE, documented, not eliminable without changing display rounding.
- **REVISED (owner decision, later on 2026-07-02 — YNAB post-2021 parity)**: future-month assignments now DO reduce the displayed Ready-to-Assign (`ready_to_assign = base identity − assigned_in_future_months`); the internal memoized base stays pure, `rta_carried_in` remains the prior month's pre-deduction base so breakdown lines never double-count a future assignment through the carry chain, and the auto-assign underfunded cap respects the displayed (deducted) figure. Locked by `test_future_assignment_reduces_current_rta` / `test_identity_exposure_includes_future_term`.
- **Kept (documented, not bugs)**: archived accounts stay in the cash side (a stale non-zero archived balance shows up honestly in the formula line); `rta_status="overassigned"` now also covers debt-driven negative RTA (copy nuance for the UI, not math).

## Evidence

`tests/test_rta_balance_identity.py` (17 scenario tests: buy↓/sell↑, CC neutral/payment/debt, tracking guards, baseline, initial balance, overspend-at-roll ×3, FX coherence, exposed identity) + `tests/test_planning_service.py` (81) and `tests/test_budgeting_acceptance.py` (14) — **assertion values unchanged** after wiring accounts into fixtures, proving the identity reproduces the flow numbers whenever money moves through on-budget accounts (the flow model was a special case of the identity).

## Agent provenance

Designed and implemented by Claude (Fable 5) with owner decisions taken interactively: brokerage/crypto on-budget, fresh start, "credit_card should be included in RTA calculation too". The formulation-(B) correction to the RFC was derived by contradiction during implementation and adopted as the smaller, identity-consistent design.
