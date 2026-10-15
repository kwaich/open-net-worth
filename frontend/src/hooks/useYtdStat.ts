// useYtdStat — YTD return vs the user's selected benchmark.
//
// Lifted out of investments/Brief.tsx so the phone masthead can carry this stat in the
// hero (owner, 2026-07-30: "on mobile, for YTD vs SPY, let's show in the hero instead")
// while the desktop brief keeps rendering it in its stats band. Both callers hit the same
// useQueryCache key, so mounting it twice costs one request, not two.

import { fetchReturns } from '../api/portfolio'
import { useQueryCache } from '../state/queryCache'
import { usePreferredBenchmark } from './usePreferredBenchmark'

export interface YtdStat {
  portYtd: number // portfolio fraction (e.g. 0.184)
  benchYtd: number | null // benchmark fraction or null
  benchSym: string // e.g. "SPY"
  ahead: number | null // excess in percentage points (portYtd - benchYtd) * 100
}

export function useYtdStat(): { stat: YtdStat | null; loading: boolean } {
  const bench = usePreferredBenchmark()
  const query = useQueryCache<YtdStat | null>(
    `/api/returns?period=YTD&benchmark=${encodeURIComponent(bench)}`,
    async () => {
      const data = await fetchReturns('YTD', bench)
      const portYtd = data.twr?.period_return ?? null
      if (portYtd == null) return null
      const benchYtd = data.comparison?.benchmark_twr ?? null
      const benchSym = data.comparison?.benchmark.symbol ?? bench
      const ahead = benchYtd != null ? (portYtd - benchYtd) * 100 : null
      return { portYtd, benchYtd, benchSym, ahead }
    },
    { staleTime: 0 },
  )
  return {
    stat: query.data ?? null,
    loading: query.initialLoading && !query.error,
  }
}
