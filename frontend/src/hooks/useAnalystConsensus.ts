import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'

export interface AnalystDistribution {
  strong_buy: number
  buy: number
  hold: number
  sell: number
  strong_sell: number
}

export interface Analyst {
  rating_key?: string
  rating_label?: string
  rating_mean?: number
  analyst_count?: number
  target_low?: number
  target_mean?: number
  target_high?: number
  current?: number
  upside_pct?: number
  distribution?: AnalystDistribution
}

export interface AnalystRating {
  date: string
  firm: string
  rating?: string
  action?: string
  target?: number
  prior_target?: number
}

export interface AnalystResponse {
  currency: string
  analyst: Analyst
  ratings?: AnalystRating[]
}

// Session-scoped cache keyed by instrumentId. The analyst endpoint (Yahoo) is
// fetched once per asset per session — a cache hit returns instantly with no
// skeleton, so switching to the Analysis tab or re-opening the asset never
// re-fetches or re-jumps. `null` is a valid cached value (no coverage).
const cache = new Map<string, AnalystResponse | null>()

/** Test-only: reset the module cache between cases. */
export function _clearAnalystCache(): void {
  cache.clear()
}

export interface UseAnalystConsensusResult {
  data: AnalystResponse | null
  /** True only while an uncached fetch is in flight (drives the skeleton). */
  loading: boolean
}

/**
 * Fetches analyst consensus for an instrument, eagerly (call from the detail
 * view's top level so it prefetches before the Analysis tab is ever opened) and
 * with a session cache so repeat opens are instant. Returns `{ data, loading }`.
 */
export function useAnalystConsensus(instrumentId: string): UseAnalystConsensusResult {
  const cached = cache.has(instrumentId)
  const [data, setData] = useState<AnalystResponse | null>(() => cache.get(instrumentId) ?? null)
  const [loading, setLoading] = useState(!cached)

  useEffect(() => {
    // Cache hit: surface it synchronously, skip the fetch (and the skeleton).
    if (cache.has(instrumentId)) {
      setData(cache.get(instrumentId) ?? null)
      setLoading(false)
      return
    }

    let alive = true
    setData(null)
    setLoading(true)
    authFetch(`/api/intel/assets/${instrumentId}/analyst`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: AnalystResponse | null) => {
        cache.set(instrumentId, d)
        if (alive) {
          setData(d)
          setLoading(false)
        }
      })
      .catch(() => {
        // Network failure is not cached — a later retry can succeed.
        if (alive) setLoading(false)
      })

    return () => {
      alive = false
    }
  }, [instrumentId])

  return { data, loading }
}
