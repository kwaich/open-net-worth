import { describe, expect, it } from 'vitest'
import { holdingsBrief } from './holdingsBrief'
import type { IntelDigest } from '../../types/intelDigest'

const DIGEST = {
  generated_at: '2026-09-27T00:00:00Z',
  base_currency: 'SGD',
  period: '1M',
  market_backdrop: {
    benchmark: {
      symbol: 'SPY',
      instrument_id: 'i',
      twr: { period_return: 0.02, annualized: 0 },
      data_quality: { market_since: null, value_points: 30, sufficient: true },
    },
    crypto_benchmark: null,
  },
  return_drivers: [
    {
      instrument_id: 'n',
      label: 'Nvidia',
      ticker: 'NVDA',
      contribution_base: 410,
      contribution_pct: 2,
      direction: 'up',
    },
  ],
  top_movers: [
    {
      instrument_id: 'n',
      label: 'Nvidia',
      ticker: 'NVDA',
      day_change_base: 95,
      day_change_pct: 8.2,
    },
    {
      instrument_id: 'a',
      label: 'Apple',
      ticker: 'AAPL',
      day_change_base: -30,
      day_change_pct: -2.1,
    },
  ],
  narration: null,
  data_quality: {
    has_price_history: true,
    rows_count: 2,
    rows_with_day_change: 2,
    benchmark_sufficient: true,
    period: '1M',
  },
  today: { pl_base: 65, pl_pct: 0.004, has_data: true },
  portfolio_return: { period_return: 0.039 },
} as IntelDigest

describe('holdingsBrief — verbosity levels', () => {
  it('level 1 is the book’s move only', () => {
    const text = holdingsBrief(DIGEST, 1)
    expect(text).toMatch(/^Your book is .* today\.$/)
    expect(text).not.toContain('NVDA')
  })

  it('level 2 adds the best and worst contributor, no benchmark', () => {
    const text = holdingsBrief(DIGEST, 2)
    expect(text).toContain('NVDA led')
    expect(text).toContain('AAPL dragged')
    expect(text).not.toContain('SPY')
  })

  it('level 3 (default) adds the benchmark gap', () => {
    expect(holdingsBrief(DIGEST)).toContain("You're 1.9 pts ahead of SPY 1M.")
    expect(holdingsBrief(DIGEST)).not.toContain('Top movers')
  })

  it('level 4 adds the top movers and the period’s biggest driver', () => {
    const text = holdingsBrief(DIGEST, 4)
    expect(text).toContain('Top movers: NVDA +8.20%, AAPL -2.10%.')
    expect(text).toContain('The 1M biggest driver: NVDA')
  })
})
