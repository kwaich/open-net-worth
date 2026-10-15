// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { TopDailyMovers } from './TopDailyMovers'
import type { ComputedRow } from '../../types/portfolio'

afterEach(() => cleanup())

function makeRow(overrides: Partial<ComputedRow> = {}): ComputedRow {
  return {
    category: 'Brokerage',
    label: 'Apple Inc.',
    vesting_state: 'vested',
    liquidity: 'liquid',
    asset_class: 'equity',
    quantity: '10',
    price: '185.50',
    cost_basis: '150.00',
    currency: 'USD',
    ticker: 'AAPL',
    note: '',
    symbol: 'AAPL',
    _base: 2500,
    _pct: 5,
    _cost_base: 2000,
    _disp_ccy: 'USD',
    _disp_unit_price: 185.5,
    _disp_unit_cost: 150,
    _pl: 500,
    _plpct: 25,
    _daily_pl: 150,
    _daily_plpct: 6.38,
    ...overrides,
  }
}

describe('TopDailyMovers price display', () => {
  it('renders the price span with currency when price is present', () => {
    const { container } = render(<TopDailyMovers rows={[makeRow()]} />)
    const priceEl = container.querySelector('.top-movers-price')
    expect(priceEl).not.toBeNull()
    expect(priceEl!.textContent).toContain('185')
    expect(priceEl!.textContent).toContain('USD')
  })

  it('omits the price span when price is an empty string', () => {
    const { container } = render(<TopDailyMovers rows={[makeRow({ price: '' })]} />)
    expect(container.querySelector('.top-movers-price')).toBeNull()
  })

  it('omits the price span when price is zero', () => {
    const { container } = render(<TopDailyMovers rows={[makeRow({ price: '0' })]} />)
    expect(container.querySelector('.top-movers-price')).toBeNull()
  })

  it('renders the server-computed per-unit price, not the raw quote', () => {
    // A per-gram holding must read per GRAM, not per troy ounce. That scaling (by
    // `quote_per_unit`) now happens in backend/services/portfolio_rows.py and arrives as
    // `_disp_unit_price` — pinned server-side by
    // tests/test_portfolio_rows.py::test_display_unit_price_scales_by_quote_per_unit.
    // Here we pin only that the component renders THAT field rather than re-deriving it.
    const { container } = render(
      <TopDailyMovers
        rows={[
          makeRow({ price: '2000', currency: 'USD', quote_per_unit: 0.001, _disp_unit_price: 2 }),
        ]}
      />,
    )
    const priceEl = container.querySelector('.top-movers-price')
    expect(priceEl).not.toBeNull()
    expect(priceEl!.textContent).not.toContain('2000')
    expect(priceEl!.textContent).toContain('2')
    expect(priceEl!.textContent).toContain('USD')
  })
})

describe('TopDailyMovers — positions of one instrument collapse to a single mover', () => {
  it('aggregates same-instrument positions into one row summing the daily P&L', () => {
    // SHIB held across three wallets — same instrument, same %, different qty → different $.
    const rows = [
      makeRow({
        symbol: 'SHIB',
        ticker: 'SHIB',
        label: 'Shiba Inu',
        instrument_id: 'inst-shib',
        position_id: 'p1',
        _daily_pl: -1.14,
        _daily_plpct: -0.48,
      }),
      makeRow({
        symbol: 'SHIB',
        ticker: 'SHIB',
        label: 'Shiba Inu',
        instrument_id: 'inst-shib',
        position_id: 'p2',
        _daily_pl: -0.87,
        _daily_plpct: -0.48,
      }),
      makeRow({
        symbol: 'SHIB',
        ticker: 'SHIB',
        label: 'Shiba Inu',
        instrument_id: 'inst-shib',
        position_id: 'p3',
        _daily_pl: -0.14,
        _daily_plpct: -0.48,
      }),
    ]
    const { container } = render(<TopDailyMovers rows={rows} />)
    expect(container.querySelectorAll('.top-movers-row').length).toBe(1)
    const amount = container.querySelector('.top-movers-amount .money')
    expect(amount!.textContent).toContain('2.15') // 1.14 + 0.87 + 0.14
  })

  it('keeps distinct instruments as separate rows', () => {
    const rows = [
      makeRow({
        symbol: 'SHIB',
        ticker: 'SHIB',
        instrument_id: 'inst-shib',
        position_id: 'p1',
        _daily_pl: -1.0,
      }),
      makeRow({
        symbol: 'BTC',
        ticker: 'BTC',
        instrument_id: 'inst-btc',
        position_id: 'p2',
        _daily_pl: 5.0,
      }),
    ]
    const { container } = render(<TopDailyMovers rows={rows} />)
    expect(container.querySelectorAll('.top-movers-row').length).toBe(2)
  })
})

describe('TopDailyMovers — tapping a mover opens its asset detail', () => {
  it('calls onOpenAsset with the row when an instrument-backed mover is tapped', () => {
    const onOpenAsset = vi.fn()
    const { getByRole } = render(
      <TopDailyMovers rows={[makeRow({ instrument_id: 'inst-aapl' })]} onOpenAsset={onOpenAsset} />,
    )
    fireEvent.click(getByRole('button'))
    expect(onOpenAsset).toHaveBeenCalledTimes(1)
    expect(onOpenAsset.mock.calls[0]![0].instrument_id).toBe('inst-aapl')
  })

  it('is not clickable when the row has no instrument_id (e.g. a manual holding)', () => {
    const onOpenAsset = vi.fn()
    const { queryByRole } = render(
      <TopDailyMovers rows={[makeRow({ instrument_id: undefined })]} onOpenAsset={onOpenAsset} />,
    )
    expect(queryByRole('button')).toBeNull()
  })
})
