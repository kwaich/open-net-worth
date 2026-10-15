import { authFetch } from '../api/client'
import type { PortfolioDrivers } from '../types/portfolioDrivers'
import { useQueryCache } from '../state/queryCache'

export function usePortfolioDrivers(): {
  data: PortfolioDrivers | null
  loading: boolean
  refetch: () => void
} {
  const query = useQueryCache<PortfolioDrivers>(
    '/api/portfolio/drivers',
    async () => {
      const response = await authFetch('/api/portfolio/drivers')
      if (!response.ok) throw new Error('Failed to load portfolio drivers')
      return response.json()
    },
    { staleTime: 0 },
  )
  return {
    data: query.data ?? null,
    loading: query.initialLoading && !query.error,
    refetch: () => {
      void query.refresh()
    },
  }
}
