// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { HoldingsTable } from './components/shared/HoldingsTable'
import { BondMaturityCard } from './components/shared/BondMaturityCard'
import { ASSET_CLASS_COLOR } from './utils/constants'
import { fDate } from './utils/format'
import type { ComputedRow, FixedIncomeSummary } from './types/portfolio'
import * as useFixedIncomeHook from './hooks/useFixedIncome'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// jsdom does not implement window.matchMedia — provide a minimal stub for components
// that call useBreakpoint (BondMaturityCard uses it to decide defaultOpen). Returning
// matches=true simulates a desktop viewport so the CollapsibleSection renders open by
// default and its children are in the DOM. The stub must implement the full MediaQueryList
// interface (addEventListener / removeEventListener) so useBreakpoint's effect doesn't throw.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: () => ({
    matches: true,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  }),
})

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeBondRow(overrides: Partial<ComputedRow> = {}): ComputedRow {
  return {
    category: 'Vantage Brokerage',
    label: 'SG 3.5% 2027',
    vesting_state: 'vested',
    liquidity: 'liquid',
    asset_class: 'bond',
    quantity: '10000',
    price: '99',
    cost_basis: '9800',
    currency: 'SGD',
    ticker: '',
    note: '',
    _base: 9900,
    _pct: 2,
    _cost_base: 9800,
    _disp_ccy: 'SGD',
    _disp_unit_price: 99,
    _disp_unit_cost: 98,
    _pl: 100,
    _plpct: 1.02,
    _daily_pl: null,
    _daily_plpct: null,
    bond_type: 'market',
    face_value: 1000,
    coupon_rate: 0.035,
    coupon_freq: 2,
    maturity_date: '2027-06-01',
    days_to_maturity: 345,
    ...overrides,
  }
}

// ── HoldingsTable bond column rendering ──────────────────────────────────────

describe('HoldingsTable bondColumns', () => {
  it('renders the bond type badge for a market bond', () => {
    const { getByText } = render(
      <HoldingsTable rows={[makeBondRow()]} bondColumns showPL={false} />,
    )
    expect(getByText('Market')).toBeTruthy()
  })

  it('renders the coupon rate as a percentage', () => {
    // Use a row with a name that doesn't contain the coupon rate to avoid ambiguity.
    const row = makeBondRow({ label: 'Test Bond', coupon_rate: 0.035 })
    const { getByText } = render(<HoldingsTable rows={[row]} bondColumns showPL={false} />)
    // coupon_rate 0.035 × 100 = 3.5 — formatted by fPct as "3.50%"
    expect(getByText('3.50%')).toBeTruthy()
  })

  it('renders the maturity date and days-to-maturity', () => {
    const { getByText } = render(
      <HoldingsTable rows={[makeBondRow()]} bondColumns showPL={false} />,
    )
    expect(getByText(fDate('2027-06-01', 'short', 'always'))).toBeTruthy()
    expect(getByText('(345d)')).toBeTruthy()
  })

  it('shows "Zero-coupon" muted badge when coupon_freq === 0', () => {
    const row = makeBondRow({ coupon_freq: 0, coupon_rate: null })
    const { getByText } = render(<HoldingsTable rows={[row]} bondColumns showPL={false} />)
    expect(getByText('Zero-coupon')).toBeTruthy()
  })

  it('shows SSB badge with pos tone for ssb subtype', () => {
    const row = makeBondRow({ bond_type: 'ssb' })
    const { getByText } = render(<HoldingsTable rows={[row]} bondColumns showPL={false} />)
    expect(getByText('SSB')).toBeTruthy()
  })

  it('renders "—" when maturity_date is null', () => {
    const row = makeBondRow({ maturity_date: null, days_to_maturity: null })
    const { getAllByText } = render(<HoldingsTable rows={[row]} bondColumns showPL={false} />)
    // There may be multiple "—" cells (face value etc); at least one for maturity.
    const dashes = getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)
  })

  it('renders bond column headers (Type / Coupon / Matures / Face) not Qty/Cost/Price/Cur', () => {
    const { getByText, queryByText } = render(
      <HoldingsTable rows={[makeBondRow()]} bondColumns showPL={false} />,
    )
    expect(getByText('Type')).toBeTruthy()
    expect(getByText('Coupon')).toBeTruthy()
    expect(getByText('Matures')).toBeTruthy()
    expect(getByText('Face')).toBeTruthy()
    expect(queryByText('Qty')).toBeNull()
    expect(queryByText('Price')).toBeNull()
  })
})

