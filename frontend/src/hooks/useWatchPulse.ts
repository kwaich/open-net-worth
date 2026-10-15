import { useCallback } from 'react'
import { authFetch } from '../api/client'
import { invalidateQueryCache, useQueryCache } from '../state/queryCache'
import type { PulsePopulation, WatchPulse } from '../types/watchPulse'

const PULSE_BASE = '/api/intel/watch-pulse'
const pulseKey = (population: PulsePopulation) => `${PULSE_BASE}?population=${population}`
// The server caches quotes for 3 min and the watch engine polls every 10; a 90s client cadence
// keeps the strip fresh without hammering the quote feed.
const REFRESH_MS = 90_000

/** Test-only: drop the cached pulse between cases. */
export function _clearWatchPulseCache(): void {
  invalidateQueryCache((key) => key.startsWith(PULSE_BASE))
}

export interface UseWatchPulseResult {
  data: WatchPulse | null
  loading: boolean
  error: boolean
  refetch: () => void
}

export function useWatchPulse(population: PulsePopulation = 'watched'): UseWatchPulseResult {
  const key = pulseKey(population)
  const query = useQueryCache<WatchPulse>(
    key,
    async () => {
      const res = await authFetch(key)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()) as WatchPulse
    },
    { refetchInterval: REFRESH_MS },
  )
  return {
    data: query.data ?? null,
    loading: query.initialLoading,
    error: !!query.error && query.data === undefined,
    refetch: useCallback(() => {
      void query.refresh()
    }, [query]),
  }
}
