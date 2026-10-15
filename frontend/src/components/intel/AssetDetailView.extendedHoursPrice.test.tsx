// @vitest-environment jsdom
//
// Regression coverage for the 2026-09-01 fix: `price.last` became the extended-hours price
// (fed into net worth/P&L too, not just display — see backend resolve_price_quote), which
// broke this component two ways until fixed here: (1) the price block showed the SAME number
// twice, once mislabeled "At close" when it was actually the extended print; (2) the chart's
// `prevClose` prop fed the (now-extended) `price.last` as its own reference line, instead of
// YESTERDAY's close (an intermediate fix swapped it to TODAY's regular close instead — also
// wrong, a dashed line captioned "Prev close" showing today's own close is simply mislabeled).
// Both are covered below.

import { act, cleanup, render, screen } from '@testing-library/react'
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

interface ChartProps {
  livePrice?: number | null
  prevClose?: number | null
  onLatestPoint?: (price: number, at: string) => void
}
let lastChartProps: ChartProps | null = null
vi.mock('./AssetPriceChart', () => ({
  AssetPriceChart: (props: ChartProps) => {
    lastChartProps = props
    return <div>Price chart</div>
  },
}))
vi.mock('./QuoteStats', () => ({ QuoteStats: () => null }))
vi.mock('./MarketStatusChip', () => ({ MarketStatusChip: () => null }))
vi.mock('./KeyFactsCard', () => ({ KeyFactsCard: () => null }))
vi.mock('./PeersCard', () => ({ PeersCard: () => null }))

function assetDetail(price: Record<string, unknown>): AssetDetail {
  return {
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
    price,
    news: [],
    key_issues: [],
    facts: null,
    crypto_facts: null,
    peers: [],
    watchlist: { following: false, price_alerts: [] },
    last_refreshed_at: '2026-09-01T21:00:00Z',
    has_ticker: true,
    portfolio_impact: null,
  } as unknown as AssetDetail
}

