import { describe, expect, it } from 'vitest'
import type { Grant, VestHistoryItem } from '../../../types/equity'
import type { ComputedRow, IncomeEvent } from '../../../types/portfolio'
import { buildPriceByInstrument, buildSinceGrantLine, buildVestHistory } from './useEquityView'
import { buildIncomeHistory, deriveIncomeView } from './useIncomeView'

function income(overrides: Partial<IncomeEvent>): IncomeEvent {
  return {
    id: 'income_1',
    position_id: 'position_1',
    instrument_id: 'instrument_1',
    account_id: 'account_1',
    paid_at: '2026-08-01',
    income_kind: 'dividend',
    gross_amount: 100,
    tax_withheld: 10,
    currency: 'SGD',
    is_drp: false,
    txn_id: 'transaction_1',
    base_amount: 90,
    ...overrides,
  }
}

function vest(overrides: Partial<VestHistoryItem>): VestHistoryItem {
  return {
    vest_event_id: 'vest_1',
    grant_id: 'grant_1',
    tranche_id: 'tranche_1',
    symbol: 'ONWAI',
    instrument_id: 'instrument_1',
    vest_date: '2026-08-01',
    units: 10,
    fmv_per_unit: 20,
    fmv_currency: 'EUR',
    release_status: 'released',
    released_on: '2026-08-01',
    expected_release_date: null,
    overdue: false,
    indicative_value_base: null,
    base_currency: 'SGD',
    ...overrides,
  }
}

function grant(overrides: Partial<Grant>): Grant {
  return {
    id: 'grant_1',
    type: 'rsu',
    instrument_id: 'instrument_1',
    symbol: 'ONWAI',
    grant_date: '2024-01-01',
    total_units: 100,
    vested_units: 40,
    unvested_units: 60,
    next_vest_date: '2026-09-01',
    currency: 'EUR',
    grant_price_per_unit: 20.36,
    tax_event: 'at_vest',
    status: 'active',
    release_required: false,
    pending_release_units: 0,
    locked_position_id: null,
    pending_position_id: null,
    account_id: null,
    tranches: [],
    note: '',
    created_at: '2024-01-01',
    updated_at: '2024-01-01',
    ...overrides,
  }
}

function computedRow(overrides: Partial<ComputedRow>): ComputedRow {
  return {
    category: 'investment',
    label: 'ONWAI Inc',
    vesting_state: 'vested',
    liquidity: 'liquid',
    asset_class: 'equity',
    quantity: '10',
    price: '23.10',
    cost_basis: '0',
    currency: 'EUR',
    ticker: 'ONWAI',
    note: '',
    instrument_id: 'instrument_1',
    quote_currency: 'EUR',
    _base: 0,
    _pct: 0,
    _cost_base: null,
    _disp_ccy: 'EUR',
    _disp_unit_price: 23.1,
    _disp_unit_cost: null,
    _pl: null,
    _plpct: null,
    _daily_pl: null,
    _daily_plpct: null,
    ...overrides,
  }
}

