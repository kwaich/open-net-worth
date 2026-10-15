// Cache keys for shared server reads.
//
// Keys are literal URL paths — the convention `state/remountCacheInventory.test.ts` already
// asserts on ('/api/connectors', '/api/model-config'). Greppable, debuggable, no key-factory
// indirection.
//
// SHARED_READ_ENDPOINTS is the enforced list: `sharedReadsInventory.test.ts` fails if any file
// outside `hooks/serverState/` calls `authFetch` on one of these directly, which is what keeps
// a ninth independent `/api/accounts` reader from reappearing.

export const PROFILE_KEY = '/api/profile'
export const ACCOUNTS_KEY = '/api/accounts'
export const CATEGORIES_KEY = '/api/categories'
export const PORTFOLIO_KEY = '/api/portfolio'
// THE portfolio: every holding with its money already computed server-side — the read that
// replaced the browser's own valuation fold. `/analytics` is the views DERIVED from it
// (allocation, KPIs, treemap, drift, the invested book) and deliberately omits the rows, so a
// caller needing both never pulls the row set twice.
// The SAME resource valued at last-known prices — `live=0` skips the Yahoo/CoinGecko round
// trip (10-15s provider timeouts) that `PORTFOLIO_KEY` pays. `usePortfolio` reads this first on
// a cold start so the shell can paint before the live prices land, then upgrades in place. It
// is deliberately NOT in SHARED_READ_ENDPOINTS: it is one endpoint with a query flag, already
// listed above, and it has exactly one caller by design.
export const PORTFOLIO_LAST_KNOWN_KEY = '/api/portfolio?live=0'
export const PORTFOLIO_ANALYTICS_KEY = '/api/portfolio/analytics'
export const INTEL_SIGNALS_KEY = '/api/intel/signals'
export const INBOX_COUNT_KEY = '/api/inbox/count'
export const SETUP_STATUS_KEY = '/api/setup-status'
export const MATCH_REVIEWS_KEY = '/api/matches/reviews'
export const NET_WORTH_KEY = '/api/net-worth'

// Server-computed aggregate reads, named for the DATA they return — never for a screen. A
// widget declares the resource it renders and the cache dedups, so N standalone widgets sharing
// a resource cost ONE request (ADR 2026-07-31-overview-aggregates-are-server-computed).
export const NETWORTH_STATS_KEY = '/api/net-worth/stats'
export const COMMITMENTS_SUMMARY_KEY = '/api/commitments/summary'
/** The portfolio funding verdict. Its key always carries the horizon (`?months=12` by default),
 *  so the Dashboard's flag and the Commitments masthead resolve to ONE cache entry: one fetched
 *  object, one shortfall figure. Mirrors backend `COVERAGE_HORIZON_MONTHS`. */
export const COMMITMENTS_COVERAGE_HORIZON_MONTHS = 12
export function commitmentsCoverageKey(
  months: number = COMMITMENTS_COVERAGE_HORIZON_MONTHS,
  floor?: number,
): string {
  const qs = new URLSearchParams({ months: String(months) })
  if (floor != null) qs.set('floor', String(floor))
  return `/api/commitments/coverage?${qs.toString()}`
}
export const DIVIDEND_SUMMARY_KEY = '/api/income/dividends'
export const CASHFLOW_PROJECTIONS_KEY = '/api/cashflow/projections'

export const SHARED_READ_ENDPOINTS = [
  PROFILE_KEY,
  ACCOUNTS_KEY,
  CATEGORIES_KEY,
  PORTFOLIO_KEY,
  PORTFOLIO_ANALYTICS_KEY,
  INTEL_SIGNALS_KEY,
  INBOX_COUNT_KEY,
  SETUP_STATUS_KEY,
  MATCH_REVIEWS_KEY,
  NET_WORTH_KEY,
  NETWORTH_STATS_KEY,
  COMMITMENTS_SUMMARY_KEY,
  DIVIDEND_SUMMARY_KEY,
  CASHFLOW_PROJECTIONS_KEY,
] as const
