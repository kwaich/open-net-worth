# 2026-07-31 — Money is computed server-side; consumers render, never compute

**Status:** Accepted
**Owner:** the maintainer (reported the residue and scoped it: "start with the overview widgets,
none of them should use local calculation or results at all")
**Supersedes in scope:** extends `2026-07-31-single-source-net-worth.md`, which fixed the
headline only.
**Touches:** `backend/services/portfolio_rows.py` + `portfolio_analytics.py` +
`investments_book.py` + `networth_stats.py` + `planning_summary.py` + `reports_rollup.py` +
`dividend_summary.py` + `commitments_summary.py` + `cashflow_projections.py` +
`spend_search.py` (all new), routes added to the matching DOMAIN routers in `backend/api/`,
`backend/services/ledger.py` (`net_worth_bands()`, `row_base_value(rates=)`),
`backend/ai/tools/portfolio.py` + `screen.py`,
`frontend/src/hooks/serverState/aggregates.ts` + `keys.ts`, `frontend/src/types/aggregates.ts`,
`frontend/src/hooks/usePortfolio.ts` (`compute()` deleted),
`frontend/src/components/tabs/overview/useWidgetData.ts` (replaces the deleted
`OverviewDataContext.tsx`), `frontend/src/components/tabs/overview/widgets/*.tsx` (all six),
`frontend/src/components/tabs/investments/*`, `frontend/src/components/tabs/accounts/useAccountsLedger.ts`,
`frontend/src/components/shared/TopDailyMovers.tsx`, `frontend/src/utils/constants.ts`,
`frontend/src/RouterShell.tsx`

## Context

The single-source-net-worth fix pointed the Overview HEADLINE at `GET /api/net-worth`. The
owner then read the rest of the tab and found the fix had covered one figure out of many:

> the headline number is API-based … but the rest of the widget still calculates locally.
> `useKpiFigures` reduces over `usePortfolio()` rows for Today, Unrealized P&L and
> Liabilities; `useAllocRows`/`useReachRows`/`useVenueRows` sum rows client-side. Only Liquid
> cash reads the backend's bands. And it's not just this widget — client-side money math is in
> `investing.tsx`, `trackingBudgeting.tsx`, `reports.tsx`, `overviewCashflowCore.tsx`,
> `cashflowPlanning.tsx` too. That's the same failure mode as the S$337k bug, in ~6 more places.

That is the correct diagnosis. The root was deeper than any one widget: `usePortfolio.compute()`
re-implemented the backend's per-row valuation in the browser — `qty × price × quote_per_unit`,
FX-converted per leg, cost basis, unrealized P&L, day P&L — and every widget then ran its own
`.reduce()` on top of it. Two independent implementations of the same money math, drifting
independently.

Auditing the tab turned up the drift the duplication had already produced:

1. **Allocation bands counted unvested equity that the denominator excluded.** `classBreakdown`
   added every row to `classes` but excluded unvested rows from `liquidity`, while the
   denominator (`portfolioTotal`) excluded them from both. On the owner's book the equity band
   read S$246,000 against the backend's S$179,000 — the ~S$67K unvested grant again — and the
   allocation percentages summed to ~124%, not 100%.
2. **The concentration flag ranked a position the user does not own.** "Top equity" filtered on
   `asset_class === 'equity'` with no vesting filter, so it selected the unvested RSU grant and
   divided it by a total that never contained it.
3. **"This month" charged a reconciliation gap to the month.** The Summary card differenced
   today's `net_worth()` against a `net_worth_history()` point. Those two computations are known
   not to reconcile exactly (the open follow-up on the prior ADR); on the owner's book that gap
   alone was ~S$3K of a reported monthly change.
4. **The recurring-commitments total added money across currencies.** It summed raw
   `transaction.amount` over scheduled tasks with no FX conversion and printed the result as a
   base-currency "/mo" figure.
5. **The "Cash" allocation band disagreed with the Accounts tab.** `toAccountRow` folded
   liability accounts into `asset_class: 'cash'` as negatives, so a credit-card balance was
   netted into the cash band.

## Decision

**A widget renders a number the server computed. It never computes one.** The frontend may
sort, slice, filter, label, colour and lay out — none of which can change a monetary value. Any
sum, difference, percentage, average or FX conversion lives in a service.

The rule is not UI-specific: the AI tool surface is held to it too. `get_portfolio` used to hand
the model `quantity`, `price` and `currency` with no base value and **no FX rate anywhere in the
payload** — it could not convert correctly, only guess.

**Services named for their data**, each behind a thin route in its own DOMAIN router (there is
no `api/overview.py` — see "Named for data, not screens" below):

| Module | Endpoint | Carries |
|---|---|---|
| `portfolio_rows.py` | `GET /api/portfolio` | the live-priced row projection — the server-side replacement for `usePortfolio.compute()`, plus `holdings_by_account` |
| `portfolio_analytics.py` | `GET /api/portfolio/analytics` | allocation · reach · venues · KPIs · risk flags · debts · FX exposure · gains · movers · treemap · drift · the invested `book` |
| `networth_stats.py` | `GET /api/net-worth/stats` | the series + range deltas · saved-vs-market · month-to-date · composition |
| `planning_summary.py` | `GET /api/planning/summary` | envelopes · goals · month-over-month moves · spend calendar · recurring · cash flow · safe-to-spend |
| `reports_rollup.py` | `GET /api/reports/rollup` | multi-month rollups · stacked series · category table · merchants · savings rate · flow |
| `dividend_summary.py` | `GET /api/income/dividends` | dividends received YTD · upcoming · 12-month forecast |
| `commitments_summary.py` | `GET /api/commitments/summary` | the feed + its next-30-days total + coverage |
| `cashflow_projections.py` | `GET /api/cashflow/projections` | passive-income coverage · the cash-runway depletion curve |
| `spend_search.py` | `GET /api/transactions/spend-by-week` | a free-text spend query bucketed into weeks |
| `investments_book.py` | `GET /api/portfolio/book` | the invested book — sections · weights · concentration · YTM · cross-account merge |

Split by data source rather than served as one blob so a widget pays only for what it shows:
the portfolio read refreshes live prices, the reports read walks several months of planning
data, and most dashboards mount neither.

### Named for data, not screens (revision, same day)
The first cut of this shipped `/api/overview/*` and a `services/overview/` package. That names
a CONSUMER, not a resource, and it showed: `/api/overview/portfolio` returned the same rows as
`/api/portfolio/rows`, so the Overview tab downloaded the row set twice and the projection ran
twice per refresh. Endpoints are now named for the data they return, and the analytics payload
omits `rows` entirely (the caller already has them from `GET /api/portfolio`), referencing them
by `row_key`.

### Widgets are standalone (revision, same day)
`OverviewDataContext` — a provider wrapping the whole grid — was deleted. On a user-composable
dashboard it was wrong twice over: it fetched the portfolio, the net-worth series and
commitments before knowing which widgets were mounted (so a dashboard holding only the coffee
widget paid for a live-priced projection), and it made widgets non-standalone, which defeats
the point of a widget picker. Each widget now declares the resource it renders; `state/queryCache`
dedups by URL, so twenty widgets sharing a resource cost ONE request and a dashboard mounting
none costs nothing.

**Live pricing moved server-side.** `rows.projected_rows()` calls `prices.fetch_live()` and
re-hydrates positions with the result, so the whole widget group moves in step. Previously the
browser fetched prices and recomputed, which is why the headline could come from one snapshot
and the KPI strip from another. A provider outage degrades to last-known prices (`is_live:
false`) — the same fallback the browser had, now in one place.

**Reuse over re-implementation.** The projection folds each row through
`ledger.row_base_value` — the ONE canonical market fold already shared by net worth, the
cash-flow baseline and the X-ray — rather than adding a fourth copy of `qty × price × qpu × FX`.
Account rows are banded by `_account_band()`, which applies the same classification
`ledger._breakdown_as_of` uses, which is what makes the allocation reconcile.

**New `ledger.net_worth_bands()`** — today's `{classes, liabilities, accounts, net_worth,
unvested_base}`, the shape `net_worth_history` emits per month. Exists so a caller needing one
band (the "Liquid cash" KPI is `cash` + `brokerage_cash`, ADR
`2026-07-31-liquid-net-worth-bands`) neither re-derives the split nor pays for a full monthly
reconstruction.

**The five drift bugs are fixed, not ported**: allocation and reach are both vested-only;
concentration ranks vested positions only; month-to-date compares two points of the SAME series
(`basis: "history"`) while the headline beside it stays `net_worth()` — a level, not a
comparison; the recurring total converts each amount to base first; liability accounts get
their own `liability` band.

**`BreakdownRow` gains an optional `pct`.** The shared list/bar/pie renderer used to divide
`value / total` itself for the percentage it prints beside each row — a second computation of a
figure the aggregate already carries. Every Overview caller now passes the server's `pct`
through; the local division survives only as a fallback for a caller without one.

**`TopDailyMovers` gains a `preAggregated` + `maxAbs` path.** Collapsing an instrument's
positions into one mover is a sum of money, so it happens in `portfolio._movers()`. Callers
still handing over raw rows (the design Gallery) keep the legacy client-side collapse.

## Consequences

- The Overview's allocation now sums **exactly** to net worth (verified on the owner's real
  book: S$79,500.00 both sides), and every band matches the backend's own breakdown.
- Overview widgets no longer depend on `usePortfolio()` for money. `Overview` still takes
  `rows`, used ONLY to gate the empty state and resolve a clicked label back to a row.
- **Prices refresh at the aggregate's cadence, not tick-by-tick in the browser** — accepted, and
  the same trade-off the prior ADR made: a number briefly stale beats a number silently wrong.
  Because numerator and denominator now come from one snapshot, percentages no longer jitter
  against their own numerator.
- The reports group went from 12 parallel per-month fetches + a client-side reporting layer to
  one read; the budget group from four independent fetches to one.
- **`usePortfolio.compute()` still exists and is still a duplicate** — for the Investments,
  Accounts and Intel tabs. This ADR is scoped to the Overview at the owner's explicit
  instruction. The duplicate is now unused by the Overview but not yet deleted.

### What deliberately stayed client-side
Two categories, neither of which can change a monetary figure the user reads:

- **CSS bar widths** (`value / max * 100` as a percentage width). These are pixel layout. Where
  the same ratio is also PRINTED as a percentage, it now comes from the server (`BreakdownRow.pct`).
- **The FX widget's cross-rate** (`rateVsBase(b) / rateVsBase(q)` over `GET /api/prices`). That
  derives a displayed exchange RATE from two server-supplied rates — it is not an aggregate over
  the user's holdings, and the "$X held" figure beside it IS server-computed. A server-side
  FX-cross endpoint would be a rates service, not Overview money math.

## Phase 2 — the remaining tabs (same change, completed after the owner asked for "the rest")

The Overview fix left `usePortfolio.compute()` alive for Investments, Accounts, Intel, Lucius
and the command palette. Finishing it turned out to be *smaller* than phase 1, because the root
was already built: the row projection was promoted out of `services/overview/rows.py` into
**`backend/services/portfolio_rows.py`**, served by **`GET /api/portfolio/rows`**.

- **`usePortfolio.compute()` is deleted.** The hook now fetches valued rows and renames the
  fields (`adaptServerRow`). Three browser round trips (`/api/portfolio` + `/api/accounts` +
  `/api/prices`, the last a real Yahoo/CoinGecko call from the client) collapsed to one, and the
  browser no longer talks to the price vendor at all.
- **`backend/services/investments_book.py`** + `GET /api/investments/book`: grand total, per-row
  weights, per-class sections, today's and unrealized P&L, single-name concentration,
  allocation-vs-target, bond YTM, and the cross-account instrument merge. `useInvestmentsBook`
  is now an adapter with no arithmetic.
- **Accounts** dropped its own copy of the fold (`holdingsFromPositions`, whose comment admitted
  it was "the same formula as usePortfolio.compute()") for the server's `holdings_by_account`.
- **Intel, `useLuciusContext` and the command palette** needed no edit — they only ever *read*
  row fields, so they were fixed by the projection alone. Verified, not assumed.
- **`ledger.row_base_value` gained an optional `rates`.** It re-read the FX cache per call, so a
  whole-book fold could straddle two FX snapshots — a row's market leg converted at different
  rates from its own cost leg. The projection now pins one rate set for the response.
- **`Masthead` lost its `portfolioTotal` prop.** The percentage and its denominator now come
  from the same server snapshot, so they cannot describe two different moments.

**Deliberately still client-side:** the subtotal of a set the USER filtered (a search box, an
account chip). That is a selection the server has no name for, so summing already-server-valued
rows for it is not a second implementation — there is nothing for it to drift from. Every
figure with a canonical definition is server-owned.

### Phase 2 evidence
`tests/test_portfolio_rows.py` (14) and `tests/test_investments_book.py` (13) — both **ported**
from frontend suites that covered the deleted client folds (`usePortfolio.foreignQuote`,
`.refGrantPl`, `.unvested`, `.propertyVsUnvested`, and `mergeByInstrument`), so the scars they
guarded moved with the code rather than being lost with it: the quote-vs-cost currency split,
unvested-only "since grant" P&L, entitlement-not-reachability gating net worth, and the
cross-account merge's summed-basis P&L %.

Live on an isolated QA clone, all four surfaces agree **to the cent**:
`GET /api/net-worth` ≡ `/api/portfolio` ≡ `/api/portfolio/analytics` ≡ `/api/portfolio/book` (`portfolio_total` = 1,650,000,000.00 on every one), book weights summing
to exactly 100.0%, and `grand == Σrows == Σmerged == Σsections`.

Backend `12,646 passed`. Frontend `3,030 passed`, with only the 11 pre-existing `commitments/`
failures. Repo-wide typecheck errors: 46 → 18.

### Found by looking, not by testing (worth recording)
Two defects survived every check above — 200s, cent-exact reconciliations, a green suite — and
were caught by the owner opening the tab:

- **The reachability chart drew ONE bar at 150%.** Liabilities were being given a liquidity
  bucket, so a credit-card and margin balance summed into `liquid` and drove it NEGATIVE
  (-S$74K against +S$250K illiquid). The widget only draws positive bars, so every holding
  looked unreachable. Auditing the siblings then found **venues** totalling 124% for the same
  reason. Both are now denominator-correct and pinned by a test over all three breakdowns.
- **The Accounts tab read S$331,900 against an authoritative S$275,900.** Two causes:
  `holdings_by_account` counted unvested grant units (an equity-grant account showed S$48.0K
  when S$36.0K of it is not the holder's), and the tab folded its OWN net worth — a fourth
  implementation — replacing a securities account's balance with its holdings rather than
  adding market value to balances. It now reads `GET /api/net-worth`.

The general defect: **a breakdown that filters its numerator but not its denominator lies about
every share it draws.** Two of three had it. The lesson for the next change of this size is that
endpoint-level verification cannot see a chart rendering one bar instead of three — the UI
Quality Gate (`make qa-e2e`, screenshots, empty/loading/error states) is not optional on a change
that touches every widget on a tab, and it was skipped here.

### Follow-ups
- `net_worth()` vs `net_worth_history()` still don't reconcile exactly (banding differences —
  today's bands omit a `crypto` band the fold includes). Inherited from the prior ADR, not
  investigated here; `month_to_date` now works AROUND it rather than charging it to the month.
- `bucket` remains semantically overloaded between vesting and liquidity. Untouched.

## Alternatives considered

- **One `GET /api/overview/aggregates` blob** — rejected: it would make every dashboard pay for
  a live price fetch AND a six-month planning walk regardless of which widgets are mounted.
- **Keep the per-row math in the browser, move only the widget-level sums** — rejected: it
  leaves the deepest duplicate (per-row valuation + FX) in place, which is the layer that
  actually drifted.
- **Have the backend return labels and colours too** — rejected: those are presentation, and
  pushing them server-side would couple the aggregate service to the design system without
  removing any arithmetic from the client.
- **Delete `usePortfolio.compute()` in this change** — rejected: three other tabs read it; the
  owner scoped this to the Overview, and a same-change rewrite of every tab would be unreviewable.

## Evidence

`tests/test_overview_aggregates.py` — 33 tests. The load-bearing ones are reconciliations,
because a reconciliation is exactly what a second implementation silently breaks:

- `test_projected_rows_reconcile_with_net_worth` — the vested row sum equals `ledger.net_worth()`
- `test_allocation_reconciles_with_the_backend_band_breakdown` — band-by-band against
  `net_worth_bands()`, including liabilities as their own band
- `test_allocation_rows_sum_to_the_stated_total`
- `test_concentration_and_allocation_share_one_denominator`
- `test_liabilities_agree_across_every_widget_that_shows_them` (KPI ≡ risk flag ≡ debt list)
- `test_unrealized_pl_total_matches_the_kpi`
- `test_month_to_date_compares_like_with_like` (`basis == "history"`, both ends same series)
- `test_recurring_total_is_converted_to_base_currency`
- `test_drift_excludes_unvested_from_the_investable_mix`
- `test_crossover_divides_the_annual_forecast_server_side`
- `test_runway_trajectory_is_projected_point_by_point` (floored at zero, monotonically down)
- plus per-endpoint smoke over all eight routes

The fixture seeds an **unvested grant** deliberately — a book without one cannot catch this
regression class.

`frontend/src/components/tabs/overview/widgets/netWorth.headline.test.tsx` — rewritten against
the aggregate: serves a payload whose `net_worth` is the incident's true S$98,000 while
`portfolio_total` is the incident's wrong S$120,000, and asserts the headline shows the former.

Live verification against an isolated QA clone (`make qa-data`, port 8094): all eight routes
200, and the Overview's own numbers reconcile against the authoritative figure —
`allocation rows sum == allocation total == portfolio_total == net_worth == GET /api/net-worth`
to the cent, allocation percentages summing to exactly **100.0%** (they summed to ~124% before),
KPI liabilities ≡ the debt list total, KPI unrealized P&L ≡ the gains total, and every range
delta satisfying `delta == saved + market`.

Backend: `make lint` clean (all 5 import-linter contracts KEPT), `12,614 passed / 15 skipped`.
Frontend: Overview suite 58/58; full suite 3,049 passed with 11 pre-existing failures confined
to `commitments/` (timelineStrip, detailActions, detailFunding — untouched here, and already
recorded as pre-existing in the prior ADR). Typecheck: zero errors in any file this change
touches; the repo-wide count went from 46 to 37.

## Agent provenance

- **Decided by:** implementing Claude agent (Opus 5), on `fix/approvals-never-expire`, from the
  owner's report and explicit scope ("start with the overview widgets"). The owner was AFK and
  directed that no clarifying questions be asked, so the judgement calls below were made
  unilaterally and are recorded here for review rather than having been approved in advance.
- **Judgement calls made without the owner:** (a) live pricing moves server-side, accepting the
  refresh-cadence trade-off; (b) the five drift bugs found mid-port were FIXED rather than
  faithfully ported, on the grounds that porting a known-wrong number forward serves nobody;
  (c) liability accounts became their own allocation band, which is a visible UI change; (d)
  seven endpoints rather than one blob.
- **Why this way (one line):** the failure mode is duplicate implementations of money math, so
  the fix is to have exactly one — in `services/`, where the rest of this codebase's monetary
  math already lives (CLAUDE.md: "All monetary math in `services/`").
