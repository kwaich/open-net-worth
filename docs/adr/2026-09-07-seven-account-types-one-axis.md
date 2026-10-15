---
title: Seven account types on one axis — the type decides band, spendability and budget; cash on hand is one number
status: Accepted
date: 2026-09-07
deciders: orchestrating Claude agent + repo owner (design approved in-session, 2026-09-07)
---

# 2026-09-07. Seven account types on one axis

## Context

Two owner complaints on 2026-09-06 with one root. First: "Cash on hand" read S$37,756 on the
Accounts tab and S$26,487 on the Assets tab and Plan → Cash Flow — the second figure netted a
brokerage account's drawn margin (−S$11,269) into spendable cash (`cashflow.cash_balance_base`,
per ADR 2026-07-02), and ADR 2026-07-31 had added `bank_cash_base` / `brokerage_cash_base` /
`liquid_net_worth_base` to *explain* the divergence rather than remove it. Second: 26 account
types, of which 15 have no behavior at all, plus five classification axes on top of `type`
(`kind` stored not derived, a free-form `asset_class` that becomes a band key, `locked`,
`on_budget` override, sidebar `group` override). The type did not decide the band: a
`pension` account with no `asset_class` was counted as cash; reachability checked type names
(`cpf`, `fixed_deposit`) that never existed.

Financially, a margin debit is a loan secured by securities. It follows its collateral the way
a mortgage follows the property; nobody nets a mortgage into cash on hand. Brokers present it
the same way (IBKR net liquidation = securities + cash, cash negative when drawn). Peers agree
on taxonomy: Actual Budget has no account type (name + `offbudget`); YNAB's 13 types collapse
to three behaviors and its six loan subtypes share identical rate/payment fields; Wealthfolio
has four types with settlement cash inside the securities account.

The owner's charge: "We must be clear — even by ourself." Not tooltips; one definition.

## Decision

We will classify an account by exactly one stored field, `type`, with seven values, and derive
everything the app asks of an account from it:

| Type | Kind | Spendable | On budget | Band |
|---|---|---|---|---|
| `cash` | asset | yes | on | `cash` |
| `fixed_deposit` | asset | no | off | `fixed_deposit` |
| `investment` | asset | no (runway toggle) | off | positions by instrument class + `settlement_cash` (may be negative — margin) |
| `retirement` | asset | never | off | `retirement` |
| `receivable` | asset | no | off | `receivable` |
| `credit` | liability | — | on (payment envelope) | `credit` |
| `loan` | liability | — | off | `loan` |

`kind`, `asset_class`, `locked`, `on_budget` and `group` are removed from the account and from
every client-facing schema. Physical things (`real_estate`, `vehicle`, `collectible`,
`other_asset`) are owned assets, not accounts. Loans are not sub-typed; the two facts that
distinguish one loan from another for behavior are the owned asset that links to it and its
`apr` (already a field). **Cash on hand is Σ `cash`** — one function, one number, in every
surface. Margin is negative settlement cash inside `investment`: it reduces the Investments
value and enters runway only when the Investments toggle brings its collateral in. Net worth
excl. retirement is shown beside net worth, never instead of it.

Migration is one fail-closed Alembic revision with an exhaustive mapping and a tested
invariant: `net_worth()` before == after, to the cent.

## Consequences

### Positive
- One number per concept. Accounts, Assets and Plan agree on cash on hand; Assets and
  Investments agree on positions; the seam (settlement cash) is a visible row, not a silent net.
- Every liquidity figure (cash on hand, runway, RTA, net worth excl. retirement) is a sum over
  types. No special-case function can drift from the band.
- The picker states the mental model in seven lines; three modal fields (band, locked,
  on budget) disappear; three AI tool parameters disappear.
- Reachability, on-budget and banding cannot reference a type that does not exist.

### Negative
- **Runway and RTA visibly jump on upgrade** by the margin amount for anyone with a drawn
  margin debit (the owner: +S$11,269). The old number was wrong; the release note says so.
- Property/vehicle accounts are converted to owned assets with an unlinked loan; the user
  re-links on the Assets tab. One-time, one row in the owner's book.
- The per-class runway toggles (equities / crypto / commodities) collapse to one Investments
  toggle — per-class toggles cannot allocate a single account's settlement cash honestly.
- The payment-envelope engine widens from `credit_card` to every `credit` (BNPL, line of
  credit) — ADR 2026-07-09's v1 restriction ends.
- 131 test files reference the old type strings; every one is touched.

### Follow-ups
- Spec: `docs/superpowers/specs/2026-09-07-account-taxonomy-design.md` (the build plan).
- Update `docs/DATA_MODEL.md`, `docs/PLANNING_MODEL.md`, `docs/INVARIANTS.md`,
  `docs/ai/TOOL_CONVENTIONS.md`, product docs, and the `CLAUDE.md` invariant lines naming
  `brokerage_cash`.
- Supersedes ADR 2026-07-31 (liquid-net-worth bands) in full and ADR 2026-07-02 in part
  (brokerage cash on-budget; margin netted into spendable cash — the balance-based RTA identity
  itself stands). Widens ADR 2026-07-09 (payment-tracked cards).

## Alternatives considered

- **Keep the numbers, add tooltips / relabel ("Bank cash on hand" vs "Cash on hand")** —
  rejected: explains a wrong definition instead of fixing it; the owner refused it explicitly.
- **Keep `checking` / `savings` as separate types (Monarch, Plaid, YNAB enum)** — rejected: no
  peer attaches behavior to the distinction; it is a label and the name carries it.
- **Keep loan subtypes** — rejected: YNAB's six subtypes share identical fields; the only in-app
  use was a rate stereotype in the AI snapshot, and `apr` is the real fact.
- **`locked` flag on `cash` instead of a `fixed_deposit` type** — rejected: hidden axes are the
  complaint; a type is visible in the picker and the band. The one deliberate divergence from
  peers, justified because we compute runway and they do not.
- **Keep an `on_budget` override for brokerage sweeps** — deferred: real but rare; the escape
  hatch is a `cash` account named for the sweep. Add back on demand.
- **Per-account "exclude from net worth" for CPF** — rejected: silently changes what the
  headline means and breaks reconciliation; the liquidity question is answered by a derived
  "excl. retirement" line beside net worth.
- **Loans as marked liabilities (Wealthfolio)** — rejected: our loans have real payment
  transactions through the ledger; YNAB keeps them as accounts too.
- **Two-level type + subtype hierarchy** — rejected: that is what five overlapping fields
  already were.

## Agent provenance

- **Decided by:** orchestrating Claude agent, in dialogue with the repo owner.
- **Lane / branch:** `feat/account-taxonomy`.
- **Alternatives weighed:** relabel/tooltip; keep checking/savings; keep loan subtypes;
  `locked` flag vs type; `on_budget` override; exclude-from-NW toggle; marked liabilities;
  type+subtype.
- **Driving artifacts:** owner complaint (three "cash on hand" figures, 2026-09-06); peer
  research 2026-09-07 (Actual `loot-core/src/types/models/account.ts`, YNAB OpenAPI
  `AccountType`, Wealthfolio `accounts_constants.rs`); ADR 2026-09-06-assets-tab-faithful-port
  decision 3; spec `2026-09-07-account-taxonomy-design.md`.
- **Why this way (one line):** the type should answer every question the app asks of an
  account and nothing else — then every number is a sum over types and none can disagree.
