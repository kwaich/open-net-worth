import type { WatchPulse, WatchPulseRow } from '../../types/watchPulse'

// Synthetic pulse payloads for the pulse card and row tests.

export function pulseRow(over: Partial<WatchPulseRow> = {}): WatchPulseRow {
  return {
    ticker: 'NVDA',
    name: 'NVIDIA Corp',
    instrument_id: null,
    watchlist_ids: ['wl_1'],
    price: 108.2,
    currency: 'USD',
    day_pct: 8,
    rel_day_pct: 7.5,
    range_pos: 0.6,
    from_high_pct: -4.2,
    vs_200d_pct: 6,
    volume_ratio: null,
    earnings_at: null,
    earnings_in_days: null,
    market_state: 'REGULAR',
    nearest_alert: null,
    findings: [
      { kind: 'big_move', tone: 'good', weight: 38, severity: 'medium', text: 'Up 8.0% today' },
    ],
    score: 38,
    fit: null,
    story_count: 0,
    story_id: null,
    week52_low: 60,
    week52_high: 113,
    position: null,
    move: null,
    forward_pe: null,
    trailing_pe: null,
    intent: null,
    thesis: null,
    in_buy_zone: null,
    material: [],
    trend: null,
    next_event: null,
    ...over,
  }
}

export function pulse(over: Partial<WatchPulse> = {}): WatchPulse {
  const rows = over.rows ?? [pulseRow()]
  return {
    generated_at: new Date().toISOString(),
    population: 'watched',
    benchmark: { symbol: 'SPY', day_pct: 0.5 },
    status: 'ok',
    summary: { tracked: rows.length, priced: rows.length, up: 1, down: 0, flagged: 1 },
    rows,
    ...over,
  }
}
