# 2026-07-31 — Net worth has ONE implementation; the frontend consumes it

**Status:** Accepted
**Owner:** the maintainer (bug caught live; fix design specified by the owner/orchestrator — read
GET /api/net-worth, keep the portfolio total for allocation math, add a regression test)
**Touches:** `frontend/src/hooks/serverState/useNetWorth.ts` (new), `frontend/src/hooks/serverState/keys.ts`,
`frontend/src/hooks/serverState/index.ts`, `frontend/src/hooks/usePortfolio.ts`,
`frontend/src/state/portfolio.tsx` (untouched, generic pass-through), `frontend/src/RouterShell.tsx`,
`frontend/src/components/tabs/overview/Overview.tsx`,
`frontend/src/components/tabs/overview/OverviewDataContext.tsx`,
`frontend/src/components/tabs/overview/widgets/netWorth.tsx`,
`frontend/src/components/tabs/overview/widgets/overviewCashflowCore.tsx`,
`frontend/src/components/tabs/overview/widgets/cashflowPlanning.tsx`,
`frontend/src/components/tabs/plan/Plan.tsx`, `frontend/src/components/tabs/plan/outlook.tsx`,
`frontend/src/components/tabs/investments/InvestmentsTab.tsx`,
`frontend/src/hooks/usePortfolio.unvested.test.ts`,
`frontend/src/components/tabs/overview/widgets/netWorth.headline.test.tsx` (new),
`frontend/src/components/tabs/overview/Overview.mobile.test.tsx`

## Context

The owner caught this: the Overview headline read **S$132,000** while the backend
(`GET /api/net-worth`) reported **S$107,000** — a ~S$25K gap that was exactly the unvested RSU
grant. This is the SAME duplicate-computation-drift bug class named as a flagged, un-actioned
follow-up in `docs/adr/2026-07-31-unvested-equity-excluded-from-net-worth.md` ("Also filter
`usePortfolio.ts`'s `total`" — rejected there as out of scope for that ADR, explicitly left for
"a separate, explicitly scoped frontend change"). This is that change.

Two independent net-worth computations existed:
- **Backend (authoritative):** `backend/services/ledger.py::net_worth()`, exposed at
  `GET /api/net-worth`. Correctly excludes `bucket="locked"` (unvested equity), returns it
  separately as `unvested_base`. **Had zero frontend callers before this change** (verified by
  grep) — the endpoint existed and was correct, but nothing read it.
