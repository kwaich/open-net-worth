# ADR: Investment as a first-class transaction kind

- **Status:** Accepted (design lock) — 2026-07-02
- **Owner:** the repo owner (with Claude)
- **Supersedes:** the `CATEGORY_TO_KIND["investment"] = "transfer"` mapping and the
  hardcoded non-spending sets.

## Context

Investment buys/sells were modeled inconsistently:
- `category=investment` mapped to `kind=transfer` (a vestigial `CATEGORY_TO_KIND` entry),
  while the real trade importer (IBKR) books the cash leg as `kind=expense` (buy) /
  `kind=income` (sell). The two disagreed.
- Net-worth-neutrality and spending-exclusion rode on **hardcoded key sets**
  (`NON_SPENDING_EXPENSE_CATEGORIES = {"investment"}`, `_NON_SPENDING_KEYS`,
  `CATEGORY_TO_KIND["investment"]`).

**The structural truth.** A `transfer` is two *cash* legs (`from_amount/from_currency →
to_amount/to_currency`) — built for cash↔cash, including FX. An investment buy's other side
is **not a currency amount** — it's a **position** (e.g. 4 shares of VOO). So an investment
buy is really **one cash leg** (from a cash account) **plus a position/lot** (recorded in the
lots system) — *not* two cash legs, and *not* a plain expense (an expense's cash-out is
consumed with nothing offsetting it; an investment's cash-out is offset by the lot →
net-worth-neutral).

Among all net-worth-neutral outflows, investment is **structurally unique**: savings, property,
and loan-principal all move cash between *accounts* (they are genuine `transfer`s), but an
investment moves cash into a **position** (shares are tracked as lots, not as a cash-balance
account). So it is the one case the account-based `transfer` cannot express.

It must be interpreted correctly by **three** independent systems: net worth (neutral),
budget (not spending), and cash flow (a cash *allocation*, not burn).

## Decision

Introduce **`investment`** as a first-class transaction kind: *a cash leg bound to a position
acquisition or disposal.*

Kinds become (each is a net-worth effect):

| kind | net worth | shape |
|---|---|---|
| `income` | + | one cash leg in (from outside) |
| `expense` | − | one cash leg out (consumed) |
| **`investment`** | **0** | **one cash leg + a mandatory linked lot** |
| `transfer` | 0 | two cash legs between your accounts |
| `adjustment` | n/a | balance correction |

Rules:
- **v1 scope = classification-only (LIGHTER).** A `kind=investment` row is a single cash leg
  (buy → cash out / `from_account`, sell → cash in / `to_account`). The lot link is
  **recommended but not enforced in v1** — the existing D3 convention (log the cash leg
  alongside the lot acquire/dispose) is unchanged, so agents/importers keep their flow.
  The **mandatory-lot invariant** (a `kind=investment` row without a linked lot is invalid) is
  the recorded *target* for a later hardening pass, **not enforced now**.
- Direction is read from the cash leg: **buy** = cash out (`from_account`), **sell** = cash in
  (`to_account`).
- `investment` becomes a **reserved kind name** (`_KIND_NAME_KEYS`), so the *literal key*
  `investment` can't be a category key (a kind name can never be a category key — the
  disjoint-namespace rule). Only that one redundant key is gone.
- **A `kind=investment` transaction CAN carry a category** (correction, 2026-07-02). A category
  is **classification** ("what is this?") — a user-chosen label like `retirement`, `growth`,
  `house-fund`, applicable to *any* kind — NOT the spending/budget axis. So investment rows keep
  the category *field*: `investment` is removed from `_NO_CATEGORY_KINDS`, and the create/import
  choke no longer strips it; the value is entirely the user's choice (their own categories +
  create-new). **Budgetability is a separate axis:** a category is budgeted only if an **envelope
  links to it** (category ≠ envelope). So an investment's category may be unbudgeted (investing
  draws RTA) or budgeted (draws its envelope) — the user's call, independent of the category
  merely existing. This also makes envelope decision #9 (envelope → kind link) unnecessary:
  budgeting investing = an envelope linking to an investment *category*.
- **Delete** the hardcoded classification sets — classification is now kind-based:
  `CATEGORY_TO_KIND["investment"]`, `NON_SPENDING_EXPENSE_CATEGORIES`, and the `investment`
  member of `_NON_SPENDING_KEYS`.

