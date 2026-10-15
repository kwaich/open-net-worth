// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssetDetailView } from './AssetDetailView'

function detailData(instrumentId: string, ticker: string) {
  return {
    instrument_id: instrumentId,
    asset: {
      name: ticker,
      symbol: ticker,
      ticker,
      exchange: null,
      mic: null,
      country: null,
      asset_class: 'equity',
      currency: 'USD',
      description: null,
    },
    position: null,
    price: {
      last: 100,
      change_abs: 1,
      change_pct: 1,
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
  }
}

let currentInstrumentId = 'aaa'

vi.mock('../../hooks/useAssetDetail', () => ({
  useAssetDetail: () => ({
    data: detailData(currentInstrumentId, currentInstrumentId.toUpperCase()),
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
  // No `sub` in the URL — matches opening a fresh ticker from a list, never a
  // peer-drill / bookmark that explicitly carries `sub` forward.
  useSearch: () => ({}),
}))
vi.mock('./AssetPriceChart', () => ({ AssetPriceChart: () => <div>Price chart</div> }))
vi.mock('./QuoteStats', () => ({ QuoteStats: () => null }))
vi.mock('./MarketStatusChip', () => ({ MarketStatusChip: () => null }))
vi.mock('./KeyFactsCard', () => ({ KeyFactsCard: () => null }))
vi.mock('./PeersCard', () => ({ PeersCard: () => null }))

afterEach(() => {
  cleanup()
})

describe('AssetDetailView — subtab resets per instrument', () => {
  it('opens a newly mounted, different instrument on Overview even after Analysis was last viewed', () => {
    currentInstrumentId = 'aaa'
    const { unmount } = render(<AssetDetailView instrumentId="aaa" />)

    fireEvent.click(screen.getByRole('tab', { name: 'Analysis' }))
    expect(screen.getByRole('tab', { name: 'Analysis' }).getAttribute('aria-selected')).toBe('true')

    unmount()
    currentInstrumentId = 'bbb'
    render(<AssetDetailView instrumentId="bbb" />)

    expect(screen.getByRole('tab', { name: 'Overview' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: 'Analysis' }).getAttribute('aria-selected')).toBe(
      'false',
    )
  })

  it('resets to Overview when the SAME mounted view is handed a different instrumentId', () => {
    currentInstrumentId = 'aaa'
    const { rerender } = render(<AssetDetailView instrumentId="aaa" />)

    fireEvent.click(screen.getByRole('tab', { name: 'Analysis' }))
    expect(screen.getByRole('tab', { name: 'Analysis' }).getAttribute('aria-selected')).toBe('true')

    currentInstrumentId = 'ccc'
    rerender(<AssetDetailView instrumentId="ccc" />)

    expect(screen.getByRole('tab', { name: 'Overview' }).getAttribute('aria-selected')).toBe('true')
  })
})
