// After a manual lot acquire / amend / remove / disposal (or any UI write that changes
// holdings), the Investments book and Overview aggregates must refetch. Lucius writes already
// push SSE `data_changed` with mutates=("portfolio",); UI forms have no SSE — they must emit
// the same signal and bust the query-cache entries that `usePortfolio.refresh()` does not
// touch (notably PORTFOLIO_ANALYTICS_KEY → useInvestmentsBook).

import { emitDataChanged } from '../../lucius/bus'
import { invalidateQueryCache } from '../../state/queryCache'
import {
  NET_WORTH_KEY,
  NETWORTH_STATS_KEY,
  PORTFOLIO_ANALYTICS_KEY,
  PORTFOLIO_KEY,
  PORTFOLIO_LAST_KNOWN_KEY,
} from './keys'

const PORTFOLIO_CACHE_PREFIXES = [
  PORTFOLIO_ANALYTICS_KEY,
  PORTFOLIO_KEY,
  PORTFOLIO_LAST_KNOWN_KEY,
  NET_WORTH_KEY,
  NETWORTH_STATS_KEY,
] as const

/** Bust portfolio-derived caches and notify useDataVersion subscribers. */
export function notifyPortfolioChanged(): void {
  for (const key of PORTFOLIO_CACHE_PREFIXES) {
    invalidateQueryCache(key)
  }
  // Match tool mutates=("portfolio",); include ledger — acquire/dispose may book funding legs.
  emitDataChanged(['portfolio', 'ledger'])
}
