// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { fDate } from '../../utils/format'
import { YourPositionCard } from './YourPositionCard'
import type { AssetPosition } from '../../types/assetDetail'

const mockNavigateFn = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigateFn,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  localStorage.clear()
})

const POSITION: AssetPosition = {
  held: true,
  position_id: 'pos-1',
  qty: 1000,
  cost_basis: 50,
  cost_currency: 'USD',
  market_value_base: 23400,
  market_value_native: 10000,
  weight_pct: 12.5,
  unrealized_pl_base: 6090,
  unrealized_pl_pct: 35.2,
  lots: [],
  realized: [],
  income: [],
  per_account: [
    {
      account_id: 'acc-1',
      account_name: 'Vantage',
      quantity: 1000,
      market_value_base: 23400,
      cost_basis: 50,
      cost_currency: 'USD',
      unrealized_pl_base: 6090,
    },
  ],
  unvested: null,
}

describe('YourPositionCard', () => {
  it('renders the base quantity/cost/value/weight/P&L rows without an Unvested section', () => {
    const { getByText, queryByText } = render(
      <YourPositionCard position={POSITION} income={null} />,
    )
    expect(getByText('Quantity')).toBeTruthy()
    expect(getByText('Market value')).toBeTruthy()
    expect(queryByText('Unvested')).toBeNull()
    expect(queryByText(/View grant/)).toBeNull()
  })

  it('renders an Unvested section with units, value, next vest, and a grant link when present', () => {
    const withUnvested: AssetPosition = {
      ...POSITION,
      unvested: {
        units: 1500.0,
        market_value_base: 14662.5,
        grant_id: 'grant-1',
        next_vest: { date: '2027-01-01', units: 700.0 },
        tranches: [],
      },
    }
    const { getByText } = render(<YourPositionCard position={withUnvested} income={null} />)
    expect(getByText('Unvested')).toBeTruthy()
    expect(getByText(/1,500 units/)).toBeTruthy()
    expect(
      getByText(new RegExp(`Next vest ${fDate('2027-01-01', 'short', 'always')}`)),
    ).toBeTruthy()
    expect(getByText(/View grant in Equity Comp/)).toBeTruthy()
  })

  it('omits the next-vest line when there is no upcoming tranche', () => {
    const withUnvested: AssetPosition = {
      ...POSITION,
      unvested: {
        units: 100,
        market_value_base: 1000,
        grant_id: 'grant-1',
        next_vest: null,
        tranches: [],
      },
    }
    const { queryByText } = render(<YourPositionCard position={withUnvested} income={null} />)
    expect(queryByText(/Next vest/)).toBeNull()
  })

  it('clicking the grant link navigates to /investments with ?sub=equity', () => {
    const withUnvested: AssetPosition = {
      ...POSITION,
      unvested: {
        units: 100,
        market_value_base: 1000,
        grant_id: 'grant-1',
        next_vest: null,
        tranches: [],
      },
    }
    const { getByText } = render(<YourPositionCard position={withUnvested} income={null} />)
    fireEvent.click(getByText(/View grant in Equity Comp/))
    expect(mockNavigateFn).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/investments', search: { sub: 'equity' } }),
    )
  })

  it('shows an em-dash for Dividends received while income is null (loading/unavailable)', () => {
    const { getByText } = render(<YourPositionCard position={POSITION} income={null} />)
    expect(getByText('Dividends received')).toBeTruthy()
  })

  it('renders the trailing-12m dividend figure once income is loaded', () => {
    const income = {
      base_currency: 'SGD',
      events: [],
      total_base: 4200,
      gross_base: 4600,
      tax_withheld_base: 400,
      trailing_12m_base: 980,
      cost_basis_base: 58720,
      yield_on_cost_pct: 1.67,
      by_instrument: {},
      by_kind: {},
    }
    const { getByText } = render(<YourPositionCard position={POSITION} income={income} />)
    expect(getByText('S$980.00')).toBeTruthy()
  })
})
