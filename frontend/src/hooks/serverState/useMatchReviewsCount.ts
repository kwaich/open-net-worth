import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import { MATCH_REVIEWS_KEY } from './keys'

export interface MatchReviewsResponse {
  count?: number
  reviews?: unknown[]
  groups?: Record<string, unknown[]>
  superseded_count?: number
}

const fetchMatchReviews = () => fetchJson<MatchReviewsResponse>(MATCH_REVIEWS_KEY)

/**
 * The dedup review queue. One shared entry backs BOTH the sidebar badge count (RouterShell)
 * and the full review list (`hooks/useMatchReviews.ts`) — before consolidation these were two
 * independent readers of the same endpoint, visible in the logs as back-to-back duplicate
 * `/api/matches/reviews` requests.
 *
 * No polling: a review is only ever enqueued by a ledger write, and every ledger write bumps
 * the `ledger` data version, which revalidates this entry.
 */
export function useMatchReviewsCount(): QueryCacheResult<MatchReviewsResponse> {
  const version = useDataVersion('ledger')
  return useQueryCache<MatchReviewsResponse>(MATCH_REVIEWS_KEY, fetchMatchReviews, {
    staleTime: 30_000,
    version,
  })
}
