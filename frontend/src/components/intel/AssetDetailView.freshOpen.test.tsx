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

afterEach(cleanup)

const LOADED = {
  data: DETAIL,
  loading: false,
  refreshing: false,
  backgroundRefreshing: false,
  error: null,
  refresh: vi.fn(),
  refetch: vi.fn(),
  refreshNotice: null,
}

describe('AssetDetailView — a fresh open always starts on Overview', () => {
  it('does not remember the last tab across an unmount/remount of the same ticker', () => {
    // Owner-reproduced 2026-09-22: back to Investments, open the same ticker again → it
    // opened on Position because that was last open. The URL (?sub=) still wins for
    // refresh / back-forward / deep links; nothing else is stored — the same rule Settings
    // follows ("let's not cache or store that").
    mockUseAssetDetail.mockReturnValue(LOADED)
    const first = render(<AssetDetailView instrumentId="aapl" />)
    fireEvent.click(first.getByText('Position'))
    expect(first.getByText('No position in this asset')).toBeTruthy()
    first.unmount()
    expect(localStorage.getItem('asset-detail-tab')).toBeNull()
    const again = render(<AssetDetailView instrumentId="aapl" />)
    expect(again.queryByText('No position in this asset')).toBeNull()
    expect(again.getByText('Overview').closest('[role="tab"]')?.getAttribute('aria-selected')).toBe(
      'true',
    )
  })
})
