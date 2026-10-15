// AssetNav context — provides `openAsset(params)` to any Intel subtree component
// without prop-drilling. Navigates IMMEDIATELY — no network call first.
//
// A ticker goes straight to /quote/$symbol, which resolves itself once mounted
// (GET /api/intel/assets/by-ticker/{ticker}, find-or-create). Only a tickerless
// instrument (manual/private holdings) falls back to /asset/$instrumentId.

import { createContext, useCallback, useContext, useMemo } from 'react'
import type { ReactNode } from 'react'
import { useNavigate, useRouterState } from '@tanstack/react-router'

export interface AssetNavParams {
  instrumentId?: string
  ticker?: string
  label?: string
}

interface AssetNavValue {
  openAsset: (params: AssetNavParams) => void
}

const AssetNavContext = createContext<AssetNavValue | null>(null)

interface ProviderProps {
  children: ReactNode
}

export function AssetNavProvider({ children }: ProviderProps) {
  const navigate = useNavigate()
  const routerState = useRouterState()

  // Read the current single-asset route's ?from= / ?sub= so peer drills inherit the
  // original source-tab (sidebar highlight) and the Overview|Analysis|Position sub-tab.
  const { currentFrom, currentSub } = (() => {
    const match = routerState.matches.find(
      (m) => m.routeId === '/asset/$instrumentId' || m.routeId === '/quote/$symbol',
    )
    const search = (match?.search ?? {}) as { from?: string; sub?: string }
    return { currentFrom: search.from, currentSub: search.sub }
  })()

  const openAsset = useCallback(
    (params: AssetNavParams) => {
      const { instrumentId, ticker, label } = params

      if (ticker) {
        navigate({
          to: '/quote/$symbol',
          params: { symbol: ticker },
          search: { from: currentFrom, label, sub: currentSub },
        })
        return
      }

      if (instrumentId) {
        navigate({
          to: '/asset/$instrumentId',
          params: { instrumentId },
          search: { from: currentFrom, label, ticker, sub: currentSub },
        })
      }
    },
    [navigate, currentFrom, currentSub],
  )

  const value = useMemo(() => ({ openAsset }), [openAsset])

  return <AssetNavContext.Provider value={value}>{children}</AssetNavContext.Provider>
}

export function useAssetNav(): AssetNavValue {
  const ctx = useContext(AssetNavContext)
  if (!ctx) throw new Error('useAssetNav must be used inside <AssetNavProvider>')
  return ctx
}
