// Regression test: asset_sale funding sources used to be valued LIVE — sell_quantity × the
// position's current market price, recomputed every render. Changed (owner decision,
// 2026-07-26): the funding plan must not silently drift with the market unless the user
// intentionally edits it, so asset_sale now stores its own frozen amount+currency (the target
// the user actually intends to raise) and is valued IDENTICALLY to a fixed source (cash/
// income/loan/windfall) — sell_quantity is informational only, never read for valuation.
import { describe, it, expect } from 'vitest'
import { resolveFundingValueBase } from './fundingValue'
import type { FundingSource } from '../../../types/commitments'

function source(overrides: Partial<FundingSource>): FundingSource {
  return {
    id: 'fs1',
    type: 'cash',
    label: 'Source',
    amount: null,
    currency: '',
    ref_position_id: null,
    sell_quantity: null,
    available_date: null,
    ref_account_id: null,
    repayment_commitment_id: null,
    note: '',
    disabled: false,
    cadence: null,
    status: 'planned',
    realized_txn_id: null,
    ...overrides,
  }
}

describe('resolveFundingValueBase — asset_sale uses its own stored amount, never a live price', () => {
  it('converts the stored native amount+currency, exactly like a fixed source', () => {
    // Base currency defaults to SGD; EUR→SGD at rate 1.4666.
    const f = source({
      type: 'asset_sale',
      ref_position_id: 'pos-1',
      sell_quantity: 340,
      amount: 10000,
      currency: 'EUR',
    })
    expect(resolveFundingValueBase(f, 'VND', { EUR: 1.4666, SGD: 1 })).toBeCloseTo(
      10000 * 1.4666,
      2,
    )
  })

  it('returns 0 for an asset_sale source with no amount set yet — never fabricates a value from sell_quantity', () => {
    const f = source({
      type: 'asset_sale',
      ref_position_id: 'pos-1',
      sell_quantity: 340,
      amount: null,
      currency: 'EUR',
    })
    expect(resolveFundingValueBase(f, 'VND', { EUR: 1.4666, VND: 1 })).toBe(0)
  })

  it('trusts a pre-hydrated value_base over everything else (server/preview-supplied)', () => {
    const f = source({ type: 'asset_sale', amount: 100, currency: 'EUR', value_base: 999 })
    expect(resolveFundingValueBase(f, 'VND', { EUR: 1, VND: 1 })).toBe(999)
  })

  it('still converts a fixed source (loan) via FX, unaffected by this change', () => {
    const f = source({ type: 'loan', amount: 50000, currency: 'SGD' })
    expect(resolveFundingValueBase(f, 'VND', { SGD: 1, VND: 1 })).toBe(50000)
  })
})
