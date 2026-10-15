// Intel digest API contract — mirrors GET /api/intel/digest?period=1M&narrate=false

export interface BenchmarkTwr {
  period_return: number
  annualized: number
}

export interface BenchmarkDataQuality {
  market_since: string | null
  value_points: number
  sufficient: boolean
}

export interface BenchmarkPayload {
  symbol: string
  instrument_id: string
  // null when price history is too short to compute a return — the payload object
  // itself can be non-null while twr is null (see services/intel_digest.benchmark_return).
  twr: BenchmarkTwr | null
  data_quality: BenchmarkDataQuality
}

export interface MarketBackdrop {
  benchmark: BenchmarkPayload | null
  crypto_benchmark: BenchmarkPayload | null
}

export interface ReturnDriver {
  instrument_id: string
  label: string
  ticker: string | null
  contribution_base: number | null
  contribution_pct: number
  direction: 'up' | 'down' | 'flat'
}

export interface TopMover {
  instrument_id: string
  label: string
  ticker: string | null
  day_change_base: number | null
  day_change_pct: number
}

export interface DigestDataQuality {
  has_price_history: boolean
  rows_count: number
  rows_with_day_change: number
  benchmark_sufficient: boolean
  period: string
}

// Today's book P&L — sum of signed per-row day change in base currency.
export interface DigestToday {
  pl_base: number
  pl_pct: number
  has_data: boolean
}

// Portfolio TWR over the digest period (null when price history is insufficient).
export interface PortfolioReturn {
  period_return: number
}

/** How far the AI's opinion went on a model-written brief or read (ai_opinion_level). */
export interface BriefOpinion {
  lean?: 'bullish' | 'bearish' | 'neutral'
  lean_reason?: string
  suggestion?: string
}

export interface IntelDigest {
  generated_at: string
  base_currency: string
  period: string
  market_backdrop: MarketBackdrop
  return_drivers: ReturnDriver[]
  top_movers: TopMover[]
  narration: string | null
  /** Lucius's lean / suggestion beside a level-5 narration (null at the information level). */
  narration_opinion?: BriefOpinion | null
  data_quality: DigestDataQuality
  today: DigestToday
  portfolio_return: PortfolioReturn | null
}
