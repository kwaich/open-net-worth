import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'
import type { BriefOpinion } from '../types/intelDigest'
import type { TrendPeriod } from '../types/watchPulse'

// A watchlist tab's brief at verbosity 5: Lucius's short narrative of the list's rows, written
// and fact-checked server-side (GET /api/intel/watch-brief). The server caches it for 30 minutes
// per list and period; `narration` is null when the model is unavailable or its write-up failed
// the checks, and the tab then keeps its level-4 template.
export interface WatchBriefNarration extends BriefOpinion {
  narration: string | null
}

const STALE_MS = 15 * 60_000

export function useWatchBrief(
  listId: string | null | undefined,
  period: TrendPeriod,
  enabled: boolean,
): { data: WatchBriefNarration | null; loading: boolean } {
  const on = enabled && !!listId
  const key = `/api/intel/watch-brief?list_id=${encodeURIComponent(listId ?? '')}&period=${period}`
  const query = useQueryCache<WatchBriefNarration>(
    key,
    async () => {
      const res = await authFetch(key)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()) as WatchBriefNarration
    },
    { enabled: on, staleTime: STALE_MS },
  )
  return { data: on ? (query.data ?? null) : null, loading: on && query.initialLoading }
}
