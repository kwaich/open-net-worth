import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import { INTEL_SIGNALS_KEY } from './keys'

export interface IntelSignal {
  status: string
  [key: string]: unknown
}

const fetchIntelSignals = () => fetchJson<IntelSignal[]>(INTEL_SIGNALS_KEY)

/**
 * The Intel feed. Polls on a 30s interval (preserving the pre-consolidation
 * `setInterval` cadence in RouterShell) — the poll lives in the cache layer so concurrent
 * subscribers share one request instead of each running their own timer.
 */
export function useIntelSignals(): QueryCacheResult<IntelSignal[]> {
  const version = useDataVersion(['signals', 'intel'])
  return useQueryCache<IntelSignal[]>(INTEL_SIGNALS_KEY, fetchIntelSignals, {
    staleTime: 30_000,
    refetchInterval: 30_000,
    version,
  })
}