describe('investment event history view models', () => {
  it('orders received income newest-first and preserves all supported kinds and delivery', () => {
    const rows = buildIncomeHistory([
      income({ id: 'dividend', paid_at: '2026-08-01', instrument_ticker: 'ONWAI' }),
      income({
        id: 'interest',
        paid_at: '2026-08-03',
        income_kind: 'interest',
        instrument_label: 'Savings bond',
      }),
      income({
        id: 'distribution',
        paid_at: '2026-08-02',
        income_kind: 'distribution',
        is_drp: true,
        instrument_ticker: 'VWRA',
      }),
    ])

    expect(rows.map((row) => row.eventId)).toEqual(['interest', 'distribution', 'dividend'])
    expect(rows.map((row) => row.kindLabel)).toEqual(['Interest', 'Distribution', 'Dividend'])
    expect(rows.at(1)?.deliveryLabel).toBe('Reinvested')
    expect(rows.at(0)?.symbol).toBe('Savings bond')
  })

  it('orders vest facts newest-first and exposes release actions only while awaiting release', () => {
    const pending = vest({
      vest_event_id: 'pending',
      vest_date: '2026-08-03',
      release_status: 'awaiting_release',
      released_on: null,
    })
    const released = vest({ vest_event_id: 'released', vest_date: '2026-08-01' })

    const rows = buildVestHistory([released, pending])

    expect(rows.map((row) => row.vestEventId)).toEqual(['pending', 'released'])
    expect(rows.at(0)?.releaseItem?.vest_event_id).toBe('pending')
    expect(rows.at(1)?.releaseItem).toBeNull()
  })

  it('carries the price at vest through, and treats a legacy event missing it as null rather than crashing', () => {
    const priced = vest({ vest_event_id: 'priced', fmv_per_unit: 20.36, fmv_currency: 'EUR' })
    const legacy = vest({
      vest_event_id: 'legacy',
      // The API type declares these non-null, but a pre-fmv-field vest event can still
      // send null over the wire — buildVestHistory must not trust the declared type.
      fmv_per_unit: null as unknown as number,
      fmv_currency: null as unknown as string,
    })

    const rows = buildVestHistory([priced, legacy])

    expect(rows.find((r) => r.vestEventId === 'priced')).toMatchObject({
      fmvPerUnit: 20.36,
      fmvCurrency: 'EUR',
    })
    expect(rows.find((r) => r.vestEventId === 'legacy')).toMatchObject({
      fmvPerUnit: null,
      fmvCurrency: null,
    })
  })

  it('keeps a legacy vest without a release fact actionable', () => {
    const row = buildVestHistory([
      vest({
        release_status: 'not_released',
        released_on: null,
        release_mode: 'record_only',
      }),
    ])[0]

    expect(row?.releaseStatus).toBe('not_released')
    expect(row?.releaseItem?.release_mode).toBe('record_only')
  })

  it('suppresses base totals, growth, and yield when an income conversion is unavailable', () => {
    const view = deriveIncomeView(
      {
        base_currency: 'SGD',
        events: [
          income({
            currency: 'USD',
            base_amount: null,
            base_amount_available: false,
            native_net_amount: 10,
            native_currency: 'USD',
          }),
        ],
        total_base: 10,
        gross_base: 11,
        tax_withheld_base: 1,
        trailing_12m_base: 10,
        cost_basis_base: 100,
        yield_on_cost_pct: 10,
        by_instrument: { instrument_1: 10 },
        by_kind: { dividend: 10 },
        base_totals_available: false,
        yield_on_cost_available: false,
        missing_fx_currencies: ['USD'],
      },
      null,
      null,
    )

    expect(view.cards.slice(0, 3).map((card) => card.value)).toEqual([
      'FX unavailable',
      'FX unavailable',
      'FX unavailable',
    ])
    expect(view.growth).toBeNull()
  })
})

describe('"since grant" reference line for unvested units', () => {
  it('shows the dynamic comparison when unvested units exist, a live price is available, and currencies match', () => {
    const priceByInstrument = buildPriceByInstrument([
      computedRow({ price: '23.10', quote_currency: 'EUR' }),
    ])
    const line = buildSinceGrantLine(grant({ unvested_units: 60 }), priceByInstrument)

    expect(line).toBe('Granted at EUR 20.36 • now EUR 23.10 • +13.5% since grant (unvested units)')
  })

  it('falls back to the static line when the grant is fully vested (no unvested units)', () => {
    const priceByInstrument = buildPriceByInstrument([
      computedRow({ price: '23.10', quote_currency: 'EUR' }),
    ])
    const line = buildSinceGrantLine(grant({ unvested_units: 0 }), priceByInstrument)

    expect(line).toBe('Granted at EUR 20.36')
  })

  it('falls back to the static line rather than cross-currency compare when the quote currency differs', () => {
    const priceByInstrument = buildPriceByInstrument([
      computedRow({ price: '31.00', quote_currency: 'USD', currency: 'USD' }),
    ])
    const line = buildSinceGrantLine(
      grant({ unvested_units: 60, currency: 'EUR' }),
      priceByInstrument,
    )

    expect(line).toBe('Granted at EUR 20.36')
  })

  it('falls back to the static line when no live price is available for the instrument', () => {
    const line = buildSinceGrantLine(grant({ unvested_units: 60 }), {})

    expect(line).toBe('Granted at EUR 20.36')
  })

  it('returns null when the grant has no recorded grant price at all', () => {
    const priceByInstrument = buildPriceByInstrument([
      computedRow({ price: '23.10', quote_currency: 'EUR' }),
    ])
    const line = buildSinceGrantLine(
      grant({ unvested_units: 60, grant_price_per_unit: null }),
      priceByInstrument,
    )

    expect(line).toBeNull()
  })

  it('buildPriceByInstrument skips rows with no instrument_id or a non-positive/unparseable price', () => {
    const map = buildPriceByInstrument([
      computedRow({ instrument_id: undefined }),
      computedRow({ instrument_id: 'instrument_2', price: '0' }),
      computedRow({ instrument_id: 'instrument_3', price: 'n/a' }),
    ])

    expect(map).toEqual({})
  })
})