function mockHook(price: Record<string, unknown>) {
  mockUseAssetDetail.mockReturnValue({
    data: assetDetail(price),
    loading: false,
    refreshing: false,
    backgroundRefreshing: false,
    error: false,
    refresh: vi.fn(),
    refetch: vi.fn(),
    refreshNotice: null,
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
  lastChartProps = null
})

describe('AssetDetailView — extended-hours price block (2026-09-01 fix)', () => {
  it('shows two DISTINCT numbers — the live extended price and the regular close — never the same value twice', () => {
    mockHook({
      last: 202.0,
      change_abs: 4.0,
      change_pct: 2.02,
      currency: 'USD',
      as_of: '2026-09-01T21:00:00Z',
      market: { is_open: false, session_bounds: null },
      is_extended: true,
      extended_session: 'post',
      at_close: {
        price: 200.0,
        change_abs: 2.0,
        change_pct: 1.01,
        as_of: '2026-09-01T20:00:00Z',
      },
    })

    render(<AssetDetailView instrumentId="aapl" />)

    // The two numbers are genuinely different — not the same value rendered twice.
    expect(screen.getByText('$202.00')).toBeTruthy()
    expect(screen.getByText('$200.00')).toBeTruthy()

    // The live number is captioned by session, never "At close".
    expect(screen.getByText(/After hours:/)).toBeTruthy()
    // The regular close is captioned "At close", and is a DIFFERENT DOM node.
    expect(screen.getByText(/At close/)).toBeTruthy()
  })

  it("feeds the chart YESTERDAY's close as prevClose — never the extended `last`, and never today's regular close either", () => {
    mockHook({
      last: 202.0,
      change_abs: 4.0,
      change_pct: 2.02,
      currency: 'USD',
      as_of: '2026-09-01T21:00:00Z',
      market: { is_open: false, session_bounds: null },
      is_extended: true,
      extended_session: 'post',
      at_close: {
        price: 200.0,
        change_abs: 2.0,
        change_pct: 1.01,
        as_of: '2026-09-01T20:00:00Z',
      },
    })

    render(<AssetDetailView instrumentId="aapl" />)

    expect(lastChartProps?.livePrice).toBe(202.0)
    // Two bugs this regression guards: prevClose must never be the extended `last` (202,
    // feeding a number as its own reference collapses the chart's up/down comparator to
    // zero) — and must never be today's regular close either (200, at_close.price — a
    // dashed line captioned "Prev close" showing TODAY's own close is simply mislabeled;
    // Perplexity's own chart shows yesterday's close there regardless of session state,
    // owner-reproduced 2026-09-01). The real answer, recovered from what the backend
    // already sends: at_close.price - at_close.change_abs = 200 - 2 = 198.
    expect(lastChartProps?.prevClose).toBe(198.0)
  })

  it('feeds the chart the MOST RECENT regular close as prevClose during PRE — at_close itself, not a further subtraction (owner-reproduced 2026-09-01: PRE showed the close from two sessions back)', () => {
    mockHook({
      last: 502.12,
      change_abs: -5.17,
      change_pct: -1.02,
      currency: 'USD',
      as_of: '2026-09-01T08:51:17Z',
      market: { is_open: true, session_bounds: null },
      is_extended: true,
      extended_session: 'pre',
      at_close: {
        // The most recently completed regular session's own close — already "yesterday's
        // close" relative to the upcoming session this PRE print is heading into.
        price: 507.29,
        change_abs: -6.24,
        change_pct: -1.22,
        as_of: '2026-08-31T20:00:01Z',
      },
    })

    render(<AssetDetailView instrumentId="aapl" />)

    expect(lastChartProps?.livePrice).toBe(502.12)
    // The POST formula (at_close.price - at_close.change_abs = 507.29 - (-6.24) = 513.53)
    // would walk back an EXTRA day here — at_close during PRE already IS the close to show.
    expect(lastChartProps?.prevClose).toBe(507.29)
  })

  it('shows only ONE number outside an active pre/post session — unchanged from before', () => {
    mockHook({
      last: 200.0,
      change_abs: -1.0,
      change_pct: -0.5,
      currency: 'USD',
      as_of: '2026-09-01T15:00:00Z',
      market: { is_open: true, session_bounds: null },
      is_extended: false,
      extended_session: null,
      at_close: null,
    })

    render(<AssetDetailView instrumentId="aapl" />)

    expect(screen.getByText('$200.00')).toBeTruthy()
    expect(screen.queryByText(/At close/)).toBeNull()
    expect(screen.getByText(/As of/)).toBeTruthy()
    // last - change_abs = 200 - (-1) = 201, same pre-existing (non-extended) formula.
    expect(lastChartProps?.prevClose).toBe(201.0)
  })

  it("shows the LAST session's after-hours print in a second column while the market is closed (weekend), like Perplexity", () => {
    mockHook({
      last: 147.61,
      change_abs: -2.98,
      change_pct: -1.98,
      currency: 'USD',
      as_of: '2026-09-18T20:04:48Z',
      market: {
        is_open: false,
        state: 'CLOSED',
        timezone: 'America/New_York',
        session_bounds: null,
      },
      is_extended: false,
      extended_session: null,
      at_close: null,
      last_after_hours: {
        price: 147.55,
        change_abs: -0.06,
        change_pct: -0.0406,
        as_of: '2026-09-18T23:59:55Z',
        session_date: '2026-09-18',
      },
    })

    render(<AssetDetailView instrumentId="orcl" />)

    expect(screen.getByText('$147.61')).toBeTruthy() // the close leads…
    expect(screen.getByText('$147.55')).toBeTruthy() // …Friday's after-hours beside it
    expect(screen.getByText(/After hours: 7:59/)).toBeTruthy() // exchange-local time
    expect(screen.getByText(/At close: /)).toBeTruthy() // Perplexity's caption, not "As of"
    expect(screen.queryByText(/As of/)).toBeNull()
    // The chart's prevClose is untouched by the second column: 147.61 - (-2.98) = 150.59.
    expect(lastChartProps?.prevClose).toBeCloseTo(150.59, 2)
  })
})

describe('AssetDetailView — header/chart price reconciliation (2026-09-01: "whatever\'s newer wins")', () => {
  // The header's own live quote and the chart's intraday bars are two independent Yahoo
  // feeds (owner-reproduced: the header briefly showed $501.87 while the chart's last real
  // bar was $502.25, from an OLDER upstream tick) — AssetPriceChart reports its own trailing
  // point back via onLatestPoint, and the header shows whichever of the two is newer.

  it("prefers the chart's own intraday point once it's NEWER than the header's live quote", () => {
    mockHook({
      last: 501.87,
      change_abs: -5.42,
      change_pct: -1.07,
      currency: 'USD',
      as_of: '2026-09-01T09:03:15Z',
      market: { is_open: true, session_bounds: null },
      is_extended: true,
      extended_session: 'pre',
      at_close: {
        price: 507.29,
        change_abs: -6.24,
        change_pct: -1.22,
        as_of: '2026-08-31T20:00:01Z',
      },
    })

    render(<AssetDetailView instrumentId="aapl" />)
    expect(screen.getByText('$501.87')).toBeTruthy()

    act(() => {
      lastChartProps?.onLatestPoint?.(502.25, '2026-09-01T09:04:00Z') // newer than as_of
    })

    // Baseline recovered from the header's OWN numbers (501.87 - (-5.42) = 507.29) — the
    // fresher price recomputed against that same baseline: 502.25 - 507.29 = -5.04. Price
    // and change stay mutually consistent even though the price came from a different feed.
    expect(screen.getByText('$502.25')).toBeTruthy()
    expect(screen.getByText('-5.04')).toBeTruthy()
    expect(screen.queryByText('$501.87')).toBeNull()
  })

  it("keeps the header's own live quote when the chart's point is OLDER — never regresses to a stale chart tick", () => {
    mockHook({
      last: 501.5,
      change_abs: -6.03,
      change_pct: -1.19,
      currency: 'USD',
      as_of: '2026-09-01T09:05:39Z',
      market: { is_open: true, session_bounds: null },
      is_extended: true,
      extended_session: 'pre',
      at_close: {
        price: 507.29,
        change_abs: -6.24,
        change_pct: -1.22,
        as_of: '2026-08-31T20:00:01Z',
      },
    })

    render(<AssetDetailView instrumentId="aapl" />)

    act(() => {
      lastChartProps?.onLatestPoint?.(502.25, '2026-09-01T09:02:00Z') // older than as_of
    })

    expect(screen.getByText('$501.50')).toBeTruthy()
    expect(screen.queryByText('$502.25')).toBeNull()
  })
})
