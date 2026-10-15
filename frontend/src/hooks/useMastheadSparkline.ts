// Masthead 30D sparkline fetcher (Wave 2, RFC 09-cross-tab).
//
// Fetches GET /api/portfolio/value-series?days=30&grain=daily.
// Returns `values: number[]` (total_value_base per day), EMPTY when:
//   - API responds with has_real_daily_history === false
//   - fewer than 2 data points
//   - any fetch error
//
// Never synthesizes or generates a fake series.

import { useMemo } from 'react'
import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'

interface SeriesPoint {
  date: string
  value_base: number
}

interface ValueSeriesResponse {
  series: SeriesPoint[]
  inception: string | null
  has_real_daily_history?: boolean
}

export interface SparklineResult {
  values: number[]
  loading: boolean
}

// Reads through `state/queryCache`, which mirrors itself to localStorage — so a reopened PWA
// draws the last known sparkline immediately and refreshes it underneath, instead of blanking
// the masthead on every open (2026-07-31).
async function fetchValueSeries(days: number): Promise<ValueSeriesResponse | null> {
  const res = await authFetch(`/api/portfolio/value-series?days=${days}&grain=daily`)
  return res.ok ? ((await res.json()) as ValueSeriesResponse) : null
}

export function useMastheadSparkline(days = 30): SparklineResult {
  const query = useQueryCache<ValueSeriesResponse | null>(
    `/api/portfolio/value-series?days=${days}&grain=daily`,
    () => fetchValueSeries(days),
  )

  const values = useMemo(() => {
    const data = query.data
    if (!data || data.has_real_daily_history === false) return []
    const pts = data.series.map((p) => p.value_base)
    // Require at least 2 points for a meaningful sparkline; never render 0 or 1 point.
    return pts.length >= 2 ? pts : []
  }, [query.data])

  return { values, loading: query.initialLoading }
}
