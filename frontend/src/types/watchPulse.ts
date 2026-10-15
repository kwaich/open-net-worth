// GET /api/intel/watch-pulse?population= — the pulse rows on Intel's My holdings and watchlist tabs.
// Mirrors backend/services/watch_pulse.build_pulse.

export type PulsePopulation = 'watched' | 'held'

export type FindingTone = 'good' | 'bad' | 'warn' | 'info'

export interface WatchFinding {
  kind: string
  tone: FindingTone
  weight: number
  severity: 'high' | 'medium' | 'low'
  text: string
}

export interface WatchNearestAlert {
  level: number
  op: string | null
  // Signed: positive = the alert level sits above the current price.
  distance_pct: number
}

export interface WatchFit {
  sector: string
  held_pct: number
  rank: number | null
  note: string
}

export type TrendPeriod = '1M' | '3M' | '1Y'

// One period of the row's trend line: closes ending on today's price (1Y thinned to weekly), the
// benchmark on the same dates (null without a benchmark move), and the period's % moves.
// `bench` / `vs_bench_pct` are the DASHED line — whatever `row.compare` names (the sector fund on
// a watchlist tab, else the benchmark). `vs_market_pct` is the benchmark gap when the line is a
// sector.
export interface TrendSeries {
  closes: number[]
  pct: number
  bench: number[] | null
  vs_bench_pct: number | null
  vs_market_pct?: number | null
}

export interface TrendCompare {
  symbol: string
  kind: 'sector' | 'benchmark'
}

export interface NextEvent {
  kind: 'earnings' | 'ex_dividend'
  at: string
  in_days: number
}

export interface WatchPulseRow {
  ticker: string
  name: string
  instrument_id: string | null
  watchlist_ids: string[]
  price: number
  currency: string | null
  day_pct: number | null
  rel_day_pct: number | null
  // 0 = at the 52-week low, 1 = at the 52-week high.
  range_pos: number | null
  from_high_pct: number | null
  vs_200d_pct: number | null
  volume_ratio: number | null
  earnings_at: string | null
  earnings_in_days: number | null
  market_state: string | null
  nearest_alert: WatchNearestAlert | null
  findings: WatchFinding[]
  score: number
  fit: WatchFit | null
  story_count: number
  story_id: string | null
  week52_low: number | null
  week52_high: number | null
  // Held population only — the name's weight in the invested book and today's base effect.
  position: { value: number; weight: number; day_pl: number } | null
  // Today's move against the stock's own history (null without enough history):
  // z = how many "usual daily moves"; kind = what drove it.
  move: {
    z: number | null
    usual_pct: number | null
    beta: number | null
    kind: 'company' | 'market' | 'sector' | null
    residual_pct: number | null
  } | null
  forward_pe: number | null
  trailing_pe: number | null
  intent: { thesis?: string; buy_below?: number; buy_above?: number; fair_value?: number } | null
  thesis: string | null
  in_buy_zone: boolean | null
  // Findings material enough for an explained alert (company_move, buy_zone, fair_value, …).
  material: string[]
  // Null when there is too little price history; a single period is null when history is thin.
  trend: Record<TrendPeriod, TrendSeries | null> | null
  // What the trend's dashed line is (null when there's no line).
  compare?: TrendCompare | null
  next_event: NextEvent | null
}

export interface WatchPulse {
  generated_at: string
  population: PulsePopulation
  benchmark: { symbol: string; day_pct: number | null } | null
  // empty = nothing watched · unavailable = watched but no quote resolved.
  status: 'empty' | 'unavailable' | 'ok'
  summary: { tracked: number; priced: number; up: number; down: number; flagged: number }
  rows: WatchPulseRow[]
}
