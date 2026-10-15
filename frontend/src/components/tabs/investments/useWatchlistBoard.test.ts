import { describe, expect, it } from 'vitest'
import type { WatchQuoteRow } from '../../../types/watchlistQuotes'
import { rowsForList, sortWatchRows } from './useWatchlistBoard'
import { fChange } from './watchColumns'

function row(ticker: string, over: Partial<WatchQuoteRow> = {}): WatchQuoteRow {
  return {
    ticker,
    name: `${ticker} Inc`,
    instrument_id: null,
    asset_class: 'equity',
    watchlist_ids: ['wl_a'],
    exchange: null,
    currency: 'USD',
    market_state: null,
    price: 10,
    prev_close: 10,
    change: 0,
    change_pct: 0,
    open: null,
    day_low: null,
    day_high: null,
    volume: null,
    avg_volume: null,
    volume_ratio: null,
    market_cap: null,
    week52_low: null,
    week52_high: null,
    range_pos: null,
    trailing_pe: null,
    earnings_at: null,
    position: null,
    spark: null,
    ...over,
  }
}

describe('watchlist board helpers', () => {
  const rows = [
    row('AMD', { change_pct: -0.9 }),
    row('AAPL', { change_pct: 2.39, watchlist_ids: ['wl_a', 'wl_b'] }),
    row('ZZZZ', { change_pct: null }),
  ]

  it('keeps the list order until a column is picked', () => {
    expect(sortWatchRows(rows, { key: 'list', dir: 1 }).map((r) => r.ticker)).toEqual([
      'AMD',
      'AAPL',
      'ZZZZ',
    ])
  })

  it('sorts by change % with unknown figures sinking in both directions', () => {
    const desc = sortWatchRows(rows, { key: 'change_pct', dir: -1 }).map((r) => r.ticker)
    const asc = sortWatchRows(rows, { key: 'change_pct', dir: 1 }).map((r) => r.ticker)
    expect(desc).toEqual(['AAPL', 'AMD', 'ZZZZ'])
    expect(asc).toEqual(['AMD', 'AAPL', 'ZZZZ'])
  })

  it('narrows to one list and a symbol/name search', () => {
    expect(rowsForList(rows, 'wl_b', '').map((r) => r.ticker)).toEqual(['AAPL'])
    expect(rowsForList(rows, 'wl_a', 'amd inc').map((r) => r.ticker)).toEqual(['AMD'])
  })

  it('formats a signed change like a broker', () => {
    expect(fChange(3.81)).toBe('+3.81')
    expect(fChange(-0.24)).toBe('−0.24')
    expect(fChange(0)).toBe('0.00')
    expect(fChange(null)).toBe('—')
  })
})
