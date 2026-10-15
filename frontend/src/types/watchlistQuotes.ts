// GET /api/watchlist/quotes — the Investments › Watchlist quote board.
// Mirrors backend/services/watchlist_quotes.build_watchlist_quotes.

import type { Watchlist } from '../components/intel/WatchlistsSection'

/** Your position in a watched name you also hold — base currency, legs across accounts summed. */
export interface WatchPosition {
  quantity: number
  value: number | null
  day_pl: number | null
  // Null when any leg has no cost basis recorded.
  unrealized: number | null
}

export interface WatchQuoteRow {
  ticker: string
  name: string
  instrument_id: string | null
  asset_class: string
  watchlist_ids: string[]
  exchange: string | null
  currency: string | null
  market_state: string | null
  // Every market field is null when no quote resolved for the ticker.
  price: number | null
  prev_close: number | null
  change: number | null
  change_pct: number | null
  open: number | null
  day_low: number | null
  day_high: number | null
  volume: number | null
  avg_volume: number | null
  volume_ratio: number | null
  market_cap: number | null
  week52_low: number | null
  week52_high: number | null
  // 0 = at the 52-week low, 1 = at the 52-week high.
  range_pos: number | null
  trailing_pe: number | null
  earnings_at: string | null
  position: WatchPosition | null
  // Last ~30 daily closes ending on today's price; null until history is stored.
  spark: number[] | null
}

export interface WatchQuoteBoard {
  generated_at: string
  watchlists: Watchlist[]
  rows: WatchQuoteRow[]
}
