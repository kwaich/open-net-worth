// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
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
  useSearch: () => ({}),
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

afterEach(() => {
  cleanup()
})

describe('AssetDetailView — never-held Position empty state', () => {
  it('offers Add lot on the never-held empty state', async () => {
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
    const { getByText, findByText, findAllByDisplayValue } = render(
      <AssetDetailView instrumentId="aapl" />,
    )
    fireEvent.click(getByText('Position'))
    expect(getByText('No position in this asset')).toBeTruthy()
    expect(getByText(/Record your first acquisition/)).toBeTruthy()
    fireEvent.click(getByText('Add lot'))
    expect(await findByText('Symbol')).toBeTruthy()
    expect((await findAllByDisplayValue('AAPL')).length).toBeGreaterThan(0)
  })
})
