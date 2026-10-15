// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssetDetailView } from './AssetDetailView'
import type { AssetDetail } from '../../types/assetDetail'

vi.mock('../../hooks/useAssetDetail', () => ({ useAssetDetail: vi.fn() }))
import { useAssetDetail } from '../../hooks/useAssetDetail'

const mockUseAssetDetail = useAssetDetail as ReturnType<typeof vi.fn>

vi.mock('../../hooks/useAnalystConsensus', () => ({
  useAnalystConsensus: () => ({ data: null, loading: false }),
}))
vi.mock('../../lucius/useScreenContract', () => ({
  useRegisterHandles: vi.fn(),
  useRegisterScreenPrompts: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  // A deep link / peer-drill / bookmark that carries ?sub= forward — the only path
  // that calls parseDetailTab with a real string instead of short-circuiting on undefined.
  useSearch: () => ({ sub: 'analysis' }),
}))
vi.mock('./AssetPriceChart', () => ({ AssetPriceChart: () => <div>Price chart</div> }))
vi.mock('./QuoteStats', () => ({ QuoteStats: () => null }))
vi.mock('./MarketStatusChip', () => ({ MarketStatusChip: () => null }))
vi.mock('./KeyFactsCard', () => ({ KeyFactsCard: () => null }))
vi.mock('./PeersCard', () => ({ PeersCard: () => null }))

const DETAIL = {
  instrument_id: 'aapl',
  asset: {
    name: 'Apple Inc.',
    symbol: 'AAPL',
    ticker: 'AAPL',
    exchange: 'NASDAQ',
    mic: null,
    country: 'US',
    asset_class: 'equity',
    currency: 'USD',
    description: null,
  },
  position: null,
  price: { last: 100, currency: 'USD' },
  news: [],
  key_issues: [],
  facts: null,
  crypto_facts: null,
  peers: [],
  watchlist: { following: false, price_alerts: [] },
  last_refreshed_at: null,
  has_ticker: true,
  portfolio_impact: null,
} as unknown as AssetDetail

afterEach(cleanup)

describe('AssetDetailView — deep link with ?sub= in the URL', () => {
  it('opens directly on the URL-specified tab instead of crashing', () => {
    mockUseAssetDetail.mockReturnValue({
      data: DETAIL,
      loading: false,
      refreshing: false,
      backgroundRefreshing: false,
      error: null,
      refresh: vi.fn(),
      refetch: vi.fn(),
      refreshNotice: null,
    })
    render(<AssetDetailView instrumentId="aapl" />)
    expect(
      screen.getByText('Analysis').closest('[role="tab"]')?.getAttribute('aria-selected'),
    ).toBe('true')
  })
})
