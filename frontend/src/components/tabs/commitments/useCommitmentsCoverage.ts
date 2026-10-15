// Fetches the portfolio coverage verdict (verdict + stats + monthly cushion `timeline`) that
// drives the masthead chart/ribbon and the list's risk chips. Separate from `useCommitments`
// (the commitment rows themselves) so the masthead's horizon/floor controls can re-fetch this
// alone without reloading every commitment.
import { useCallback, useEffect, useState } from 'react'
import type { PortfolioCoverage, UnmarkedPaymentSuggestion } from '../../../types/commitments'
import { authFetch } from '../../../api/client'
import { useCommitmentsCoverageRead } from '../../../hooks/serverState'
import { useQueryCache } from '../../../state/queryCache'

const UNMARKED_KEY = '/api/commitments/unmarked-payments'

export interface CommitmentsCoverageState {
  coverage: PortfolioCoverage | null
  unmarked: UnmarkedPaymentSuggestion[]
  loading: boolean
  refresh: () => void
}

export function useCommitmentsCoverage(months: number, floor?: number): CommitmentsCoverageState {
  // The verdict is read through the SAME shared hook (and so, for the default window, the SAME
  // cache entry) the Dashboard's "Funding shortfall" flag reads: two fetches of it, minutes or
  // an FX refresh apart, once quoted S$13,792.08 on one screen and S$13,792.95 on the other.
  const coverageQuery = useCommitmentsCoverageRead(months, floor)
  const unmarkedQuery = useQueryCache<UnmarkedPaymentSuggestion[]>(
    UNMARKED_KEY,
    async () => {
      const response = await authFetch(UNMARKED_KEY)
      if (!response.ok) throw new Error('Failed to load unmarked payments')
      return response.json()
    },
    { staleTime: 0 },
  )
  const refresh = useCallback(() => {
    void coverageQuery.refresh()
    void unmarkedQuery.refresh()
  }, [coverageQuery, unmarkedQuery])

  // The cache key embeds `months`, so switching the horizon toggle (6m/12m/24m/All) always
  // looks like a brand-new, never-fetched key — `data` goes back to `undefined` for a moment
  // even though we just had a perfectly good chart on screen. Without this, the whole page's
  // `loading` gate (Commitments.tsx) treated that as a first load and blanked the entire page
  // behind a spinner just for tapping a filter. Keep the last resolved value around across
  // horizon switches and only report `loading` when we've truly never loaded anything yet —
  // the new horizon's data swaps in in place once it resolves.
  const [last, setLast] = useState<PortfolioCoverage | null>(null)
  useEffect(() => {
    if (coverageQuery.data) setLast(coverageQuery.data)
  }, [coverageQuery.data])

  const coverage = coverageQuery.data ?? last

  return {
    coverage,
    unmarked: unmarkedQuery.data ?? [],
    loading: coverage == null,
    refresh,
  }
}
