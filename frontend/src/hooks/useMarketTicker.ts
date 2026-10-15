// Commodity / coin / FX rows for the topbar ticker. The HOLDINGS half of the strip comes from
// usePortfolio() — already in the shell — so this hook only fetches what the owner may not own.
//
// Polls on the same 60s cadence as the portfolio refresh and the server's own cache TTL, so a
// poll that lands mid-TTL is served from memory rather than hitting the provider again.

import { useEffect, useState } from 'react'
import { fetchJson } from './serverState'

export interface MarketTickerRow {
  symbol: string
  label: string
  kind: 'commodity' | 'coin' | 'fx' | 'equity'
  unit: string
  price: number
  change_pct: number | null
  /** Present only while a pre/post-market session is active — price/change_pct above are
   *  already the extended-hours figures in that case, not the frozen regular-session ones. */
  session?: 'pre' | 'post'
}

const POLL_MS = 60_000

export function useMarketTicker(enabled: boolean): MarketTickerRow[] {
  const [rows, setRows] = useState<MarketTickerRow[]>([])

  useEffect(() => {
    // Disabled: don't poll at all. The ticker being off should cost nothing, not just render
    // nothing.
    if (!enabled) {
      setRows([])
      return
    }
    let alive = true

    async function load() {
      try {
        const data = await fetchJson<{ rows?: MarketTickerRow[] }>('/api/market-ticker')
        if (alive && data?.rows) setRows(data.rows)
      } catch {
        // Keep the last good rows. A status bar must never surface a fetch error — the strip
        // simply stops updating, and the holdings half keeps rendering regardless.
      }
    }

    load()
    const id = setInterval(load, POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [enabled])

  return rows
}
