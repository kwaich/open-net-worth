import { describe, it, expect } from 'vitest'
import { fromApiAccount } from './data'
import { toBase } from '../../../utils/format'
import type { ApiAccount } from './types'

// Regression: securities accounts must surface their HOLDINGS market value (positions), not just the
// cash/transaction balance — otherwise net worth is understated. See the /api/portfolio valuation.
const api: ApiAccount = {
  id: 'a1',
  name: 'Crestmark',
  type: 'investment',
  currency: 'EUR',
  balance: 0,
  balance_base: 0,
  cleared_balance: 0,
  cleared_balance_base: 0,
} as unknown as ApiAccount

describe('fromApiAccount holdings', () => {
  it('stores holdingsBase and marks the account as securities when it has holdings', () => {
    const a = fromApiAccount(api, 100_000)
    expect(a.holdingsBase).toBe(100_000)
    expect(a.role).toBe('securities')
  })

  it('is a cash account (holdingsBase 0) when there are no positions', () => {
    const a = fromApiAccount(api, 0)
    expect(a.holdingsBase).toBe(0)
    expect(a.role).toBe('cash')
  })
})

describe('position base valuation (mirrors useAccountsLedger / usePortfolio.compute)', () => {
  const fx = { SGD: 1.0, EUR: 1.4727, USD: 1.2931 }
  it('values qty × price × quote_per_unit in the quote currency, converted to base', () => {
    // 1000.500000 ONWAI @ €35.81 → EUR → SGD
    const v = toBase(1000.5 * 35.81 * 1, 'EUR', fx, 'SGD')
    expect(v).toBeGreaterThan(50_000)
    expect(Math.round(v)).toBe(Math.round(1000.5 * 35.81 * 1.4727))
  })
})