## How the three systems read it (all off `kind`)

- **Net worth:** `kind=investment` is neutral — the cash leg and the linked lot cancel.
- **Cash flow:** it *reduces the cash balance* (real cash left your spendable account) but is
  **excluded from spending / burn**; shown as an "→ Investing" allocation line, never mixed
  into expenses. `is spending? == (kind == "expense")`.
- **Budget / Ready-to-Assign:** excluded from spending/burn totals (not consumed).
  **Interim (this ADR): RTA-neutral** — a `kind=investment` row touches neither an envelope nor
  RTA (`_inflows_to_assign` counts only `kind=income`; a buy is neither expense nor income, so it
  falls through — the code already does this, no patch added).
  **End-state: investing draws Ready-to-Assign** — a *buy reduces RTA*, a *sell increases RTA*,
  because invested cash leaves (and returned cash re-enters) the **spendable** pool. Buy and sell
  are **symmetric money-out / money-in**, not special cases. This is delivered NOT by a per-kind
  term but by moving RTA to the **balance-based identity** `RTA = spendable_cash − Σ envelope_balances`
  (own ADR: *balance-based RTA*), under which a buy is just cash leaving a spendable account into an
  off-budget holding (the lot) and a sell is cash returning — no investment-specific rule anywhere.
  This resolves the long-standing "is investment an expense?" tension — it isn't an expense, but it
  draws the same pool you budget from, which is why it *felt* like one.
  **Also deferred to `category ≠ envelope`:** a per-investment *target* (planning
  "$1,000/mo → VOO" as a fundable line) — kinds aren't envelopes yet.
- **Tax:** unchanged. Realized gains come from the lots engine; `kind=investment` is not
  income. Dividends/interest stay `kind=income` (new money from outside), taxable.

## Migration (idempotent, dry-run + `--commit`)

1. Cash-leg transactions with `category=="investment"` **that have a linked lot/position** →
   `kind=investment` (drop the now-reserved category, keep the lot link).
2. IBKR/importer trade cash legs currently `kind=expense`/`income` + `category=investment` →
   `kind=investment`.
3. **DCA `commitment_*` categories stay `kind=expense` for now** (their budget envelopes keep
   working through the interim). They migrate to `investment` + envelopes when decoupling
   lands. *(This avoids a budgeting regression between the two ADRs.)*
4. Report (not auto-fix) any `category=investment` row with **no** linked lot — it can't become
   a valid `kind=investment` and needs manual attention.

## Sequencing

1. **This ADR → `kind=investment`** (backend leg/lot validation + net-worth math + cash-flow
   + choke points + migration; then frontend; then E2E). Investment correct for net worth +
   cash flow; **RTA-neutral in the interim** (buy↓/sell↑ arrives with the balance-based RTA ADR —
   no per-kind patch); per-investment *targeting* deferred.
2. **`category ≠ envelope`** (next ADR): envelopes become first-class, linking to 1+
   categories/kinds; re-enables per-investment budgeting; migrate DCA commitments.

## Consequences

- Touches the **ledger core**: kind enums, leg/lot validation, net-worth math, the
  create/update choke points, importers (IBKR/email), AI tools/MCP, cash-flow, and the
  transaction UI — plus ~80 tests that assume 4 kinds. **The test suite is the guardrail;**
  characterization tests (`test_char_ledger_math`, `test_char_split_safety`) must stay green.
- **Hard to reverse** (kind is stored on every row). Accepted: the correctness spans three
  systems and the mandatory-lot invariant is materially stronger than the flag.
- **Interim:** investment is RTA-neutral — buy↓/sell↑ lands with the balance-based RTA ADR; and
  a specific investment can't be *targeted* (a planned "$X/mo → VOO" line) until the decoupling
  ADR ships.

## Alternatives considered

- **`expense` + a non-spending flag/marker** (what the app half-does today): lighter, but
  net-worth-neutrality isn't guaranteed — a flag can be mis-set and there's no mandatory lot.
  Rejected for the weaker invariant.
- **`transfer`:** structurally wrong — two cash legs / two currencies; an investment's other
  side is a position, not a cash amount. Rejected.
- **A general `neutral` kind:** rejected — the owner prefers the explicit `investment`, and
  since property/loans/savings are genuine account-based `transfer`s, investment is
  structurally *unique* (position-bound), not a family that needs a general neutral kind.
