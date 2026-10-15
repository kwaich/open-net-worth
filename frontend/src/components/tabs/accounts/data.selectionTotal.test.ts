// data.selectionTotal.test.ts — the bulk-bar selection sum sums BY LEG.
// One shared currency → EXACT native sum (a completed row's figure never drifts with
// the market); mixed currencies → ≈ base (trusted historical stamps where present,
// the current cached rate otherwise). Regression for: transfers summing as 0 (no base
// stamp), then foreign legs drifting with the FX cache even when every selected row
// lived in one currency (owner, 2026-07-16).

import { describe, it, expect } from 'vitest'
import { selectionTotal } from './data'
import type { Txn, FxRates } from './types'

const fx: FxRates = { SGD: 1, EUR: 1.5 }
const row = (over: Partial<Txn>): Txn => ({ baseAmount: 0, baseCurrency: 'SGD', ...over }) as Txn

describe('selectionTotal', () => {
  it('base-currency account: exact native sum — expense − and transfer-out leg −', () => {
    const rows = [
      row({
        kind: 'expense',
        fromAccountId: 'sgd',
        fromAmount: 20,
        fromCurrency: 'SGD',
        baseAmount: 20,
      }),
      row({
        kind: 'transfer',
        fromAccountId: 'sgd',
        fromAmount: 1000,
        fromCurrency: 'SGD',
        toAccountId: 'eur',
        toAmount: 660,
        toCurrency: 'EUR',
      }),
    ]
    expect(selectionTotal(rows, 'sgd', 'SGD', 'SGD', fx)).toEqual({
      amount: -1020,
      currency: 'SGD',
      approx: false,
    })
  })

  it('foreign wallet, single currency: exact native sum — no FX touched, never drifts', () => {
    const rows = [
      row({
        kind: 'transfer',
        fromAccountId: 'sgd',
        fromAmount: 1500,
        fromCurrency: 'SGD',
        toAccountId: 'eur',
        toAmount: 1000,
        toCurrency: 'EUR',
      }),
      row({
        kind: 'expense',
        fromAccountId: 'eur',
        fromAmount: 20,
        fromCurrency: 'EUR',
        baseAmount: 29.2,
      }),
      row({
        kind: 'expense',
        fromAccountId: 'eur',
        fromAmount: 30,
        fromCurrency: 'EUR',
        baseAmount: 43.8,
      }),
    ]
    expect(selectionTotal(rows, 'eur', 'EUR', 'SGD', fx)).toEqual({
      amount: 950,
      currency: 'EUR',
      approx: false,
    })
  })

  it('all-accounts view: transfers net zero (skipped); mixed currencies → ≈ base', () => {
    const rows = [
      row({
        kind: 'expense',
        fromAccountId: 'sgd',
        fromAmount: 10,
        fromCurrency: 'SGD',
        baseAmount: 10,
      }),
      // unstamped foreign expense (baseAmount 0) → converted at the current rate
      row({
        kind: 'expense',
        fromAccountId: 'eur',
        fromAmount: 100,
        fromCurrency: 'EUR',
        baseAmount: 0,
      }),
      row({
        kind: 'transfer',
        fromAccountId: 'sgd',
        fromAmount: 50,
        fromCurrency: 'SGD',
        toAccountId: 'sgd2',
        toAmount: 50,
        toCurrency: 'SGD',
      }),
    ]
    const t = selectionTotal(rows, null, null, 'SGD', fx)
    expect(t.currency).toBeNull()
    expect(t.approx).toBe(true)
    expect(t.amount).toBeCloseTo(-160, 6)
  })

  it('mixed: a stamp whose base_currency is STALE is not trusted — recomputed at the current rate', () => {
    const rows = [
      row({
        kind: 'expense',
        fromAccountId: 'eur',
        fromAmount: 100,
        fromCurrency: 'EUR',
        baseAmount: 80,
        baseCurrency: 'USD',
      }),
      row({ kind: 'income', toAccountId: 'sgd', toAmount: 5, toCurrency: 'SGD', baseAmount: 5 }),
    ]
    const t = selectionTotal(rows, null, null, 'SGD', fx)
    expect(t.amount).toBeCloseTo(-145, 6)
  })

  it('investment SELL (to-leg only) counts positive; empty selection is a plain zero', () => {
    const sell = row({
      kind: 'investment',
      toAccountId: 'sgd',
      toAmount: 200,
      toCurrency: 'SGD',
      baseAmount: 200,
    })
    expect(selectionTotal([sell], 'sgd', 'SGD', 'SGD', fx)).toEqual({
      amount: 200,
      currency: 'SGD',
      approx: false,
    })
    expect(selectionTotal([], 'sgd', 'SGD', 'SGD', fx)).toEqual({
      amount: 0,
      currency: null,
      approx: false,
    })
  })
})