- **Frontend (the duplicate):** `usePortfolio.ts::compute()` summed every portfolio row
  (`/api/portfolio` market rows + `/api/accounts` mapped to rows, including liability rows as
  negatives) into `total`. A same-session stop-gap had already tagged `_unvested` rows at
  assembly and excluded them from this sum (regression-tested in
  `usePortfolio.unvested.test.ts`) — narrowing the gap but leaving the fundamental problem: TWO
  independently-maintained implementations of "what is net worth" that can silently re-diverge
  on the next feature (a new asset class, a new bucket, a new exclusion rule the backend gets and
  the frontend doesn't).

## Decision

Net worth has **one** implementation: `backend/services/ledger.py::net_worth()` via
`GET /api/net-worth`. Every frontend surface that renders a "net worth" figure reads it; nothing
re-derives net worth from portfolio rows.

**Consumer audit** (every read of `usePortfolioContext().total` / `useOverviewData().total`,
traced to its actual use):

| Consumer | Classification | Resolution |
|---|---|---|
| Overview headline (`netWorth.tsx` `Headline`) | **Net worth** | reads `useNetWorth()` |
| Overview "Summary" card (`overviewCashflowCore.tsx`) | **Net worth** — also compared against backend `history` points, an apples-to-oranges bug on its own | reads `useNetWorth()` |
| FIRE `current_nw` seed — Overview `Crossover`/`PlanFire` (`cashflowPlanning.tsx`), Plan/Outlook (`useOutlookFire`) | **Net worth** (feeds `/api/fire?current_nw=`) | reads `useNetWorth()` |
| `useAllocRows`/`useReachRows`/`useVenueRows` (asset-class %, liquidity-bucket %, venue breakdown denominators) | Portfolio/allocation total | kept — `OverviewDataContext.portfolioTotal` |
| `Attention` widget (`topEquityPct`, `contingentPct`) | Portfolio/allocation total | kept — `portfolioTotal` |
| Investments `Masthead`'s "% of net worth" (holdings value / total) | Portfolio/allocation total — **deliberately not swapped**: the numerator (`d.grand`) is also rows-derived and LIVE-priced; pairing it with the backend's ~2min-stale figure would make the percentage itself inconsistent, a different bug | kept — `InvestmentsTab`'s `portfolioTotal` prop |

**New hook:** `hooks/serverState/useNetWorth.ts`, mirroring `useProfile.ts`'s shape exactly
(`useQueryCache` + `useDataVersion(['portfolio', 'ledger', 'accounts'])`, added to
`SHARED_READ_ENDPOINTS`/`keys.ts` since it is now genuinely cross-tab — Overview AND Plan both
read it, the same reason `useProfile`/`useAccounts` live there rather than being duplicated
per-caller).

**Rename, not delete, `usePortfolio()`'s `total`.** The consumer audit is genuinely mixed: some
callers need net worth, others need a portfolio/allocation total that must stay in step with its
own numerator (live prices) rather than the backend's slower-refreshing figure. Forcing one
answer for every call site would have been wrong for at least the Masthead case. `PortfolioState.total`
→ `PortfolioState.portfolioTotal`, with a doc comment stating explicitly it is NOT net worth and
pointing at `useNetWorth()`/`GET /api/net-worth`. The rename cascades through every consumer
(`RouterShell`, `Overview`, `OverviewDataContext`, `InvestmentsTab`) so a future re-wiring of a
net-worth surface to this field reads as a visible mismatch (`portfolioTotal` named into a "net
worth" slot), not a silent one. `compute()`'s own internal return field stays named `total` — it
is a local helper, already covered by `usePortfolio.unvested.test.ts`'s literal `.total`
assertions, and its own new doc comment says the same thing.

`Plan`'s `total` prop is **removed entirely** (not renamed) — every one of its uses fed FIRE's
`current_nw` (an (a)/net-worth case), so after the fix `Plan` has no portfolio-total use left;
it calls `useNetWorth()` directly instead of receiving a prop.

## The live-price trade-off (accepted)

`usePortfolio()` fetches `/api/prices` client-side (a real Yahoo/CoinGecko round-trip) so the old
headline updated within seconds of a price move. `net_worth()` reads stored last-known prices,
refreshed by the backend scheduler on its own ~2-minute cadence. Moving the headline to
`GET /api/net-worth` means it now updates on refetch (write-triggered via `useDataVersion`, or the
scheduler's next tick) rather than instantly.

**Accepted by the owner**: "a number that's 60s stale beats a number that's S$25K wrong." This is
the same trade-off already made for `history`/`net_worth_history()` (never live-priced) — the
headline now agrees with the chart it sits above, which it didn't before.

## Consequences

### Positive
- Net worth cannot drift between surfaces again by construction — there is one function that
  computes it, and every "net worth" render is a read of its output, not a re-derivation.
- The Overview headline now agrees with its own trend chart (`history`, always backend-sourced)
  and with the FIRE projection seeded from the same figure — three previously-independent
  numbers collapse to one.
- `usePortfolio()`'s portfolio/allocation total is now explicitly documented as NOT net worth, at
  its point of definition and at every prop boundary it crosses.

### Negative
- The Overview headline (and FIRE's `current_nw`) no longer ticks live with market prices between
  scheduler runs — a real, accepted UX regression in exchange for correctness.
- `Masthead`'s "% of net worth" figure on the Investments tab is now the ONLY headline-adjacent
  surface still using a client-computed total rather than the backend's — a deliberate exception
  (see table above), not an oversight, but worth knowing it exists.

**Update, same session:** the `bucket` overload flagged above as deferred follow-up work was
resolved concurrently by a parallel effort — `docs/adr/2026-07-31-split-position-bucket.md`
splits it into `vesting_state` (entitlement) + `liquidity` (reachability) across the whole stack.
`usePortfolio.ts`/`netWorth.tsx`/`usePortfolio.unvested.test.ts` were updated in this change to
match (`vesting_state === 'unvested'` replaces the old `_unvested` assembly-time tag entirely —
the backend now states entitlement per row, so no frontend-side inference is needed at all); the
`Attention` widget's `contingentPct` risk check (`overviewCashflowCore.tsx`) was similarly
migrated off the removed `bucket` field to `liquidity === 'semi_liquid' || asset_class ===
'receivable'`, preserving its prior meaning under the new field split.

### Follow-ups
- `net_worth()` vs. `net_worth_history()` still don't reconcile exactly (banding differences),
  per the prior ADR's own follow-up — unrelated to this change, not investigated further here.

## Alternatives considered

- **Keep computing net worth client-side, just fix the unvested-exclusion bug** (the stop-gap
  already in place before this change) — rejected: it fixes today's known gap but leaves the
  structural problem (two implementations) in place for the next one; the owner explicitly asked
  for the duplication removed, not patched again.
- **Delete `usePortfolio()`'s `total` entirely** — rejected: the consumer audit found genuine,
  non-net-worth uses (allocation/venue/reach denominators, the Masthead "% of net worth" whose
  numerator must share its live-priced source) that would have needed re-deriving a local sum
  anyway; renaming documents the boundary instead of hiding it.
- **Also swap `Masthead`'s `portfolioTotal` denominator to `useNetWorth()`** — rejected: it would
  pair a live-priced numerator with a ~2min-stale denominator, producing a percentage that visibly
  jitters against its own numerator's ticks — a different, self-inflicted bug.

## Evidence

`frontend/src/components/tabs/overview/widgets/netWorth.headline.test.tsx` — mocks
`GET /api/net-worth` with a distinctive figure and asserts the Overview headline renders it, even
when the portfolio rows/total passed into `OverviewDataProvider` sum to the OTHER real number from
the incident (S$132,000.00 vs. the backend's S$107,000.00); a second case asserts a loading
skeleton, never a stale/zero figure, before the fetch resolves.

Kept green (updated in this change for the concurrent `vesting_state`/`liquidity` split, still
pinning the same invariant): `frontend/src/hooks/usePortfolio.unvested.test.ts` — the entitlement
exclusion in `compute()`, still correct for the allocation denominator.

Final state (`npx tsc -p tsconfig.app.json --noEmit`, `npx vitest run`): 306 test files / 3045
tests passing; the 3 failing files are entirely `frontend/src/components/tabs/commitments/`
(timelineStrip, detailActions, detailFunding) — pre-existing, unrelated to net worth, explicitly
out of scope for this change. Remaining typecheck errors are all in files under concurrent,
unrelated edit at the time of this change (`equity/`, `mobileKeyboard.contract.test.ts`,
`useAccountsLedger.optimisticBulk.test.ts`, `FeedCards.tsx`, `commitments/detail.tsx`,
`commitments/fundingBarParity.test.tsx`) — none touched by this change.

## Agent provenance

- **Decided by:** implementing Claude agent (Sonnet 5), on `fix/approvals-never-expire`, from an
  owner-approved bug report + fix brief (read `/api/net-worth` for net worth, keep the portfolio
  total for allocation math, add the regression test, write the ADR). The per-consumer
  classification (net worth vs. portfolio/allocation total) and the rename-vs-delete call for
  `usePortfolio()`'s `total` were made by tracing every call site, not assumed.
- **Lane / branch:** `fix/approvals-never-expire` (no dedicated worktree for this sub-task).
- **Alternatives weighed:** see Alternatives considered above.
- **Driving artifacts:** owner-reported bug (S$132,000 vs S$107,000 headline gap);
  `docs/adr/2026-07-31-unvested-equity-excluded-from-net-worth.md`'s explicit follow-up flagging
  this exact fix as future, separately-scoped work.
- **Why this way (one line):** one authoritative computation, read everywhere a "net worth" figure
  renders; keep (and clearly label) the separate portfolio/allocation total where the math
  genuinely needs it instead of forcing every consumer onto one field.
