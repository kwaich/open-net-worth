// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssetDetailView } from './AssetDetailView'

vi.mock('../../hooks/useAssetDetail', () => ({
  useAssetDetail: () => ({
    data: {
      instrument_id: 'btc',
      asset: {
        name: 'Bitcoin',
        symbol: 'BTC',
        ticker: 'BTC',
        exchange: null,
        mic: null,
        country: null,
        asset_class: 'crypto',
        currency: 'USD',
        description: null,
      },
      position: null,
      price: {
        last: 63_463,
        change_abs: -456,
        change_pct: -0.71,
        currency: 'USD',
        as_of: '2026-08-13T12:00:00Z',
        provider: 'yahoo',
        state: 'live',
        market: null,
      },
      news: [],
      key_issues: [],
      facts: {
        ceo: null,
        employees: null,
        ipo_date: null,
        industry: null,
        sector: null,
        exchange: null,
        country: null,
        unverified: true,
      },
      crypto_facts: null,
      peers: [],
      watchlist: { following: false, price_alerts: [] },
      last_refreshed_at: '2026-08-13T12:00:00Z',
      has_ticker: false,
      portfolio_impact: null,
    },
    loading: false,
    refreshing: false,
    error: false,
    refresh: vi.fn(),
    refetch: vi.fn(),
    refreshNotice: null,
  }),
}))
vi.mock('../../hooks/useAnalystConsensus', () => ({
  useAnalystConsensus: () => ({ data: null, loading: false }),
}))
vi.mock('../../lucius/useScreenContract', () => ({
  useRegisterHandles: vi.fn(),
  useRegisterScreenPrompts: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useSearch: () => ({}),
}))
vi.mock('./AssetPriceChart', () => ({ AssetPriceChart: () => <div>Price chart</div> }))
vi.mock('./QuoteStats', () => ({ QuoteStats: () => null }))
vi.mock('./MarketStatusChip', () => ({ MarketStatusChip: () => null }))
vi.mock('./KeyFactsCard', () => ({ KeyFactsCard: () => null }))
vi.mock('./PeersCard', () => ({ PeersCard: () => null }))

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('AssetDetailView market attribution', () => {
  it('places healthy provider freshness in the existing facts disclaimer', () => {
    vi.setSystemTime(new Date('2026-08-13T12:00:41Z'))

    render(<AssetDetailView instrumentId="btc" />)

    const disclaimer = screen.getByText(/Company facts are AI-generated/)
    expect(disclaimer.textContent).toContain('Data via crypto search.')
    expect(disclaimer.textContent).toContain('Live via Yahoo Finance • Updated 41s ago.')
    expect(disclaimer.textContent).toContain(
      'Company facts are AI-generated and may be inaccurate.',
    )
  })
})
