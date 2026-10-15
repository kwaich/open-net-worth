---
number: 0020
title: First-class bonds — computed marks, own display band, and coupons as confirmed income (never silent cash movement)
status: Accepted
date: 2026-06-21
deciders: Lucius (Claude, Opus 4.8) under operator AFK-autonomous mandate ("design RFC + UI + implement E2E")
---

# 0020. First-class bonds — computed marks, own display band, coupons as confirmed income

## Context

`bond` was a recognised `asset_class` but a second-class one: it **folded into the `equity`
display band**, had **no metadata** (face value, coupon, maturity), and **no fixed-income
valuation**. The operator asked for all bond types tracked as first-class holdings:
individual market bonds, Singapore Savings Bonds (SSB), T-bills, bond ETFs, and fixed
deposits / CDs. Depth chosen: "first-class holding" (metadata + correct current value + own
band + coupon income) — explicitly NOT the analytics tier (no YTM / duration / convexity /
yield-curve / credit / tax-amortization). Full design: `.claude/rfcs/bonds-first-class.md`.

Three decisions bend or extend stated conventions and are recorded here.

## Decision

### 1. Coupons are CONFIRMED income, never autonomously moved cash

The "first-class holding" option promised "coupons auto-book into the Income panel." We
deliberately **do not** ship a scheduled job that silently books coupon cash into a wallet.
`CLAUDE.md`'s security posture is *money movement always confirms* (external-MCP money
movement always confirms; the income choke point requires a destination account). A daily
process that moves money with no human in the loop is exactly that smell.

Instead: the system **knows** the coupon schedule (`fixed_income.coupon_schedule` /
`next_coupon`, surfaced in the forecast and `summary()`), and a coupon is **recorded in one
confirmed step** through the **existing** income choke point
(`income.apply_income_change(type="record", income_kind="interest")`) — which already books
the cash leg via `ledger.create_transaction` and is idempotency-keyed. The forward forecast
(`income.forward_rate_for_position`) now includes coupon / accretion income so the Income
sub-tab projects it. A deterministic `coupon_reminder` notify task (mirroring `vest_reminder`)
is a **deferred fast-follow** — a notification nicety, not a prerequisite, since recording +
forecast + schedule data all ship now.

**WHY this matters (Agent provenance):** an autonomous agent building "auto-book coupons"
the literal way would have created a scheduled writer that moves money unattended. The honest
first-class behavior under this codebase's invariants is *know the schedule, confirm the
booking* — so that is what shipped.

### 2. Bonds get their OWN display band, valued on the rows-path (not lot-backed reconstruction)

`ledger._MARKET_BAND` and `portfolio_history._ASSET_CLASS_TO_BAND` change `bond → "bond"`
(was `"equity"`). Bond is **not** added to the lot-backed `_MARKET_BANDS` reconstruction set
in `portfolio_history` / `returns`. Rationale: computed-mark bonds (T-bill / FD / SSB) have
**no historical `price_history` series** — only a current computed mark — so they cannot be
reconstructed onto past dates. Treating bond like `property` (current-qty × as-of price on the
rows-path of `_breakdown_as_of`) keeps **current** net worth and allocation correct; bond ETFs
(Yahoo-priced) also chart historically, while computed-mark bonds simply have no pre-today
historical points. Consequence: **bonds are excluded from the TWR/MWR performance series in
v1** — performance analytics for fixed income is the next tier.

### 3. Fixed-income valuation reuses the existing `qty × price × quote_per_unit` identity

No new valuation formula. Holdings are standardized so the existing fold yields the right
number: `quote_per_unit = face/100`, `price` = percent of par. Only SSB / T-bill / FD need a
**computed mark** (par / linear accretion / principal+accrued), supplied by the new
`services/fixed_income.py` leaf and injected at the TOP of `positions._resolve_price` (a
deterministic computed mark out-ranks a stale manual price). Market bonds and bond ETFs return
`None` from `computed_mark` and fall through to the normal manual/live ladder. Bond metadata
lives in a nested, default-`None` `bond_terms` on the instrument (additive, no migration —
same pattern as `constituents` / `dividend_rate_annual`); the store factory
`_coerce_bond_terms` is the single coercion authority.

## Consequences

- **Positive:** all four bond types tracked with correct current value; bonds visible as their
  own band/allocation slice; coupon income forecast + recordable; `GET /api/fixed-income` +
  `get_fixed_income` tool answer "what do I hold / what matures / what income is coming"; zero
  data migration; money math stays in `services`, layering intact (`fixed_income` is a leaf).
- **Negative / limits (v1):** no historical marks for computed-mark bonds → not in the
  pre-today stacked chart; bonds excluded from TWR/MWR; SSB step-up modeled as a single rate;
  accrual is actual/365 simple interest (tracking-grade, not settlement-grade); `coupon_reminder`
  task deferred.

## Follow-ups

- Deterministic `coupon_reminder` notify task seeded on first bond.
- Make `bond` a lot-backed TWR band once a historical mark series exists (the "+ valuation &
  yield" tier): YTM / current yield / accrued-interest-on-trade.
- SSB true step-up coupon schedule; configurable day-count.