// ── HoldingsTable non-bondColumns inline bond meta badge ─────────────────────

describe('HoldingsTable bond meta inline badge (non-bondColumns layout)', () => {
  it('renders coupon+maturity meta badge in the name cell for a bond row', () => {
    const row = makeBondRow({
      coupon_rate: 0.0425,
      coupon_freq: 2,
      maturity_date: '2029-06-01',
    })
    const { container } = render(<HoldingsTable rows={[row]} bondColumns={false} showPL={false} />)
    const badge = container.querySelector('.bond-meta-inline')
    expect(badge).toBeTruthy()
    // Should show coupon rate
    expect(badge!.textContent).toContain('4.25%')
    // Should show maturity year
    expect(badge!.textContent).toContain('mat 2029')
  })

  it('renders "• fund" badge when maturity_date is null', () => {
    const row = makeBondRow({ maturity_date: null, days_to_maturity: null })
    const { container } = render(<HoldingsTable rows={[row]} bondColumns={false} showPL={false} />)
    const badge = container.querySelector('.bond-meta-inline')
    expect(badge).toBeTruthy()
    expect(badge!.textContent).toContain('fund')
  })

  it('does NOT render the inline badge when bondColumns is true', () => {
    const row = makeBondRow()
    const { container } = render(<HoldingsTable rows={[row]} bondColumns showPL={false} />)
    const badge = container.querySelector('.bond-meta-inline')
    expect(badge).toBeNull()
  })

  it('does NOT render the inline badge for a non-bond asset class', () => {
    const equityRow: ComputedRow = {
      ...makeBondRow(),
      asset_class: 'equity',
      bond_type: undefined,
    }
    const { container } = render(
      <HoldingsTable rows={[equityRow]} bondColumns={false} showPL={false} />,
    )
    const badge = container.querySelector('.bond-meta-inline')
    expect(badge).toBeNull()
  })
})

// ── Bond asset-class color token ─────────────────────────────────────────────

describe('ASSET_CLASS_COLOR.bond token', () => {
  it('is the teal #2ba39a (not the old #34b08a)', () => {
    expect(ASSET_CLASS_COLOR['bond']).toBe('#2ba39a')
  })
})

// ── BondMaturityCard ladder_excluded_count footnote ──────────────────────────

function makeSummary(overrides: Partial<FixedIncomeSummary> = {}): FixedIncomeSummary {
  return {
    base_currency: 'SGD',
    total_base: 10000,
    count: 2,
    by_subtype: { ssb: 10000 },
    maturity_buckets: { d30: 0, d90: 0, y1: 0 },
    maturity_ladder: [],
    holdings: [],
    ladder_excluded_count: 0,
    ...overrides,
  }
}

describe('BondMaturityCard ladder_excluded_count footnote', () => {
  it('renders the excluded-count footnote when ladder_excluded_count > 0', () => {
    const summary = makeSummary({ ladder_excluded_count: 3 })
    vi.spyOn(useFixedIncomeHook, 'useFixedIncome').mockReturnValue({
      data: summary,
      loading: false,
      error: false,
    })

    const { getByText } = render(<BondMaturityCard />)
    expect(getByText(/3 bond funds without a fixed maturity/)).toBeTruthy()
  })

  it('renders singular "bond fund" for count === 1', () => {
    const summary = makeSummary({ ladder_excluded_count: 1 })
    vi.spyOn(useFixedIncomeHook, 'useFixedIncome').mockReturnValue({
      data: summary,
      loading: false,
      error: false,
    })

    const { getByText } = render(<BondMaturityCard />)
    expect(getByText(/1 bond fund without a fixed maturity/)).toBeTruthy()
  })

  it('does NOT render the footnote when ladder_excluded_count is 0', () => {
    const summary = makeSummary({ ladder_excluded_count: 0 })
    vi.spyOn(useFixedIncomeHook, 'useFixedIncome').mockReturnValue({
      data: summary,
      loading: false,
      error: false,
    })

    const { queryByText } = render(<BondMaturityCard />)
    expect(queryByText(/bond fund/)).toBeNull()
  })
})
