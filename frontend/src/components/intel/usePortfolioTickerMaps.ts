import { useMemo } from 'react'
import { usePortfolioContext } from '../../state/portfolio'
import { CLASS_LABELS, DEFAULT_TARGETS, type LuciusContext } from '../../utils/luciusEngine'

/** What the Intel tab needs from your positions: each ticker's quote currency, the tickers you
 *  own (as written, and upper-cased for matching), and the Lucius engine's holdings context
 *  (the price-alert suggestions). */
export function usePortfolioTickerMaps() {
  const { positionRows } = usePortfolioContext()
  return useMemo(() => {
    const tickerCcy: Record<string, string> = {}
    const owned = new Set<string>()
    const holdingsBySymbol: LuciusContext['holdingsBySymbol'] = {}
    for (const r of positionRows) {
      if (!r.ticker) continue
      tickerCcy[r.ticker] = r.quote_currency || r.currency
      owned.add(r.ticker)
      holdingsBySymbol[r.ticker.toUpperCase()] = {
        symbol: r.ticker,
        cls: r.asset_class,
        qty: parseFloat(r.quantity) || 0,
        weight: r._pct,
        plPct: r._plpct,
      }
    }
    const luciusCtx: LuciusContext = {
      holdingsBySymbol,
      targets: DEFAULT_TARGETS,
      classLabels: CLASS_LABELS,
    }
    return {
      tickerCcy,
      ownedTickers: owned,
      heldSet: new Set([...owned].map((t) => t.toUpperCase())),
      luciusCtx,
    }
  }, [positionRows])
}
