# 2026-07-31 — Split "cash" into bank cash / brokerage cash, add a liquid-net-worth metric

**Status:** Superseded by [`2026-09-07-seven-account-types-one-axis`](2026-09-07-seven-account-types-one-axis.md) — the bank / brokerage cash split and `liquid_net_worth_base` are replaced by the seven-type bands; margin is negative settlement cash inside `investment`, never netted into cash on hand.
**Owner:** the maintainer (design approved in-session)
**Touches:** `backend/services/cashflow.py`, `backend/services/ledger.py` (`_breakdown_as_of`),
`backend/ai/tools/ledger.py` (`simulate_cashflow`), `backend/ai/tools/portfolio.py`
(`get_net_worth_history`), `frontend/src/components/tabs/overview/widgets/netWorth.tsx`,
`frontend/src/utils/constants.ts`

## Context

A prior audit traced ONE broad "cash" definition computed independently in three places:

1. `services/cashflow.cash_balance_base()` — every `kind="asset"` account except
   `type="loan_receivable"`, an `asset_class` override, or `locked`. This deliberately
   includes bank/e-wallet **and** brokerage/crypto-exchange accounts, and a negative
   margin-debit balance drawn inside a brokerage account.
2. `services/ledger._breakdown_as_of()`'s `classes["cash"]` band, surfaced by
   `net_worth_history()` and the `get_net_worth_history` / `simulate_cashflow` AI tools —
   the SAME broad set.
3. `frontend/src/hooks/usePortfolio.ts`'s `toAccountRow()` — every non-property,
   non-`loan_receivable` asset account maps to `asset_class: 'cash'` client-side, feeding
   Overview's "Liquid cash" KPI.

Meanwhile `frontend/src/components/tabs/accounts/categories.ts`'s `TYPE_GROUP` — the Accounts
tab's own sidebar grouping — already puts `brokerage`/`crypto_exchange` under **"investments"**,
independently of any of the above. So the Accounts tab (correct) and the AI / Overview KPI
(mislabeled) disagreed, and the owner noticed: idle brokerage settlement cash and drawn margin
debit were being reported to the user, and to Lucius, as plain "cash."

Industry precedent backs the distinction: Plaid's `account_type`/`account_subtype` taxonomy
separates `depository` (checking/savings) from `investment` (brokerage), and models a broker's
`margin_loan_amount` as a **liability field**, never netted into cash. The standard "liquid net
worth" metric (NerdWallet, SmartAsset, Kubera) is liquid assets minus short-term liabilities,
explicitly excluding illiquid/retirement holdings and long-term financing debt.

## Decision

Add three new, additive surfaces without touching the existing ones' math:

| Surface | Definition |
|---|---|
| `cashflow.bank_cash_base(base)` | Σ `type ∈ {checking, savings, cash, ewallet, debit_card, money_market}` asset accounts (no `asset_class` override, not `locked`) |
| `ledger._breakdown_as_of()`'s new `classes["brokerage_cash"]` band | Σ `type ∈ {brokerage, crypto_exchange}` asset accounts, no `asset_class` override — margin debit netted in (can be negative) |
| `cashflow.brokerage_cash_base(base)` | The `bank_cash_base`-family (locked/asset_class-excluded) counterpart of the band above, for point-in-time reads outside the net-worth-history reconstruction |
| `cashflow.liquid_net_worth_base(base)` | `bank_cash_base + brokerage_cash_base − short-term liabilities` (`credit_card`, `line_of_credit`, `bnpl`, `other_debt`) |

`ledger._breakdown_as_of()`'s account loop grows one branch: an asset account with
`type ∈ {brokerage, crypto_exchange}` (and no `asset_class` override) now lands in
`classes["brokerage_cash"]` instead of `classes["cash"]`. `classes["cash"]` becomes **bank-only**
going forward — this is a real, deliberate narrowing of that band's meaning, not a bug.

**`cash_balance_base()`'s math is UNCHANGED and stays correct** — it is the RUNWAY figure
(cash-flow starting balance, RTA, `services.commitments`, `services.targets`), and ADR
2026-07-02-balance-based-rta already decided borrowed margin money is a real claim against the
spendable pool ("hiding it would inflate RTA ~S$5k"). Every existing caller keeps its exact
current behavior; `bank_cash_base`/`brokerage_cash_base` are extracted from a new shared
`_spendable_asset_total()` helper so `cash_balance_base` stays output-identical while the three
functions share one summation loop instead of three copies.

### Short-term liability set (a judgment call, flagged explicitly)

`liquid_net_worth_base` subtracts `{credit_card, line_of_credit, bnpl, other_debt}`. The build
brief named `credit_card` + `other_debt` explicitly and said to check whether `line_of_credit`
exists in the enum (it does) and to exclude "mortgage, auto_loan, student_loan, and anything else
clearly long-term." Two types needed a judgment call, made by matching an **existing** split
already in `cashflow.py` (`_ON_BUDGET_LIABILITY_TYPES` = `{credit_card, line_of_credit, bnpl}`,
and `is_on_budget`'s docstring explicitly groups `personal_loan` and `margin` with
mortgage/auto/student loans as "long-term financing debt"):

