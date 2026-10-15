import { useEffect, useState } from 'react'
import type { FixedIncomeSummary } from '../types/portfolio'
import { fetchFixedIncome } from '../api/portfolio'

interface UseFixedIncomeResult {
  data: FixedIncomeSummary | null
  loading: boolean
  error: boolean
}

/** Fetches GET /api/fixed-income — the bond maturity ladder + coupon schedule summary. */
export function useFixedIncome(version?: number): UseFixedIncomeResult {
  const [data, setData] = useState<FixedIncomeSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(false)
    fetchFixedIncome()
      .then((d) => {
        if (!alive) return
        setData(d)
        setLoading(false)
      })
      .catch(() => {
        if (!alive) return
        setError(true)
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [version])

  return { data, loading, error }
}
