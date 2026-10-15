// budgetLens/useBudgetEngines.ts — the lens switcher's one data source. Always enabled (the
// switcher itself needs it to render at all, independent of which lens is active) — this is
// distinct from a non-home lens's ViewModel, which stays unfetched until the user picks it
// (acceptance B5, see ProportionalPane).
import { useQueryCache } from '../../../../state/queryCache'
import type { QueryCacheResult } from '../../../../state/queryCache'
import { ENGINES_KEY, fetchBudgetEngines } from './api'
import type { BudgetEnginesResponse } from './types'

export function useBudgetEngines(): QueryCacheResult<BudgetEnginesResponse> {
  return useQueryCache(ENGINES_KEY, fetchBudgetEngines, { staleTime: 30_000 })
}