- **`bnpl` → INCLUDED.** Not clearly long-term (it fails the brief's own exclusion test), and
  the codebase already treats it as short-term/revolving spending debt alongside `credit_card`.
- **`personal_loan` and `margin` (as their own liability account type) → EXCLUDED.** The
  codebase already classifies both as long-term financing debt (`is_on_budget`'s docstring,
  same file). Margin debit itself is NOT double-excluded — it is already netted into
  `brokerage_cash_base` as a negative balance inside the brokerage account; a standalone
  `type="margin"` liability account is a different real-world shape (a broker-reported margin
  loan tracked separately) and stays off this metric, consistent with RTA's treatment.

### A real-data finding, surfaced not hidden

On a real production book, `liquid_net_worth_base` comes out sharply negative (~-S$310k),
dominated by one `other_debt` account ("Parkview Outstanding Installments", ~S$165k) — a
multi-year real-estate installment plan that economically reads as long-term financing despite
carrying the `other_debt` type (there is no better-fitting enum value; recategorizing accounts
is out of scope here). Because the build brief explicitly named `other_debt` for inclusion, and
there is no data-driven way to distinguish "genuinely short-term other debt" from "this specific
account," the type-level rule is implemented as specified. **Flagged for the owner to confirm**
before trusting `liquid_net_worth_base` at face value — it may currently read far more negative
than a genuine "liquid net worth" should, because of this one account's classification.

### AI tools: additive fields, no swapped numbers

- `simulate_cashflow`: the projection's own starting balance still uses `cash_balance_base`
  (ADR 2026-07-02 stands — margin debit belongs in the runway pool). Three new fields ride
  alongside it: `bank_cash_base`, `brokerage_cash_base`, `liquid_net_worth_base`. The tool
  description now tells the model never to say "your cash is X" using the broad figure alone.
- `get_net_worth_history`: no logic change — it already returns `classes` verbatim, so the new
  `classes["brokerage_cash"]` band flows through as a genuinely distinct, clearly-named field
  alongside the now-narrower `classes["cash"]`. The tool description was rewritten to spell out
  the split explicitly, since the model previously had no way to know `classes.cash` used to be
  the broad figure.

### Frontend

- `netWorth.tsx`'s Overview "Liquid cash" KPI now reads `history`'s latest point
  (`classes.cash + classes.brokerage_cash`, already fetched by this widget group) instead of
  re-deriving from `usePortfolio` rows. `usePortfolio.ts`'s row classification (every non-
  property/non-receivable asset account → `asset_class: 'cash'`) is **deliberately left
  unchanged** — narrowing it would also narrow the allocation donut, the investment-book split,
  and every other rows-based consumer, none of which asked for this change. The KPI is the one
  place actually labeled "cash" to the user without also being labeled by account type, so it's
  the one place that needed the backend's now-narrower band.
- `frontend/src/components/tabs/accounts/categories.ts`'s `TYPE_GROUP` is **left unchanged** —
  it is pure display grouping for the Accounts sidebar (group by literal account type), a
  different concern from net-worth banding, and it already puts brokerage/crypto under
  "investments." It was the ALREADY-CORRECT reference the owner trusted; nothing to fix.
- `frontend/src/utils/constants.ts` gains a `brokerage_cash` entry in `ASSET_CLASS_COLOR`/
  `LABEL`/`ORDER` so the two net-worth-composition stacked-bar charts that read
  `history.classes` (`netWorth.tsx`'s composition mode, `trackingBudgeting.tsx`'s hidden
  Insights-demo composition widget) render the new band instead of silently dropping its dollar
  value from the stack. The existing `cash` label stays "Cash" (not "Bank cash") because it is
  SHARED with row-based usages where `asset_class: 'cash'` still means the broad bucket.

## Consequences

- `classes["cash"]` is a breaking-shape change for any reader that assumed it was the broad
  figure. Every consumer was enumerated before this landed (see the task's step-1 audit); none
  needed a compensating fix beyond the ones listed above — `net_worth`/`net_worth_history`'s
  totals are unaffected (brokerage_cash still sums into `net_worth` via `classes.values()`).
- A future account type that is genuinely "cash-like at a broker" (e.g. a money-market fund
  held inside a brokerage account, currently typed `brokerage`) stays in `brokerage_cash`, not
  `bank_cash` — correct under this scheme, since the settlement-cash sub-balance really does
  live at the broker.

## Evidence

`tests/test_cashflow_liquid_net_worth.py` (bank/brokerage/liquid-net-worth splits, margin-debit
netting, short vs. long-term liability treatment, `cash_balance_base` pinned unchanged),
`tests/test_positions_stores.py::test_breakdown_as_of_splits_brokerage_cash_from_bank_cash`,
`tests/test_tools_cashflow_params.py::test_simulate_cashflow_exposes_bank_brokerage_liquid_breakdown`.

Real data (`~/.open-net-worth/data`, read-only verification): `bank_cash_base` ≈ +S$185.30,
`classes["brokerage_cash"]`/`brokerage_cash_base` ≈ -S$2,800.00, `cash_balance_base`
≈ -S$9,500.00 (unchanged math, matches `bank_cash_base + brokerage_cash_base` exactly),
`liquid_net_worth_base` ≈ -S$85,000.00 (see the real-data finding above).

## Agent provenance

Designed and implemented by Claude (Sonnet 5) on `fix/approvals-never-expire`, from an
owner-approved design brief. The short-term-liability type set (`bnpl` in, `personal_loan`/
`margin` out) and the decision to leave `usePortfolio.ts`/`categories.ts` unchanged were judgment
calls made by pattern-matching existing codebase precedent, flagged above rather than asserted
silently.
