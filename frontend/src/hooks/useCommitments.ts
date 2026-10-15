import { useCallback } from 'react'
import type { Commitment } from '../types/commitments'
import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'

export interface CommitmentsState {
  commitments: Commitment[]
  loading: boolean
  refresh: () => void
}

/** Fetches /api/commitments. The endpoint returns each commitment with its full
 *  installments + funding plan (and, from Phase 2, a live coverage block). */
export function useCommitments(): CommitmentsState {
  const query = useQueryCache<Commitment[]>(
    '/api/commitments',
    async () => {
      const response = await authFetch('/api/commitments')
      if (!response.ok) throw new Error('Failed to load commitments')
      return response.json()
    },
    { staleTime: 0 },
  )
  const refresh = useCallback(() => {
    void query.refresh()
  }, [query])
  return { commitments: query.data ?? [], loading: query.initialLoading, refresh }
}
