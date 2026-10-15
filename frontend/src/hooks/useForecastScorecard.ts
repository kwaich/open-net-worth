import { useCallback, useEffect, useRef, useState } from 'react'
import { useDataVersion } from './useDataVersion'
import { fetchForecastScorecard } from '../api/insights'
import type { ForecastScorecard, ScorecardWindow } from '../api/insights'

interface UseForecastScorecardResult {
  data: ForecastScorecard | null
  loading: boolean
  error: boolean
  retry: () => void
}

export function useForecastScorecard(window: ScorecardWindow): UseForecastScorecardResult {
  const [data, setData] = useState<ForecastScorecard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [tick, setTick] = useState(0)

  const retry = useCallback(() => setTick((t) => t + 1), [])

  const anyWriteV = useDataVersion('*')
  const seenWriteRef = useRef(anyWriteV)
  useEffect(() => {
    let alive = true
    const writeDriven = seenWriteRef.current !== anyWriteV
    seenWriteRef.current = anyWriteV
    if (!writeDriven) setLoading(true)
    setError(false)
    fetchForecastScorecard(window)
      .then((d) => {
        if (alive) {
          setData(d)
          setLoading(false)
        }
      })
      .catch(() => {
        if (alive) {
          setError(true)
          setLoading(false)
        }
      })
    return () => {
      alive = false
    }
  }, [window, tick, anyWriteV])

  return { data, loading, error, retry }
}
