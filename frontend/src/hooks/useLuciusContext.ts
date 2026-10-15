import { useMemo } from 'react'
import { usePortfolioContext } from '../state/portfolio'
import { CLASS_LABELS, DEFAULT_TARGETS, type LuciusContext } from '../utils/luciusEngine'

// Builds the portfolio-aware context the Lucius alert engine reasons over: owned holdings keyed by
// uppercased symbol (weight, P/L, qty) plus the default allocation targets/labels. Reads the one
// shared PortfolioProvider instance (mounted once above the app shell) — never mounts its own
// usePortfolio() fetch/poll, so opening this from any alert surface costs no extra network call.
export function useLuciusContext(): LuciusContext {
  const { positionRows } = usePortfolioContext()
  return useMemo<LuciusContext>(() => {
    const holdingsBySymbol: LuciusContext['holdingsBySymbol'] = {}
    for (const r of positionRows) {
      if (!r.ticker) continue
      holdingsBySymbol[r.ticker.toUpperCase()] = {
        symbol: r.ticker,
        cls: r.asset_class,
        qty: parseFloat(r.quantity) || 0,
        weight: r._pct,
        plPct: r._plpct,
      }
    }
    return { holdingsBySymbol, targets: DEFAULT_TARGETS, classLabels: CLASS_LABELS }
  }, [positionRows])
}
