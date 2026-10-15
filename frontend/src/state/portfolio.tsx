// Portfolio context — ONE shared usePortfolio() instance for the whole app.
//
// usePortfolio() owns a live-price fetch (a real Yahoo/CoinGecko network round-trip) plus a
// 60s refresh interval. Before this provider, RouterShell, Intel, and useLuciusContext each
// called usePortfolio() independently — three uncoordinated fetches + three uncoordinated
// timers for the same data, tripling live-price load and contending with unrelated backend
// requests for the same synchronous request thread pool. Mount this once above the app shell;
// every consumer reads the same in-flight state via usePortfolioContext().

import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import { usePortfolio } from '../hooks/usePortfolio'
import type { PortfolioState } from '../hooks/usePortfolio'

const PortfolioContext = createContext<PortfolioState | null>(null)

interface ProviderProps {
  children: ReactNode
}

export function PortfolioProvider({ children }: ProviderProps) {
  const portfolio = usePortfolio()
  return <PortfolioContext.Provider value={portfolio}>{children}</PortfolioContext.Provider>
}

export function usePortfolioContext(): PortfolioState {
  const ctx = useContext(PortfolioContext)
  if (!ctx) throw new Error('usePortfolioContext must be used inside <PortfolioProvider>')
  return ctx
}
